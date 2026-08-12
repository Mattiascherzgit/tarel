"""Rebuildable SQLite vector cache and transparent hybrid retrieval."""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
import tempfile
from array import array
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from tarel.annotations.states import DEFAULT_CONTEXT_ANNOTATION_STATES
from tarel.graph.contracts import GraphDocument
from tarel.graph.revision import graph_revision
from tarel.retrieval.bm25 import rank_bm25, tokenize
from tarel.retrieval.contracts import (
    EmbeddingBackend,
    IndexBuildResult,
    IndexMetadata,
    RankedDocument,
    RetrievalDocument,
    RetrievalFailure,
)
from tarel.retrieval.documents import build_retrieval_documents
from tarel.retrieval.local import sha256_file
from tarel.search import FieldSearchHit, SearchHit, SearchResults

_CONTRACT_VERSION = "tarel.retrieval.v0.1"
_GRAPH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RRF_K = 60
_MAX_FIELDS = 8


class FileRetrievalIndex:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "indexes"

    def build(
        self,
        graph: GraphDocument,
        *,
        embedder: EmbeddingBackend,
        model_path: Path,
        batch_size: int = 16,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> IndexBuildResult:
        documents = build_retrieval_documents(graph)
        total = len(documents)
        if progress is not None:
            progress(0, total, "embedding")
        vector_batches = []
        for start in range(0, total, batch_size):
            batch = documents[start : start + batch_size]
            vector_batches.extend(
                embedder.embed_documents(
                    tuple(document.text for document in batch),
                    batch_size=batch_size,
                )
            )
            if progress is not None:
                progress(min(start + len(batch), total), total, "embedding")
        vectors = tuple(vector_batches)
        dimensions = _validate_vectors(vectors, expected_count=len(documents))
        metadata = IndexMetadata(
            contract_version=_CONTRACT_VERSION,
            graph=graph.name,
            graph_hash=graph_revision(graph),
            document_count=len(documents),
            dimensions=dimensions,
            model_id=embedder.model_id,
            model_path=str(model_path.resolve()),
            model_sha256=sha256_file(model_path),
            normalized=True,
        )
        path = self.path(graph.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".index-",
            suffix=".sqlite",
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        try:
            if progress is not None:
                progress(total, total, "writing")
            with sqlite3.connect(temporary_path) as connection:
                _create_schema(connection)
                connection.executemany(
                    "INSERT INTO metadata(key, value) VALUES (?, ?)",
                    ((key, json.dumps(value)) for key, value in metadata.to_dict().items()),
                )
                connection.executemany(
                    """
                    INSERT INTO documents(id, object_id, field_id, namespace, label, text)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            document.id,
                            document.object_id,
                            document.field_id,
                            document.namespace,
                            document.label,
                            document.text,
                        )
                        for document in documents
                    ),
                )
                connection.executemany(
                    "INSERT INTO vectors(document_id, dimensions, value) VALUES (?, ?, ?)",
                    (
                        (document.id, dimensions, _pack_vector(vector))
                        for document, vector in zip(documents, vectors, strict=True)
                    ),
                )
                connection.commit()
            os.replace(temporary_path, path)
            if progress is not None:
                progress(total, total, "ready")
        except (OSError, sqlite3.Error) as exc:
            temporary_path.unlink(missing_ok=True)
            raise RetrievalFailure(
                "index_build_failed",
                "Could not persist retrieval index.",
            ) from exc
        return IndexBuildResult(path=path, metadata=metadata)

    def metadata(self, name: str) -> IndexMetadata:
        path = self.path(name)
        if not path.is_file():
            raise RetrievalFailure("index_not_found", f"Retrieval index not found: {name}")
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                values = {
                    str(key): json.loads(value)
                    for key, value in connection.execute("SELECT key, value FROM metadata")
                }
        except (OSError, sqlite3.Error, json.JSONDecodeError) as exc:
            raise RetrievalFailure(
                "invalid_index",
                f"Could not read retrieval index: {name}",
            ) from exc
        try:
            metadata = IndexMetadata(**values)
        except (TypeError, ValueError) as exc:
            raise RetrievalFailure("invalid_index", "Retrieval index metadata is invalid.") from exc
        if metadata.contract_version != _CONTRACT_VERSION:
            raise RetrievalFailure("unsupported_index", "Retrieval index must be rebuilt.")
        return metadata

    def load(
        self,
        graph: GraphDocument,
        *,
        model_path: Path,
    ) -> tuple[IndexMetadata, tuple[RetrievalDocument, ...], tuple[tuple[float, ...], ...]]:
        metadata = self.metadata(graph.name)
        if metadata.graph_hash != graph_revision(graph):
            raise RetrievalFailure(
                "stale_index",
                f"Graph {graph.name} changed after indexing. Run `tarel index build {graph.name}`.",
            )
        if metadata.model_sha256 != sha256_file(model_path):
            raise RetrievalFailure(
                "model_index_mismatch",
                "The selected embedding model differs from the indexed model. Rebuild the index.",
            )
        path = self.path(graph.name)
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                rows = connection.execute(
                    """
                    SELECT d.id, d.object_id, d.field_id, d.namespace, d.label, d.text,
                           v.dimensions, v.value
                    FROM documents AS d
                    JOIN vectors AS v ON v.document_id = d.id
                    ORDER BY d.id
                    """
                ).fetchall()
        except sqlite3.Error as exc:
            raise RetrievalFailure("invalid_index", "Could not read retrieval vectors.") from exc
        documents = tuple(
            RetrievalDocument(
                id=str(row[0]),
                object_id=str(row[1]),
                field_id=str(row[2]) if row[2] is not None else None,
                namespace=str(row[3]),
                label=str(row[4]),
                text=str(row[5]),
            )
            for row in rows
        )
        vectors = tuple(_unpack_vector(row[7], int(row[6])) for row in rows)
        if len(documents) != metadata.document_count:
            raise RetrievalFailure("invalid_index", "Retrieval index document count is invalid.")
        return metadata, documents, vectors

    def path(self, name: str) -> Path:
        if not _GRAPH_NAME.fullmatch(name):
            raise RetrievalFailure("invalid_graph_name", "Invalid graph name for retrieval index.")
        return self.root / name / "index.sqlite"


def search_retrieval(
    graph: GraphDocument,
    query: str,
    *,
    mode: str,
    limit: int,
    namespace: str | None = None,
    object_ids: frozenset[str] | None = None,
    embedder: EmbeddingBackend | None = None,
    model_path: Path | None = None,
    store: FileRetrievalIndex | None = None,
    annotation_states: frozenset[str] = DEFAULT_CONTEXT_ANNOTATION_STATES,
) -> SearchResults:
    if mode not in {"bm25", "vector", "hybrid"}:
        raise RetrievalFailure("invalid_retrieval_mode", "Mode must be bm25, vector, or hybrid.")
    if not 1 <= limit <= 100:
        raise RetrievalFailure("invalid_limit", "Search limit must be between 1 and 100.")
    if mode in {"vector", "hybrid"} and annotation_states != DEFAULT_CONTEXT_ANNOTATION_STATES:
        raise RetrievalFailure(
            "unsupported_annotation_filter",
            "Vector and hybrid search currently use the default annotation states. "
            "Use lexical or BM25 mode for a custom annotation-state filter.",
        )
    documents = tuple(
        document
        for document in build_retrieval_documents(graph, annotation_states=annotation_states)
        if namespace is None or document.namespace.casefold() == namespace.casefold()
        if object_ids is None or document.object_id in object_ids
    )
    candidate_limit = max(20, min(len(documents), limit * 5))
    bm25_results = (
        rank_bm25(documents, query, limit=candidate_limit) if mode in {"bm25", "hybrid"} else ()
    )
    vector_results: tuple[RankedDocument, ...] = ()
    if mode in {"vector", "hybrid"}:
        if embedder is None or model_path is None:
            raise RetrievalFailure("missing_embedding_backend", "Vector retrieval needs a model.")
        _metadata, indexed_documents, vectors = (store or FileRetrievalIndex()).load(
            graph,
            model_path=model_path,
        )
        indexed = tuple(
            (document, vector)
            for document, vector in zip(indexed_documents, vectors, strict=True)
            if namespace is None or document.namespace.casefold() == namespace.casefold()
            if object_ids is None or document.object_id in object_ids
        )
        query_vector = embedder.embed_query(query)
        vector_results = _rank_vectors(indexed, query_vector, limit=candidate_limit)

    if mode == "bm25":
        ranked = bm25_results
    elif mode == "vector":
        ranked = vector_results
    else:
        ranked = _reciprocal_rank_fusion(bm25_results, vector_results, limit=candidate_limit)
    return _object_results(graph, query, mode=mode, ranked=ranked, limit=limit)


def _rank_vectors(
    indexed: tuple[tuple[RetrievalDocument, tuple[float, ...]], ...],
    query_vector: tuple[float, ...],
    *,
    limit: int,
) -> tuple[RankedDocument, ...]:
    ranked: list[RankedDocument] = []
    for document, vector in indexed:
        if len(vector) != len(query_vector):
            raise RetrievalFailure("model_index_mismatch", "Query and index dimensions differ.")
        score = sum(left * right for left, right in zip(vector, query_vector, strict=True))
        if math.isfinite(score):
            ranked.append(RankedDocument(document=document, score=score, sources=("vector",)))
    return tuple(
        sorted(
            ranked,
            key=lambda item: (-item.score, item.document.label.casefold(), item.document.id),
        )[:limit]
    )


def _reciprocal_rank_fusion(
    left: tuple[RankedDocument, ...],
    right: tuple[RankedDocument, ...],
    *,
    limit: int,
) -> tuple[RankedDocument, ...]:
    scores: defaultdict[str, float] = defaultdict(float)
    sources: defaultdict[str, set[str]] = defaultdict(set)
    documents: dict[str, RetrievalDocument] = {}
    for results in (left, right):
        for rank, result in enumerate(results, start=1):
            document_id = result.document.id
            documents[document_id] = result.document
            scores[document_id] += 1.0 / (_RRF_K + rank)
            sources[document_id].update(result.sources)
    return tuple(
        sorted(
            (
                RankedDocument(
                    document=documents[document_id],
                    score=score,
                    sources=tuple(sorted(sources[document_id])),
                )
                for document_id, score in scores.items()
            ),
            key=lambda item: (-item.score, item.document.label.casefold(), item.document.id),
        )[:limit]
    )


def _object_results(
    graph: GraphDocument,
    query: str,
    *,
    mode: str,
    ranked: tuple[RankedDocument, ...],
    limit: int,
) -> SearchResults:
    node_by_id = graph.node_by_id()
    by_object: defaultdict[str, list[RankedDocument]] = defaultdict(list)
    for result in ranked:
        by_object[result.document.object_id].append(result)
    query_terms = tuple(sorted(set(tokenize(query))))
    hits: list[SearchHit] = []
    for object_id, results in by_object.items():
        node = node_by_id.get(object_id)
        if node is None or node.type not in {"table", "view"}:
            continue
        ordered = sorted(results, key=lambda item: (-item.score, item.document.id))
        best = ordered[0]
        fields = tuple(
            FieldSearchHit(
                id=result.document.field_id or "",
                label=result.document.label.rsplit(".", 1)[-1],
                score=max(1, round(result.score * 1_000_000)),
                reasons=tuple(f"retrieval:{source}" for source in result.sources),
            )
            for result in ordered
            if result.document.field_id is not None
        )[:_MAX_FIELDS]
        document_terms = set().union(*(set(tokenize(result.document.text)) for result in ordered))
        matched_terms = tuple(term for term in query_terms if term in document_terms)
        source_names = tuple(sorted(set().union(*(set(result.sources) for result in ordered))))
        hits.append(
            SearchHit(
                id=object_id,
                label=node.label,
                type=node.type,
                score=max(1, round(best.score * 1_000_000)),
                matched_terms=matched_terms,
                reasons=tuple(f"retrieval:{source}" for source in source_names),
                fields=fields,
            )
        )
    ordered_hits = tuple(
        sorted(hits, key=lambda hit: (-hit.score, hit.label.casefold(), hit.id))[:limit]
    )
    return SearchResults(
        graph=graph.name,
        query=query,
        terms=query_terms,
        hits=ordered_hits,
        mode=mode,
    )


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            object_id TEXT NOT NULL,
            field_id TEXT,
            namespace TEXT NOT NULL,
            label TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE vectors (
            document_id TEXT PRIMARY KEY REFERENCES documents(id),
            dimensions INTEGER NOT NULL,
            value BLOB NOT NULL
        );
        """
    )


def _validate_vectors(
    vectors: tuple[tuple[float, ...], ...],
    *,
    expected_count: int,
) -> int:
    if len(vectors) != expected_count or not vectors:
        raise RetrievalFailure("embedding_failed", "Embedding count does not match documents.")
    dimensions = len(vectors[0])
    if dimensions < 1 or any(len(vector) != dimensions for vector in vectors):
        raise RetrievalFailure("embedding_failed", "Embedding dimensions are inconsistent.")
    if any(not math.isfinite(value) for vector in vectors for value in vector):
        raise RetrievalFailure("embedding_failed", "Embedding contains a non-finite number.")
    return dimensions


def _pack_vector(vector: tuple[float, ...]) -> bytes:
    values = array("f", vector)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def _unpack_vector(value: bytes, dimensions: int) -> tuple[float, ...]:
    values = array("f")
    values.frombytes(value)
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) != dimensions:
        raise RetrievalFailure("invalid_index", "Stored vector dimensions are invalid.")
    return tuple(values)
