import hashlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

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
from tarel.retrieval.local import MODEL_SPECS, ModelSpec, download_model


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
