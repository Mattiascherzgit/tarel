import hashlib
import json
import os
import sys
from contextlib import redirect_stdout
from dataclasses import replace
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogResult,
)
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.contracts import AnnotationEvidence, GraphAnnotation
from tarel.retrieval.bm25 import rank_bm25
from tarel.retrieval.contracts import RetrievalFailure
from tarel.retrieval.documents import build_retrieval_documents
from tarel.retrieval.index import FileRetrievalIndex, search_retrieval
from tarel.retrieval.local import (
    MODEL_SPECS,
    LlamaCppEmbedding,
    ModelSpec,
    download_model,
)
from tarel.sdk import Tarel


class RetrievalTests(TestCase):
    def test_documents_use_an_allowlist_and_never_copy_samples_or_evidence(self) -> None:
        graph = _retrieval_graph()
        fact = next(node for node in graph.nodes if node.label == "dbo.FactInternetSales")
        unsafe = replace(
            graph,
            nodes=tuple(
                replace(
                    node,
                    metadata={
                        **node.metadata,
                        "connection_url": "SECRET_CONNECTION",
                        "sample_rows": [{"CustomerName": "SECRET_SAMPLE"}],
                    },
                    annotation=GraphAnnotation(
                        description="Internet sales facts.",
                        evidence=(
                            AnnotationEvidence(
                                source="sample",
                                reference="row",
                                value="SECRET_EVIDENCE",
                            ),
                        ),
                    ),
                )
                if node.id == fact.id
                else node
                for node in graph.nodes
            ),
        )

        text = "\n".join(document.text for document in build_retrieval_documents(unsafe))

        self.assertIn("Internet sales facts", text)
        self.assertNotIn("SECRET_CONNECTION", text)
        self.assertNotIn("SECRET_SAMPLE", text)
        self.assertNotIn("SECRET_EVIDENCE", text)

    def test_bm25_ranks_exact_object_and_field_names_without_an_index(self) -> None:
        documents = build_retrieval_documents(_retrieval_graph())

        results = rank_bm25(documents, "internet sales amount", limit=5)

        self.assertEqual(
            results[0].document.object_id,
            "object:AdventureWorksDW/dbo/FactInternetSales",
        )
        self.assertIn("bm25", results[0].sources)

    def test_vector_and_hybrid_search_use_the_sqlite_cache(self) -> None:
        graph = _retrieval_graph()
        embedder = _FakeEmbedding()
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")
            built = store.build(graph, embedder=embedder, model_path=model, batch_size=4)

            vector = search_retrieval(
                graph,
                "Umsatz pro Jahr",
                mode="vector",
                limit=3,
                embedder=embedder,
                model_path=model,
                store=store,
            )
            hybrid = search_retrieval(
                graph,
                "Internet Umsatz pro Jahr",
                mode="hybrid",
                limit=3,
                embedder=embedder,
                model_path=model,
                store=store,
            )

        self.assertEqual(built.metadata.document_count, 8)
        self.assertEqual(vector.hits[0].label, "dbo.DimDate")
        self.assertEqual(hybrid.hits[0].label, "dbo.FactInternetSales")
        self.assertIn("dbo.DimDate", [hit.label for hit in hybrid.hits])
        self.assertEqual(hybrid.mode, "hybrid")

    def test_index_build_reports_embedding_and_persistence_progress(self) -> None:
        graph = _retrieval_graph()
        events: list[tuple[int, int, str]] = []
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")

            store.build(
                graph,
                embedder=_FakeEmbedding(),
                model_path=model,
                batch_size=3,
                progress=lambda completed, total, phase: events.append(
                    (completed, total, phase)
                ),
            )

        self.assertEqual(
            events,
            [
                (0, 8, "embedding"),
                (3, 8, "embedding"),
                (6, 8, "embedding"),
                (8, 8, "embedding"),
                (8, 8, "writing"),
                (8, 8, "ready"),
            ],
        )

    def test_index_rejects_an_unbounded_document_batch_before_writing(self) -> None:
        graph = _retrieval_graph()
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")

            with self.assertRaises(RetrievalFailure) as raised:
                store.build(
                    graph,
                    embedder=_FakeEmbedding(),
                    model_path=model,
                    batch_size=257,
                )

            self.assertFalse(store.path(graph.name).exists())

        self.assertEqual(raised.exception.code, "invalid_batch_size")

    def test_interrupted_index_resumes_only_the_missing_document_suffix(self) -> None:
        graph = _retrieval_graph()
        events: list[tuple[int, int, str]] = []
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")
            interrupted = _FailingEmbedding(fail_on_call=2)

            with self.assertRaises(RetrievalFailure) as raised:
                store.build(
                    graph,
                    embedder=interrupted,
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )

            self.assertTrue(store.checkpoint_path(graph.name).is_file())
            self.assertFalse(store.path(graph.name).exists())
            resumed = _FakeEmbeddingWithCalls()
            built = store.build(
                graph,
                embedder=resumed,
                model_path=model,
                batch_size=3,
                resume=True,
                progress=lambda completed, total, phase: events.append(
                    (completed, total, phase)
                ),
            )

            self.assertFalse(store.checkpoint_path(graph.name).exists())
            metadata, documents, vectors = store.load(graph, model_path=model)

        self.assertEqual(raised.exception.code, "embedding_failed")
        self.assertEqual(built.resumed_documents, 3)
        self.assertEqual(len(resumed.texts), 5)
        self.assertEqual(events[0], (3, 8, "resuming"))
        self.assertEqual(events[-1], (8, 8, "ready"))
        self.assertEqual(metadata.document_count, 8)
        self.assertEqual(len(documents), 8)
        self.assertEqual(len(vectors), 8)

    def test_resume_rejects_a_checkpoint_for_a_changed_graph_or_model(self) -> None:
        graph = _retrieval_graph()
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")
            with self.assertRaises(RetrievalFailure):
                store.build(
                    graph,
                    embedder=_FailingEmbedding(fail_on_call=2),
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )
            changed = replace(graph, catalog="ChangedCatalog")

            with self.assertRaises(RetrievalFailure) as graph_error:
                store.build(
                    changed,
                    embedder=_FakeEmbedding(),
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )
            model.write_bytes(b"different model")
            with self.assertRaises(RetrievalFailure) as model_error:
                store.build(
                    graph,
                    embedder=_FakeEmbedding(),
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )

        self.assertEqual(graph_error.exception.code, "stale_index_checkpoint")
        self.assertEqual(model_error.exception.code, "stale_index_checkpoint")

    def test_corrupt_checkpoint_fails_without_creating_a_complete_index(self) -> None:
        graph = _retrieval_graph()
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")
            with self.assertRaises(RetrievalFailure):
                store.build(
                    graph,
                    embedder=_FailingEmbedding(fail_on_call=2),
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )
            store.checkpoint_path(graph.name).write_bytes(b"not a sqlite checkpoint")

            with self.assertRaises(RetrievalFailure) as raised:
                store.build(
                    graph,
                    embedder=_FakeEmbedding(),
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )

            self.assertFalse(store.path(graph.name).exists())

        self.assertEqual(raised.exception.code, "invalid_index_checkpoint")

    def test_failed_resumable_rebuild_keeps_the_previous_complete_index(self) -> None:
        graph = _retrieval_graph()
        fact = next(node for node in graph.nodes if node.label == "dbo.FactInternetSales")
        graph = replace(
            graph,
            nodes=tuple(
                replace(
                    node,
                    metadata={**node.metadata, "sample_rows": ["CHECKPOINT_RAW_SAMPLE"]},
                    annotation=GraphAnnotation(description="CHECKPOINT_RETRIEVAL_TEXT"),
                )
                if node.id == fact.id
                else node
                for node in graph.nodes
            ),
        )
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")
            store.build(graph, embedder=_FakeEmbedding(), model_path=model)
            original = store.path(graph.name).read_bytes()

            with self.assertRaises(RetrievalFailure):
                store.build(
                    graph,
                    embedder=_FailingEmbedding(fail_on_call=2),
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )

            current = store.path(graph.name).read_bytes()
            checkpoint = store.checkpoint_path(graph.name).read_bytes()

        self.assertEqual(current, original)
        self.assertNotIn(b"CHECKPOINT_RETRIEVAL_TEXT", checkpoint)
        self.assertNotIn(b"CHECKPOINT_RAW_SAMPLE", checkpoint)

    def test_plain_rebuild_clears_a_stale_resume_checkpoint(self) -> None:
        graph = _retrieval_graph()
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")
            with self.assertRaises(RetrievalFailure):
                store.build(
                    graph,
                    embedder=_FailingEmbedding(fail_on_call=2),
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )

            changed = replace(graph, catalog="ChangedCatalog")
            built = store.build(
                changed,
                embedder=_FakeEmbedding(),
                model_path=model,
                batch_size=3,
            )

            self.assertTrue(built.path.is_file())
            self.assertFalse(store.checkpoint_path(graph.name).exists())

        self.assertEqual(built.resumed_documents, 0)

    def test_sdk_checkpoint_can_be_resumed_by_the_cli_application_path(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir=previous) as temporary_directory:
            project = Path(temporary_directory)
            model = project / "model.gguf"
            model.write_bytes(b"test model")
            sdk = Tarel(project / ".tarel")
            sdk.runtime.graph_store().save(_retrieval_graph())
            with (
                patch(
                    "tarel.application.LlamaCppEmbedding",
                    return_value=_FailingEmbedding(fail_on_call=2),
                ),
                self.assertRaises(RetrievalFailure),
            ):
                sdk.index.build(
                    "retrieval_demo",
                    model_path=model,
                    batch_size=3,
                    resume=True,
                )
            checkpoint_status = sdk.index.status("retrieval_demo")
            resumed = _FakeEmbeddingWithCalls()
            status_output = StringIO()
            output = StringIO()
            try:
                os.chdir(project)
                with redirect_stdout(status_output):
                    status_exit_code = main(
                        ["index", "status", "retrieval_demo", "--format", "json"]
                    )
                with (
                    patch("tarel.application.LlamaCppEmbedding", return_value=resumed),
                    redirect_stdout(output),
                ):
                    exit_code = main(
                        [
                            "index",
                            "build",
                            "retrieval_demo",
                            "--model",
                            str(model),
                            "--batch-size",
                            "3",
                            "--resume",
                            "--format",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous)
            status_payload = json.loads(status_output.getvalue())
            payload = json.loads(output.getvalue())

        self.assertEqual(exit_code, 0)
        self.assertEqual(status_exit_code, 1)
        self.assertEqual(status_payload["checkpoint"]["completed_documents"], 3)
        self.assertFalse(checkpoint_status["current"])
        self.assertIsNone(checkpoint_status["index"])
        self.assertEqual(checkpoint_status["checkpoint"]["completed_documents"], 3)
        self.assertEqual(payload["resumed_documents"], 3)
        self.assertEqual(len(resumed.texts), 5)

    def test_changed_graph_requires_an_explicit_index_rebuild(self) -> None:
        graph = _retrieval_graph()
        embedder = _FakeEmbedding()
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            model = root / "model.gguf"
            model.write_bytes(b"test model")
            store = FileRetrievalIndex(root / "indexes")
            store.build(graph, embedder=embedder, model_path=model)
            changed = replace(graph, catalog="ChangedCatalog")

            with self.assertRaisesRegex(RetrievalFailure, "changed after indexing"):
                search_retrieval(
                    changed,
                    "year",
                    mode="vector",
                    limit=3,
                    embedder=embedder,
                    model_path=model,
                    store=store,
                )

    def test_download_is_atomic_and_checksum_verified(self) -> None:
        payload = b"small deterministic model fixture"
        checksum = hashlib.sha256(payload).hexdigest()
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory)
            target = root / "cache" / "model.gguf"
            spec = ModelSpec(
                name="fixture",
                filename="model.gguf",
                url="https://models.example.test/model.gguf",
                sha256=checksum,
                size=len(payload),
                source="local test fixture",
            )
            with (
                patch.dict(MODEL_SPECS, {"fixture": spec}),
                patch("urllib.request.urlopen", return_value=BytesIO(payload)),
            ):
                downloaded = download_model(name="fixture", target=target)
                reused = download_model(name="fixture", target=target)

        self.assertFalse(downloaded.reused)
        self.assertTrue(reused.reused)

    def test_download_rejects_non_https_model_registry_entries(self) -> None:
        spec = ModelSpec(
            name="unsafe",
            filename="model.gguf",
            url="file:///tmp/model.gguf",
            sha256="0" * 64,
            size=1,
            source="unsafe test fixture",
        )
        with (
            TemporaryDirectory(dir=Path.cwd()) as temporary_directory,
            patch.dict(MODEL_SPECS, {"unsafe": spec}),
            self.assertRaisesRegex(RetrievalFailure, "HTTPS source URL"),
        ):
            download_model(
                name="unsafe",
                target=Path(temporary_directory) / "model.gguf",
            )

    def test_llama_cpp_embeds_document_batches_as_single_sequences(self) -> None:
        texts = tuple(f"document-{number} " * 180 for number in range(1, 4))
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            model_path = Path(temporary_directory) / "model.gguf"
            model_path.write_bytes(b"test model")
            runtime = _StrictLlama()
            with patch.dict(
                sys.modules,
                {"llama_cpp": SimpleNamespace(Llama=lambda **kwargs: runtime)},
            ):
                embedder = LlamaCppEmbedding(model_path, n_threads=2)

            vectors = embedder.embed_documents(texts, batch_size=3)

        self.assertEqual(runtime.calls, list(texts))
        self.assertEqual(vectors, ((0.6, 0.8),) * 3)

    def test_llama_cpp_batch_failure_identifies_position_without_text(self) -> None:
        protected_text = "PROTECTED_DOCUMENT_VALUE"
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            model_path = Path(temporary_directory) / "model.gguf"
            model_path.write_bytes(b"test model")
            runtime = _StrictLlama(fail_on=protected_text)
            with patch.dict(
                sys.modules,
                {"llama_cpp": SimpleNamespace(Llama=lambda **kwargs: runtime)},
            ):
                embedder = LlamaCppEmbedding(model_path)

            with self.assertRaises(RetrievalFailure) as raised:
                embedder.embed_documents(("safe", protected_text), batch_size=2)

        self.assertEqual(raised.exception.code, "embedding_failed")
        self.assertIn("document 2 of 2", str(raised.exception))
        self.assertNotIn(protected_text, str(raised.exception))


