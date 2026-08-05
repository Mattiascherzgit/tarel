"""Classify source changes and reconcile them with reviewed local knowledge."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from tarel.graph.changes import GraphChange, classify_graph_changes, node_reference
from tarel.graph.contracts import (
    GraphDocument,
    GraphEdge,
    GraphFailure,
    GraphNode,
)
from tarel.graph.revision import graph_revision

_SEMANTIC_METADATA_KEYS = {
    "annotation_review",
    "change_review",
    "grain",
    "semantic_type",
}


@dataclass(frozen=True, slots=True)
class StaleClaim:
    claim_type: str
    target_id: str
    reference: str
    previous_state: str
    present: bool
    reasons: tuple[str, ...]
    claim: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "claim": self.claim,
            "claim_type": self.claim_type,
            "present": self.present,
            "previous_state": self.previous_state,
            "reasons": list(self.reasons),
            "reference": self.reference,
            "target_id": self.target_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StaleClaim:
        reasons = data.get("reasons")
        claim = data.get("claim")
        if not isinstance(reasons, list) or not all(isinstance(item, str) for item in reasons):
            raise GraphFailure("invalid_change_report", "Stale-claim reasons must be strings.")
        if not isinstance(claim, dict):
            raise GraphFailure("invalid_change_report", "Stale claim must be an object.")
        present = data.get("present")
        if not isinstance(present, bool):
            raise GraphFailure("invalid_change_report", "Stale-claim present must be boolean.")
        return cls(
            claim_type=_report_string(data, "claim_type"),
            target_id=_report_string(data, "target_id"),
            reference=_report_string(data, "reference"),
            previous_state=_report_string(data, "previous_state"),
            present=present,
            reasons=tuple(reasons),
            claim=claim,
        )


@dataclass(frozen=True, slots=True)
class GraphRefreshReport:
    before_revision: str
    after_revision: str
    changes: tuple[GraphChange, ...]
    stale_claims: tuple[StaleClaim, ...]
    carried_annotations: int
    carried_relationships: int
    removed_annotated_nodes: int
    removed_relationships: int
    superseded_relationships: int
    review_required_annotations: int
    review_required_relationships: int
    added_nodes: int
    removed_nodes: int
    contract_version: str = "tarel.change.v0.1"

    @property
    def affected_node_ids(self) -> tuple[str, ...]:
        identifiers: set[str] = set()
        for change in self.changes:
            if change.entity_type == "node":
                identifiers.add(change.target_id)
            if change.object_id:
                identifiers.add(change.object_id)
            identifiers.update(change.related_ids)
        return tuple(sorted(identifiers))

    @property
    def affected_edge_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(change.target_id for change in self.changes if change.entity_type == "edge")
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "added_nodes": self.added_nodes,
            "after_revision": self.after_revision,
            "before_revision": self.before_revision,
            "carried_annotations": self.carried_annotations,
            "carried_relationships": self.carried_relationships,
            "changes": [change.to_dict() for change in self.changes],
            "contract_version": self.contract_version,
            "removed_annotated_nodes": self.removed_annotated_nodes,
            "removed_nodes": self.removed_nodes,
            "removed_relationships": self.removed_relationships,
            "review_required_annotations": self.review_required_annotations,
            "review_required_relationships": self.review_required_relationships,
            "stale_claims": [claim.to_dict() for claim in self.stale_claims],
            "superseded_relationships": self.superseded_relationships,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphRefreshReport:
        if data.get("contract_version") != "tarel.change.v0.1":
            raise GraphFailure("unsupported_change_report", "Unsupported change-report contract.")
        changes = data.get("changes")
        stale_claims = data.get("stale_claims")
        if not isinstance(changes, list) or not all(isinstance(item, dict) for item in changes):
            raise GraphFailure("invalid_change_report", "Report changes must be objects.")
        if not isinstance(stale_claims, list) or not all(
            isinstance(item, dict) for item in stale_claims
        ):
            raise GraphFailure("invalid_change_report", "Report stale_claims must be objects.")
        return cls(
            before_revision=_report_string(data, "before_revision"),
            after_revision=_report_string(data, "after_revision"),
            changes=tuple(GraphChange.from_dict(item) for item in changes),
            stale_claims=tuple(StaleClaim.from_dict(item) for item in stale_claims),
            carried_annotations=_report_int(data, "carried_annotations"),
            carried_relationships=_report_int(data, "carried_relationships"),
            removed_annotated_nodes=_report_int(data, "removed_annotated_nodes"),
            removed_relationships=_report_int(data, "removed_relationships"),
            superseded_relationships=_report_int(data, "superseded_relationships"),
            review_required_annotations=_report_int(data, "review_required_annotations"),
            review_required_relationships=_report_int(data, "review_required_relationships"),
            added_nodes=_report_int(data, "added_nodes"),
            removed_nodes=_report_int(data, "removed_nodes"),
        )


def refresh_graph(
    current: GraphDocument,
    discovered: GraphDocument,
) -> tuple[GraphDocument, GraphRefreshReport]:
    if current.name != discovered.name:
        raise GraphFailure("refresh_mismatch", "Graph refresh requires the same graph name.")
    if current.connector != discovered.connector or current.catalog != discovered.catalog:
        raise GraphFailure(
            "refresh_mismatch",
            "Graph refresh cannot change connector or catalog.",
        )

    before_revision = graph_revision(current)
    changes = classify_graph_changes(current, discovered)
    reasons_by_node = _stale_reasons_by_node(changes)
    current_nodes = current.node_by_id()
    discovered_ids = {node.id for node in discovered.nodes}
    carried_annotations = 0
    review_required_annotations = 0
    stale_claims: list[StaleClaim] = []
    refreshed_nodes: list[GraphNode] = []
    for node in discovered.nodes:
        previous = current_nodes.get(node.id)
        if previous is None:
            refreshed_nodes.append(node)
            continue
        metadata = dict(node.metadata)
        for key in _SEMANTIC_METADATA_KEYS:
            if key in previous.metadata:
                metadata[key] = previous.metadata[key]
        annotation = previous.annotation
        if annotation is not None:
            carried_annotations += 1
            reasons = reasons_by_node.get(node.id, ())
            if annotation.state == "validated" and reasons:
                previous_state = annotation.state
                annotation = replace(annotation, state="review_required")
                metadata["change_review"] = {
                    "from_revision": before_revision,
                    "reasons": list(reasons),
                }
                review_required_annotations += 1
                stale_claims.append(
                    _annotation_claim(
                        replace(node, metadata=metadata, annotation=annotation),
                        current,
                        previous_state=previous_state,
                        present=True,
                        reasons=reasons,
                    )
                )
        refreshed_nodes.append(replace(node, metadata=metadata, annotation=annotation))

    removed_node_ids = set(current_nodes) - discovered_ids
    for node_id in sorted(removed_node_ids):
        node = current_nodes[node_id]
        if node.annotation is not None:
            reasons = reasons_by_node.get(node.id, ("node_removed",))
            stale_claims.append(
                _annotation_claim(
                    node,
                    current,
                    previous_state=node.annotation.state,
                    present=False,
                    reasons=reasons,
                )
            )

    candidates = [edge for edge in current.edges if edge.type == "relationship_candidate"]
    declared_relationships = [edge for edge in discovered.edges if edge.type == "foreign_key"]
    carried: list[GraphEdge] = []
    removed_relationships = 0
    superseded_relationships = 0
    review_required_relationships = 0
    refreshed = replace(discovered, nodes=tuple(refreshed_nodes))
    changed_field_ids = {
        change.target_id
        for change in changes
        if change.entity_type == "node"
        and change.kind
        in {
            "field_removed",
            "field_type_changed",
            "field_nullability_changed",
            "field_key_status_changed",
        }
    }
    for candidate in candidates:
        if not _candidate_endpoints_exist(refreshed, candidate):
            removed_relationships += 1
            stale_claims.append(
                _relationship_claim(
                    candidate,
                    present=False,
                    reasons=("endpoint_removed",),
                )
            )
            continue
        if any(_same_field_pair(candidate, declared) for declared in declared_relationships):
            superseded_relationships += 1
            stale_claims.append(
                _relationship_claim(
                    candidate,
                    present=False,
                    reasons=("superseded_by_declared_relationship",),
                )
            )
            continue
        changed_endpoints = _candidate_field_ids(refreshed, candidate) & changed_field_ids
        if candidate.metadata.get("state") == "validated" and changed_endpoints:
            metadata = dict(candidate.metadata)
            metadata["state"] = "review_required"
            metadata["change_review"] = {
                "from_revision": before_revision,
                "reasons": ["relationship_endpoint_changed"],
            }
            candidate = replace(candidate, metadata=metadata)
            review_required_relationships += 1
            stale_claims.append(
                _relationship_claim(
                    candidate,
                    present=True,
                    reasons=("relationship_endpoint_changed",),
                    previous_state="validated",
                )
            )
        carried.append(candidate)

    refreshed_edges = (*refreshed.edges, *sorted(carried, key=lambda edge: edge.id))
    refreshed = replace(refreshed, edges=refreshed_edges)
    report = GraphRefreshReport(
        before_revision=before_revision,
        after_revision=graph_revision(refreshed),
        changes=changes,
        stale_claims=tuple(
            sorted(stale_claims, key=lambda item: (item.reference.casefold(), item.target_id))
        ),
        carried_annotations=carried_annotations,
        carried_relationships=len(carried),
        removed_annotated_nodes=sum(
            current_nodes[node_id].annotation is not None for node_id in removed_node_ids
        ),
        removed_relationships=removed_relationships
        + sum(change.kind == "relationship_removed" for change in changes),
        superseded_relationships=superseded_relationships,
        review_required_annotations=review_required_annotations,
        review_required_relationships=review_required_relationships,
        added_nodes=len(discovered_ids - set(current_nodes)),
        removed_nodes=len(removed_node_ids),
    )
    return refreshed, report


def _stale_reasons_by_node(changes: tuple[GraphChange, ...]) -> dict[str, tuple[str, ...]]:
    reasons: dict[str, set[str]] = {}
    stale_kinds = {
        "field_key_status_changed",
        "field_nullability_changed",
        "field_removed",
        "field_type_changed",
        "object_kind_changed",
        "object_removed",
        "primary_key_changed",
        "possible_field_rename",
    }
    for change in changes:
        if change.entity_type == "node" and change.kind in stale_kinds:
            reasons.setdefault(change.target_id, set()).add(change.kind)
    return {key: tuple(sorted(value)) for key, value in reasons.items()}


def _annotation_claim(
    node: GraphNode,
    graph: GraphDocument,
    *,
    previous_state: str,
    present: bool,
    reasons: tuple[str, ...],
) -> StaleClaim:
    annotation = node.annotation
    if annotation is None:
        raise GraphFailure("invalid_stale_claim", "Annotated stale claim has no annotation.")
    claim: dict[str, object] = {"annotation": annotation.to_dict()}
    for key in ("grain", "semantic_type", "annotation_review", "change_review"):
        if key in node.metadata:
            claim[key] = node.metadata[key]
    return StaleClaim(
        claim_type="annotation",
        target_id=node.id,
        reference=node_reference(node, graph),
        previous_state=previous_state,
        present=present,
        reasons=reasons,
        claim=claim,
    )


def _relationship_claim(
    edge: GraphEdge,
    *,
    present: bool,
    reasons: tuple[str, ...],
    previous_state: str | None = None,
) -> StaleClaim:
    state = previous_state or str(edge.metadata.get("state") or "draft")
    return StaleClaim(
        claim_type="relationship",
        target_id=edge.id,
        reference=str(edge.metadata.get("name") or edge.id),
        previous_state=state,
        present=present,
        reasons=reasons,
        claim={"edge": edge.to_dict()},
    )


def _candidate_endpoints_exist(graph: GraphDocument, edge: GraphEdge) -> bool:
    nodes = graph.node_by_id()
    if edge.source_id not in nodes or edge.target_id not in nodes:
        return False
    metadata = edge.metadata
    return _field_exists(
        graph,
        edge.source_id,
        str(metadata.get("from_field") or ""),
    ) and _field_exists(graph, edge.target_id, str(metadata.get("to_field") or ""))


def _candidate_field_ids(graph: GraphDocument, edge: GraphEdge) -> set[str]:
    fields: set[str] = set()
    for object_id, key in (
        (edge.source_id, "from_field"),
        (edge.target_id, "to_field"),
    ):
        label = str(edge.metadata.get(key) or "")
        fields.update(
            node.id
            for node in graph.nodes
            if node.type == "field"
            and node.metadata.get("object_id") == object_id
            and node.label == label
        )
    return fields


def _field_exists(graph: GraphDocument, object_id: str, field_name: str) -> bool:
    return any(
        node.type == "field"
        and node.metadata.get("object_id") == object_id
        and node.label == field_name
        for node in graph.nodes
    )


def _same_field_pair(candidate: GraphEdge, declared: GraphEdge) -> bool:
    if candidate.source_id != declared.source_id or candidate.target_id != declared.target_id:
        return False
    return declared.metadata.get("from_fields") == [
        candidate.metadata.get("from_field")
    ] and declared.metadata.get("to_fields") == [candidate.metadata.get("to_field")]


def _report_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise GraphFailure("invalid_change_report", f"Report field must be a string: {key}")
    return value


def _report_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GraphFailure("invalid_change_report", f"Report field must be an integer: {key}")
    return value
