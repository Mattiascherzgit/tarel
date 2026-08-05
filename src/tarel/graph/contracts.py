"""Dependency-free graph contracts and explicit JSON serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ANNOTATION_STATES = frozenset(
    {"deferred", "draft", "rejected", "review_required", "validated"}
)


class GraphFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AnnotationEvidence:
    source: str
    reference: str
    value: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "reason": self.reason,
            "reference": self.reference,
            "source": self.source,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnnotationEvidence:
        return cls(
            source=_string(data, "source"),
            reference=_string(data, "reference"),
            value=_optional_string(data.get("value")),
            reason=_optional_string(data.get("reason")),
        )


@dataclass(frozen=True, slots=True)
class AnnotationProvenance:
    source: str
    provider: str | None = None
    model: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"model": self.model, "provider": self.provider, "source": self.source}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnnotationProvenance:
        return cls(
            source=_string(data, "source"),
            provider=_optional_string(data.get("provider")),
            model=_optional_string(data.get("model")),
        )


@dataclass(frozen=True, slots=True)
class GraphAnnotation:
    description: str
    role: str | None = None
    synonyms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    confidence: float | None = None
    confidence_reason: str | None = None
    evidence: tuple[AnnotationEvidence, ...] = ()
    provenance: AnnotationProvenance = field(
        default_factory=lambda: AnnotationProvenance(source="agent")
    )
    state: str = "draft"

    def to_dict(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "description": self.description,
            "evidence": [item.to_dict() for item in self.evidence],
            "provenance": self.provenance.to_dict(),
            "role": self.role,
            "state": self.state,
            "synonyms": list(self.synonyms),
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphAnnotation:
        evidence = data.get("evidence", [])
        provenance = data.get("provenance", {"source": "agent"})
        if not isinstance(evidence, list) or not isinstance(provenance, dict):
            raise GraphFailure("invalid_graph", "Annotation evidence or provenance is invalid.")
        return cls(
            description=_string(data, "description"),
            role=_optional_string(data.get("role")),
            synonyms=_string_tuple(data.get("synonyms", [])),
            warnings=_string_tuple(data.get("warnings", [])),
            confidence=_optional_confidence(data.get("confidence")),
            confidence_reason=_optional_string(data.get("confidence_reason")),
            evidence=tuple(AnnotationEvidence.from_dict(item) for item in evidence),
            provenance=AnnotationProvenance.from_dict(provenance),
            state=_annotation_state(data.get("state")),
        )


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str
    type: str
    label: str
    metadata: dict[str, object]
    annotation: GraphAnnotation | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "annotation": self.annotation.to_dict() if self.annotation else None,
            "id": self.id,
            "label": self.label,
            "metadata": self.metadata,
            "type": self.type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphNode:
        metadata = data.get("metadata", {})
        annotation = data.get("annotation")
        if not isinstance(metadata, dict):
            raise GraphFailure("invalid_graph", "Node metadata must be an object.")
        if annotation is not None and not isinstance(annotation, dict):
            raise GraphFailure("invalid_graph", "Node annotation must be an object or null.")
        return cls(
            id=_string(data, "id"),
            type=_string(data, "type"),
            label=_string(data, "label"),
            metadata=metadata,
            annotation=GraphAnnotation.from_dict(annotation) if annotation else None,
        )


@dataclass(frozen=True, slots=True)
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    type: str
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "metadata": self.metadata,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "type": self.type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphEdge:
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise GraphFailure("invalid_graph", "Edge metadata must be an object.")
        return cls(
            id=_string(data, "id"),
            source_id=_string(data, "source_id"),
            target_id=_string(data, "target_id"),
            type=_string(data, "type"),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class GraphDocument:
    name: str
    connector: str
    source_type: str
    catalog: str
    dialect: str | None
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    contract_version: str = "tarel.graph.v0.1"

    def node_by_id(self) -> dict[str, GraphNode]:
        return {node.id: node for node in self.nodes}

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog": self.catalog,
            "connector": self.connector,
            "contract_version": self.contract_version,
            "dialect": self.dialect,
            "edges": [edge.to_dict() for edge in self.edges],
            "name": self.name,
            "nodes": [node.to_dict() for node in self.nodes],
            "source_type": self.source_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphDocument:
        nodes = data.get("nodes")
        edges = data.get("edges")
        if data.get("contract_version") != "tarel.graph.v0.1":
            raise GraphFailure("unsupported_graph", "Unsupported TAREL graph contract.")
        if not isinstance(nodes, list) or not isinstance(edges, list):
            raise GraphFailure("invalid_graph", "Graph nodes and edges must be arrays.")
        graph = cls(
            name=_string(data, "name"),
            connector=_string(data, "connector"),
            source_type=_string(data, "source_type"),
            catalog=_string(data, "catalog"),
            dialect=_optional_string(data.get("dialect")),
            nodes=tuple(GraphNode.from_dict(item) for item in nodes),
            edges=tuple(GraphEdge.from_dict(item) for item in edges),
            contract_version="tarel.graph.v0.1",
        )
        _validate_graph(graph)
        return graph


def _validate_graph(graph: GraphDocument) -> None:
    node_ids = [node.id for node in graph.nodes]
    if len(node_ids) != len(set(node_ids)):
        raise GraphFailure("invalid_graph", "Graph contains duplicate node IDs.")
    known = set(node_ids)
    for edge in graph.edges:
        if edge.source_id not in known or edge.target_id not in known:
            raise GraphFailure("invalid_graph", f"Graph edge references an unknown node: {edge.id}")


def _string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise GraphFailure("invalid_graph", f"Graph field must be a non-empty string: {key}")
    return value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GraphFailure("invalid_graph", "Optional graph field must be a string or null.")
    return value


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GraphFailure("invalid_graph", "Graph field must be an array of strings.")
    return tuple(value)


def _optional_confidence(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GraphFailure("invalid_graph", "Annotation confidence must be a number or null.")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise GraphFailure("invalid_graph", "Annotation confidence must be between 0 and 1.")
    return result


def _annotation_state(value: Any) -> str:
    if not isinstance(value, str) or value not in ANNOTATION_STATES:
        supported = ", ".join(sorted(ANNOTATION_STATES))
        raise GraphFailure(
            "invalid_graph",
            f"Annotation state must be one of: {supported}.",
        )
    return value
