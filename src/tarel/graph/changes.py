"""Deterministic classification of two technical graph observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tarel.graph.contracts import GraphDocument, GraphEdge, GraphFailure, GraphNode

_OBJECT_TYPES = {"table", "view"}


@dataclass(frozen=True, slots=True)
class GraphChange:
    kind: str
    severity: str
    entity_type: str
    target_id: str
    reference: str
    object_id: str | None = None
    namespace: str | None = None
    related_ids: tuple[str, ...] = ()
    before: object = None
    after: object = None

    def to_dict(self) -> dict[str, object]:
        return {
            "after": self.after,
            "before": self.before,
            "entity_type": self.entity_type,
            "kind": self.kind,
            "namespace": self.namespace,
            "object_id": self.object_id,
            "reference": self.reference,
            "related_ids": list(self.related_ids),
            "severity": self.severity,
            "target_id": self.target_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphChange:
        related_ids = data.get("related_ids", [])
        if not isinstance(related_ids, list) or not all(
            isinstance(item, str) for item in related_ids
        ):
            raise GraphFailure("invalid_change_report", "Change related_ids must be strings.")
        return cls(
            kind=_required_string(data, "kind"),
            severity=_required_string(data, "severity"),
            entity_type=_required_string(data, "entity_type"),
            target_id=_required_string(data, "target_id"),
            reference=_required_string(data, "reference"),
            object_id=_optional_string(data.get("object_id")),
            namespace=_optional_string(data.get("namespace")),
            related_ids=tuple(related_ids),
            before=data.get("before"),
            after=data.get("after"),
        )


def classify_graph_changes(
    current: GraphDocument,
    discovered: GraphDocument,
) -> tuple[GraphChange, ...]:
    current_nodes = current.node_by_id()
    discovered_nodes = discovered.node_by_id()
    changes = _graph_changes(current, discovered)
    for node_id in sorted(current_nodes.keys() - discovered_nodes.keys()):
        node = current_nodes[node_id]
        if node.type in _OBJECT_TYPES:
            changes.append(_node_change("object_removed", "breaking", node, current))
        elif node.type == "field":
            changes.append(_node_change("field_removed", "breaking", node, current))
    for node_id in sorted(discovered_nodes.keys() - current_nodes.keys()):
        node = discovered_nodes[node_id]
        if node.type in _OBJECT_TYPES:
            changes.append(_node_change("object_added", "info", node, discovered))
        elif node.type == "field":
            changes.append(_node_change("field_added", "info", node, discovered))

    for node_id in sorted(current_nodes.keys() & discovered_nodes.keys()):
        before = current_nodes[node_id]
        after = discovered_nodes[node_id]
        if before.type in _OBJECT_TYPES and after.type in _OBJECT_TYPES:
            changes.extend(_object_changes(before, after, discovered))
        elif before.type == "field" and after.type == "field":
            changes.extend(_field_changes(before, after, discovered))

    changes.extend(_possible_field_renames(current, discovered))
    changes.extend(_relationship_changes(current, discovered))
    return tuple(sorted(changes, key=lambda item: (item.reference.casefold(), item.kind)))


def node_reference(node: GraphNode, graph: GraphDocument) -> str:
    if node.type != "field":
        return node.label
    parent = graph.node_by_id().get(str(node.metadata.get("object_id") or ""))
    return f"{parent.label}.{node.label}" if parent is not None else node.label


def _graph_changes(current: GraphDocument, discovered: GraphDocument) -> list[GraphChange]:
    changes: list[GraphChange] = []
    for before, after, kind in (
        (current.dialect, discovered.dialect, "graph_dialect_changed"),
        (current.source_type, discovered.source_type, "source_type_changed"),
    ):
        if before != after:
            changes.append(
                GraphChange(
                    kind=kind,
                    severity="review_required",
                    entity_type="graph",
                    target_id=current.name,
                    reference=current.name,
                    before=before,
                    after=after,
                )
            )
    return changes


def _object_changes(
    before: GraphNode,
    after: GraphNode,
    graph: GraphDocument,
) -> list[GraphChange]:
    changes: list[GraphChange] = []
    if before.type != after.type:
        changes.append(
            _node_change(
                "object_kind_changed",
                "review_required",
                after,
                graph,
                before=before.type,
                after=after.type,
            )
        )
    for key, kind, severity in (
        ("primary_key", "primary_key_changed", "breaking"),
        ("technical_description", "technical_description_changed", "info"),
    ):
        if before.metadata.get(key) != after.metadata.get(key):
            changes.append(
                _node_change(
                    kind,
                    severity,
                    after,
                    graph,
                    before=before.metadata.get(key),
                    after=after.metadata.get(key),
                )
            )
    return changes


def _field_changes(
    before: GraphNode,
    after: GraphNode,
    graph: GraphDocument,
) -> list[GraphChange]:
    changes: list[GraphChange] = []
    for key, kind, severity in (
        ("data_type", "field_type_changed", "review_required"),
        ("nullable", "field_nullability_changed", "review_required"),
        ("is_primary_key", "field_key_status_changed", "breaking"),
        ("position", "field_position_changed", "info"),
        ("technical_description", "technical_description_changed", "info"),
    ):
        if before.metadata.get(key) != after.metadata.get(key):
            changes.append(
                _node_change(
                    kind,
                    severity,
                    after,
                    graph,
                    before=before.metadata.get(key),
                    after=after.metadata.get(key),
                )
            )
    return changes


def _possible_field_renames(
    current: GraphDocument,
    discovered: GraphDocument,
) -> list[GraphChange]:
    current_ids = set(current.node_by_id())
    discovered_ids = set(discovered.node_by_id())
    removed = [
        node for node in current.nodes if node.type == "field" and node.id not in discovered_ids
    ]
    added = [
        node for node in discovered.nodes if node.type == "field" and node.id not in current_ids
    ]
    removed_by_signature: dict[tuple[object, ...], list[GraphNode]] = {}
    added_by_signature: dict[tuple[object, ...], list[GraphNode]] = {}
    for node in removed:
        removed_by_signature.setdefault(_rename_signature(node), []).append(node)
    for node in added:
        added_by_signature.setdefault(_rename_signature(node), []).append(node)
    changes: list[GraphChange] = []
    for signature in sorted(removed_by_signature, key=repr):
        before = removed_by_signature[signature]
        after = added_by_signature.get(signature, [])
        if len(before) != 1 or len(after) != 1 or before[0].label == after[0].label:
            continue
        old = before[0]
        new = after[0]
        parent = str(old.metadata.get("object_id") or "")
        changes.append(
            GraphChange(
                kind="possible_field_rename",
                severity="review_required",
                entity_type="node",
                target_id=old.id,
                reference=node_reference(old, current),
                object_id=parent or None,
                namespace=_node_namespace(old, current),
                related_ids=(new.id,),
                before=old.label,
                after=new.label,
            )
        )
    return changes


def _relationship_changes(
    current: GraphDocument,
    discovered: GraphDocument,
) -> list[GraphChange]:
    before = {edge.id: edge for edge in current.edges if edge.type == "foreign_key"}
    after = {edge.id: edge for edge in discovered.edges if edge.type == "foreign_key"}
    changes: list[GraphChange] = []
    for edge_id in sorted(before.keys() - after.keys()):
        changes.append(_edge_change("relationship_removed", "breaking", before[edge_id]))
    for edge_id in sorted(after.keys() - before.keys()):
        changes.append(_edge_change("relationship_added", "info", after[edge_id]))
    for edge_id in sorted(before.keys() & after.keys()):
        old = before[edge_id]
        new = after[edge_id]
        if _relationship_shape(old) != _relationship_shape(new):
            changes.append(
                _edge_change(
                    "relationship_changed",
                    "breaking",
                    new,
                    before=_relationship_shape(old),
                    after=_relationship_shape(new),
                )
            )
    return changes


def _node_change(
    kind: str,
    severity: str,
    node: GraphNode,
    graph: GraphDocument,
    *,
    before: object = None,
    after: object = None,
) -> GraphChange:
    object_id = node.id if node.type in _OBJECT_TYPES else str(node.metadata.get("object_id") or "")
    return GraphChange(
        kind=kind,
        severity=severity,
        entity_type="node",
        target_id=node.id,
        reference=node_reference(node, graph),
        object_id=object_id or None,
        namespace=_node_namespace(node, graph),
        before=before,
        after=after,
    )


def _edge_change(
    kind: str,
    severity: str,
    edge: GraphEdge,
    *,
    before: object = None,
    after: object = None,
) -> GraphChange:
    return GraphChange(
        kind=kind,
        severity=severity,
        entity_type="edge",
        target_id=edge.id,
        reference=str(edge.metadata.get("name") or edge.id),
        object_id=edge.source_id,
        related_ids=tuple(sorted({edge.source_id, edge.target_id})),
        before=before,
        after=after,
    )


def _node_namespace(node: GraphNode, graph: GraphDocument) -> str | None:
    if node.type in _OBJECT_TYPES:
        value = node.metadata.get("namespace")
    else:
        parent = graph.node_by_id().get(str(node.metadata.get("object_id") or ""))
        value = parent.metadata.get("namespace") if parent is not None else None
    return str(value) if value is not None else None


def _rename_signature(node: GraphNode) -> tuple[object, ...]:
    return (
        node.metadata.get("object_id"),
        node.metadata.get("position"),
        node.metadata.get("data_type"),
        node.metadata.get("nullable"),
        node.metadata.get("is_primary_key"),
    )


def _relationship_shape(edge: GraphEdge) -> dict[str, object]:
    return {
        "from_fields": edge.metadata.get("from_fields"),
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "to_fields": edge.metadata.get("to_fields"),
    }


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise GraphFailure("invalid_change_report", f"Report field must be a string: {key}")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GraphFailure("invalid_change_report", "Optional report field must be a string.")
    return value