class _FakeEmbedding:
    @property
    def model_id(self) -> str:
        return "fake.gguf"

    def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        batch_size: int,
    ) -> tuple[tuple[float, ...], ...]:
        del batch_size
        return tuple(self._document_vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        lowered = text.casefold()
        if "internet" in lowered:
            return (0.70710678, 0.70710678)
        return (1.0, 0.0)

    @staticmethod
    def _document_vector(text: str) -> tuple[float, ...]:
        lowered = text.casefold()
        if "calendaryear" in lowered or "dimdate" in lowered:
            return (1.0, 0.0)
        if "factinternetsales" in lowered or "salesamount" in lowered:
            return (0.0, 1.0)
        return (0.70710678, 0.70710678)


class _FakeEmbeddingWithCalls(_FakeEmbedding):
    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        batch_size: int,
    ) -> tuple[tuple[float, ...], ...]:
        self.texts.extend(texts)
        return super().embed_documents(texts, batch_size=batch_size)


class _FailingEmbedding(_FakeEmbedding):
    def __init__(self, *, fail_on_call: int) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        batch_size: int,
    ) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RetrievalFailure("embedding_failed", "Synthetic embedding interruption.")
        return super().embed_documents(texts, batch_size=batch_size)


class _StrictLlama:
    def __init__(self, *, fail_on: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on

    def embed(
        self,
        text: str | list[str],
        *,
        normalize: bool,
        truncate: bool,
    ) -> list[float]:
        del normalize, truncate
        if isinstance(text, list):
            raise RuntimeError("multi-sequence embedding is unsafe")
        self.calls.append(text)
        if text == self.fail_on:
            raise RuntimeError(text)
        return [3.0, 4.0]


def _retrieval_graph():
    return build_graph_from_catalog(
        "retrieval_demo",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="AdventureWorksDW",
            dialect="tsql",
            objects=(
                CatalogObject(
                    namespace="dbo",
                    name="FactInternetSales",
                    kind="table",
                    fields=(
                        CatalogField("OrderDateKey", 1, "integer", False),
                        CatalogField("SalesAmount", 2, "decimal", False),
                    ),
                ),
                CatalogObject(
                    namespace="dbo",
                    name="DimDate",
                    kind="table",
                    fields=(
                        CatalogField("DateKey", 1, "integer", False, is_primary_key=True),
                        CatalogField("CalendarYear", 2, "integer", False),
                    ),
                    primary_key=("DateKey",),
                ),
                CatalogObject(
                    namespace="dbo",
                    name="UnrelatedHelper",
                    kind="table",
                    fields=(CatalogField("Value", 1, "integer", True),),
                ),
            ),
            relationships=(
                CatalogRelationship(
                    name="FK_FactInternetSales_DimDate",
                    from_namespace="dbo",
                    from_object="FactInternetSales",
                    from_fields=("OrderDateKey",),
                    to_namespace="dbo",
                    to_object="DimDate",
                    to_fields=("DateKey",),
                ),
            ),
        ),
    )
