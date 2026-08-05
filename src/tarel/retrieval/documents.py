"""Create safe retrieval documents from an in-memory graph."""

from __future__ import annotations

from tarel.annotations.states import (
    DEFAULT_CONTEXT_ANNOTATION_STATES,
    annotation_is_visible,
)
from tarel.graph.contracts import GraphAnnotation, GraphDocument, GraphNode
from tarel.retrieval.contracts import RetrievalDocument


def build_retrieval_documents(
    graph: GraphDocument,
    *,
    annotation_states: frozenset[str] = DEFAULT_CONTEXT_ANNOTATION_STATES,
) -> tuple[RetrievalDocument, ...]:
    """Project only approved metadata; arbitrary metadata and evidence stay out."""
    fields_by_object: dict[str, list[GraphNode]] = {}
    for node in graph.nodes:
        object_id = node.metadata.get("object_id")
        if node.type == "field" and isinstance(object_id, str):
            fields_by_object.setdefault(object_id, []).append(node)

    documents: list[RetrievalDocument] = []
    objects = sorted(
        (node for node in graph.nodes if node.type in {"table", "view"}),
        key=lambda node: (node.label.casefold(), node.id),
    )
    for node in objects:
        namespace = _text(node.metadata.get("namespace"))
        fields = sorted(
            fields_by_object.get(node.id, []),
            key=lambda field: (int(field.metadata.get("position") or 9999), field.id),
        )
        documents.append(
            RetrievalDocument(
                id=f"object:{node.id}",
                object_id=node.id,
                field_id=None,
                namespace=namespace,
                label=node.label,
                text=_object_text(graph, node, fields, annotation_states),
            )
        )
        documents.extend(
            _field_document(node, field, namespace, annotation_states) for field in fields
        )
    return tuple(documents)


def _object_text(
    graph: GraphDocument,
    node: GraphNode,
    fields: list[GraphNode],
    annotation_states: frozenset[str],
) -> str:
    lines = [
        f"Database object: {node.label}",
        f"Object type: {node.type}",
        f"Catalog: {graph.catalog}",
        f"Namespace: {_text(node.metadata.get('namespace'))}",
    ]
    if graph.dialect:
        lines.append(f"SQL dialect: {graph.dialect}")
    _append_approved_description(lines, node, annotation_states)
    grain = (
        _text(node.metadata.get("grain"))
        if node.annotation is None or annotation_is_visible(node.annotation, annotation_states)
        else ""
    )
    if grain:
        lines.append(f"Grain: {grain}")
    if fields:
        lines.append("Fields:")
    for field in fields:
        flags = _field_flags(field, annotation_states)
        detail = f" ({'; '.join(flags)})" if flags else ""
        lines.append(f"- {field.label}{detail}")
        description = _approved_description(field, annotation_states)
        if description:
            lines.append(f"  Description: {description}")
        synonyms = _synonyms(field.annotation, annotation_states)
        if synonyms:
            lines.append(f"  Synonyms: {', '.join(synonyms)}")
    return "\n".join(lines)


def _field_document(
    parent: GraphNode,
    field: GraphNode,
    namespace: str,
    annotation_states: frozenset[str],
) -> RetrievalDocument:
    lines = [
        f"Database field: {parent.label}.{field.label}",
        f"Parent object: {parent.label}",
        f"Namespace: {namespace}",
    ]
    flags = _field_flags(field, annotation_states)
    if flags:
        lines.append(f"Properties: {', '.join(flags)}")
    _append_approved_description(lines, field, annotation_states)
    return RetrievalDocument(
        id=f"field:{field.id}",
        object_id=parent.id,
        field_id=field.id,
        namespace=namespace,
        label=f"{parent.label}.{field.label}",
        text="\n".join(lines),
    )


def _field_flags(field: GraphNode, annotation_states: frozenset[str]) -> list[str]:
    flags: list[str] = []
    data_type = _text(field.metadata.get("data_type"))
    annotation = (
        field.annotation
        if annotation_is_visible(field.annotation, annotation_states)
        else None
    )
    semantic_type = (
        _text(field.metadata.get("semantic_type"))
        if field.annotation is None or annotation is not None
        else ""
    )
    if data_type:
        flags.append(f"data type={data_type}")
    if semantic_type:
        flags.append(f"semantic type={semantic_type}")
    if field.metadata.get("is_primary_key") is True:
        flags.append("primary key")
    if field.metadata.get("is_foreign_key") is True:
        flags.append("foreign key")
    if annotation and annotation.role:
        flags.append(f"role={annotation.role}")
    return flags


def _append_approved_description(
    lines: list[str],
    node: GraphNode,
    annotation_states: frozenset[str],
) -> None:
    description = _approved_description(node, annotation_states)
    if description:
        lines.append(f"Description: {description}")
    annotation = (
        node.annotation if annotation_is_visible(node.annotation, annotation_states) else None
    )
    if annotation and annotation.role:
        lines.append(f"Role: {annotation.role}")
    synonyms = _synonyms(node.annotation, annotation_states)
    if synonyms:
        lines.append(f"Synonyms: {', '.join(synonyms)}")


def _approved_description(node: GraphNode, annotation_states: frozenset[str]) -> str:
    if annotation_is_visible(node.annotation, annotation_states):
        assert node.annotation is not None
        return node.annotation.description
    return _text(node.metadata.get("technical_description"))


def _synonyms(
    annotation: GraphAnnotation | None,
    annotation_states: frozenset[str],
) -> tuple[str, ...]:
    return annotation.synonyms if annotation_is_visible(annotation, annotation_states) else ()


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
