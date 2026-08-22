"""Application use cases for auditable entity-resolution candidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tarel.entity_resolution.contracts import (
    ENTITY_RESOLUTION_MODES,
    EntityResolutionCandidate,
    EntityResolutionFailure,
    EntityResolutionMatch,
    review_candidate,
    validate_entity_resolution_candidate,
)
from tarel.entity_resolution.store import FileEntityResolutionStore
from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.graph.revision import graph_revision
from tarel.graph.store import FileGraphStore
from tarel.relationships.core import RelationshipFailure, resolve_field
from tarel.runtime import TarelRuntime


@dataclass(frozen=True, slots=True)
class EntityResolutionChangeResult:
    candidate: EntityResolutionCandidate
    path: Path
    changed: bool


def import_entity_resolution_candidate_use_case(
    candidate: EntityResolutionCandidate,
    *,
    runtime: TarelRuntime | None = None,
) -> EntityResolutionChangeResult:
    validate_entity_resolution_candidate(candidate)
    if candidate.state != "candidate" or candidate.review is not None:
        raise EntityResolutionFailure(
            "invalid_entity_resolution_import",
            "New entity-resolution imports must be unreviewed candidates.",
        )
    graph = _graph_store(runtime).load(candidate.graph_name)
    _validate_candidate_binding(candidate, graph)
    store = _entity_store(runtime)
    if store.exists(candidate.id):
        current = store.load(candidate.id)
        if current == candidate:
            return EntityResolutionChangeResult(
                candidate=current,
                path=store.path(candidate.id),
                changed=False,
            )
        raise EntityResolutionFailure(
            "entity_resolution_exists",
            f"Entity-resolution candidate already exists: {candidate.id}",
        )
    return EntityResolutionChangeResult(
        candidate=candidate,
        path=store.save(candidate),
        changed=True,
    )


def load_entity_resolution_candidate_use_case(
    candidate_id: str,
    *,
    runtime: TarelRuntime | None = None,
) -> EntityResolutionCandidate:
    return _entity_store(runtime).load(candidate_id)


def list_entity_resolution_candidates_use_case(
    *,
    graph_name: str | None = None,
    runtime: TarelRuntime | None = None,
) -> tuple[EntityResolutionCandidate, ...]:
    store = _entity_store(runtime)
    candidates = tuple(store.load(candidate_id) for candidate_id in store.list())
    if graph_name is not None:
        candidates = tuple(item for item in candidates if item.graph_name == graph_name)
    return candidates


def find_entity_resolution_candidates_use_case(
    graph_name: str,
    *,
    source: str | None = None,
    target: str | None = None,
    mode: str = "confirmed_then_candidates",
    runtime: TarelRuntime | None = None,
) -> tuple[EntityResolutionMatch, ...]:
    graph = _graph_store(runtime).load(graph_name)
    return find_entity_resolution_candidates_for_graph_use_case(
        graph,
        source=source,
        target=target,
        mode=mode,
        runtime=runtime,
    )


def find_entity_resolution_candidates_for_graph_use_case(
    graph: GraphDocument,
    *,
    source: str | None = None,
    target: str | None = None,
    mode: str = "confirmed_then_candidates",
    runtime: TarelRuntime | None = None,
) -> tuple[EntityResolutionMatch, ...]:
    if mode not in ENTITY_RESOLUTION_MODES:
        raise EntityResolutionFailure(
            "invalid_entity_resolution_mode",
            f"Unsupported entity-resolution retrieval mode: {mode}",
        )
    source_id = _resolve_optional_field(graph, source)
    target_id = _resolve_optional_field(graph, target)
    candidates = [
        item
        for item in list_entity_resolution_candidates_use_case(
            graph_name=graph.name,
            runtime=runtime,
        )
        if item.graph_revision == graph_revision(graph)
        and item.state != "rejected"
        and (source_id is None or item.source_field_id == source_id)
        and (target_id is None or item.target_field_id == target_id)
    ]
    if mode == "confirmed_only":
        candidates = [item for item in candidates if item.state == "reviewed"]
    elif mode == "confirmed_then_candidates":
        reviewed_pairs = {
            _field_pair(item)
            for item in candidates
            if item.state == "reviewed"
        }
        candidates = [
            item
            for item in candidates
            if item.state == "reviewed"
            or _field_pair(item) not in reviewed_pairs
        ]
    return tuple(
        _match(graph, item)
        for item in sorted(candidates, key=lambda candidate: candidate.id)
    )


def decide_entity_resolution_candidate_use_case(
    candidate_id: str,
    *,
    decision: str,
    reason: str,
    expected_revision: str | None = None,
    runtime: TarelRuntime | None = None,
) -> EntityResolutionChangeResult:
    store = _entity_store(runtime)
    candidate = store.load(candidate_id)
    if expected_revision is not None and candidate.revision != expected_revision:
        raise EntityResolutionFailure(
            "stale_entity_resolution_candidate",
            "The candidate changed after it was loaded. Reload it before reviewing.",
        )
    graph = _graph_store(runtime).load(candidate.graph_name)
    _validate_candidate_binding(candidate, graph)
    changed = review_candidate(candidate, decision=decision, reason=reason)
    return EntityResolutionChangeResult(
        candidate=changed,
        path=store.save(changed),
        changed=True,
    )


def _validate_candidate_binding(
    candidate: EntityResolutionCandidate,
    graph: GraphDocument,
) -> None:
    if candidate.graph_revision != graph_revision(graph):
        raise EntityResolutionFailure(
            "entity_resolution_graph_revision_mismatch",
            "The entity-resolution candidate does not match the current graph revision.",
        )
    source = _field_by_id(graph, candidate.source_field_id)
    target = _field_by_id(graph, candidate.target_field_id)
    if source.id == target.id:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "Entity-resolution endpoints must be different fields.",
        )


def _match(
    graph: GraphDocument,
    candidate: EntityResolutionCandidate,
) -> EntityResolutionMatch:
    return EntityResolutionMatch(
        candidate=candidate,
        source_reference=_field_reference(graph, candidate.source_field_id),
        target_reference=_field_reference(graph, candidate.target_field_id),
    )


def _field_pair(candidate: EntityResolutionCandidate) -> tuple[str, str]:
    first, second = sorted((candidate.source_field_id, candidate.target_field_id))
    return first, second


def _field_by_id(graph: GraphDocument, field_id: str) -> GraphNode:
    node = graph.node_by_id().get(field_id)
    if node is None or node.type != "field":
        raise EntityResolutionFailure(
            "entity_resolution_field_not_found",
            f"Entity-resolution endpoint is not a current graph field: {field_id}",
        )
    return node


def _field_reference(graph: GraphDocument, field_id: str) -> str:
    field = _field_by_id(graph, field_id)
    parent = graph.node_by_id().get(str(field.metadata.get("object_id") or ""))
    if parent is None or parent.type not in {"table", "view"}:
        raise EntityResolutionFailure(
            "entity_resolution_field_not_found",
            f"Entity-resolution field has no current graph object: {field_id}",
        )
    return f"{parent.label}.{field.label}"


def _resolve_optional_field(graph: GraphDocument, reference: str | None) -> str | None:
    if reference is None:
        return None
    try:
        return resolve_field(graph, reference).field_node.id
    except RelationshipFailure as exc:
        raise EntityResolutionFailure(
            "entity_resolution_field_not_found",
            str(exc),
        ) from exc


def _graph_store(runtime: TarelRuntime | None) -> FileGraphStore:
    return FileGraphStore() if runtime is None else runtime.graph_store()


def _entity_store(runtime: TarelRuntime | None) -> FileEntityResolutionStore:
    return FileEntityResolutionStore() if runtime is None else runtime.entity_resolution_store()
