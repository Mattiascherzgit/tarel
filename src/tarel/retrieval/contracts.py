"""Small contracts shared by local retrieval implementations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class RetrievalFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    id: str
    object_id: str
    field_id: str | None
    namespace: str
    label: str
    text: str


@dataclass(frozen=True, slots=True)
class RankedDocument:
    document: RetrievalDocument
    score: float
    sources: tuple[str, ...]


class EmbeddingBackend(Protocol):
    """The only model behavior required by TAREL retrieval."""

    @property
    def model_id(self) -> str: ...

    def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        batch_size: int,
    ) -> tuple[tuple[float, ...], ...]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    contract_version: str
    graph: str
    graph_hash: str
    document_count: int
    dimensions: int
    model_id: str
    model_path: str
    model_sha256: str
    normalized: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "dimensions": self.dimensions,
            "document_count": self.document_count,
            "graph": self.graph,
            "graph_hash": self.graph_hash,
            "model_id": self.model_id,
            "model_path": self.model_path,
            "model_sha256": self.model_sha256,
            "normalized": self.normalized,
        }


@dataclass(frozen=True, slots=True)
class IndexBuildResult:
    path: Path
    metadata: IndexMetadata
    resumed_documents: int = 0
