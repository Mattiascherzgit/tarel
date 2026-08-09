"""Small deterministic relationship operations over a TAREL graph."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace

from tarel.connectors.contracts import RelationshipPair, RelationshipPairProfile
from tarel.graph.contracts import GraphDocument, GraphEdge, GraphNode


class RelationshipFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ResolvedField:
    object_node: GraphNode
    field_node: GraphNode

    @property
    def reference(self) -> str:
        return f"{self.object_node.label}.{self.field_node.label}"


def relationship_pair(
    graph: GraphDocument,
    from_reference: str,
    to_reference: str,
) -> RelationshipPair:
    source = resolve_field(graph, from_reference)
    target = resolve_field(graph, to_reference)
    if source.field_node.id == target.field_node.id:
        raise RelationshipFailure(
            "invalid_relationship_pair",
            "Relationship endpoints must be different fields.",
        )
    return _pair(source, target)


def add_manual_relationship(
    graph: GraphDocument,
    *,
    pair: RelationshipPair,
    reason: str,
    validated: bool,
) -> tuple[GraphDocument, GraphEdge]:
    if not reason.strip():
        raise RelationshipFailure(
            "missing_relationship_reason",
            "A human relationship requires a non-empty reason.",
        )
    _ensure_pair_is_new(graph, pair)
    source = resolve_field(graph, _from_reference(pair))
    target = resolve_field(graph, _to_reference(pair))
    edge = _candidate_edge(
        source,
        target,
        origin="human",
        state="validated" if validated else "draft",
        reason=reason.strip(),
        profile=None,
    )
    return replace(graph, edges=(*graph.edges, edge)), edge


def candidate_pairs(
    graph: GraphDocument,
    *,
    object_reference: str,
    field_name: str | None,
    max_pairs: int,
    allowed_object_ids: frozenset[str] | None = None,
) -> tuple[RelationshipPair, ...]:
    if not 1 <= max_pairs <= 50:
        raise RelationshipFailure(
            "invalid_pair_budget",
            "Relationship discovery pair budget must be between 1 and 50.",
        )
    source_object = resolve_object(graph, object_reference)
    if allowed_object_ids is not None and source_object.id not in allowed_object_ids:
        raise RelationshipFailure(
            "object_outside_focus",
            f"Object is outside the selected focus: {source_object.label}",
        )
    fields_by_object = _fields_by_object(graph)
    source_fields = fields_by_object.get(source_object.id, [])
    if field_name:
        source_fields = [
            field for field in source_fields if field.label.lower() == field_name.lower()
        ]
        if not source_fields:
            raise RelationshipFailure(
                "field_not_found",
                f"Field not found on {source_object.label}: {field_name}",
            )
    source_fields = sorted(source_fields, key=_field_position)[:16]

    target_fields = [
        field
        for object_node in _object_nodes(graph)
        if allowed_object_ids is None or object_node.id in allowed_object_ids
        for field in fields_by_object.get(object_node.id, [])
        if bool(field.metadata.get("is_primary_key")) or _field_position(field) == 1
    ]
    grouped: list[list[tuple[tuple[object, ...], RelationshipPair]]] = []
    for source_field in source_fields:
        source_family = _type_family(str(source_field.metadata.get("data_type") or ""))
        if source_family in {"bool", "binary", "unknown"}:
            continue
        candidates: list[tuple[tuple[object, ...], RelationshipPair]] = []
        for target_field in target_fields:
            if source_field.id == target_field.id:
                continue
            target_family = _type_family(str(target_field.metadata.get("data_type") or ""))
            if source_family != target_family:
                continue
            target_object = graph.node_by_id()[str(target_field.metadata["object_id"])]
            pair = _pair(
                ResolvedField(source_object, source_field),
                ResolvedField(target_object, target_field),
            )
            if _pair_exists(graph, pair):
                continue
            rank = (
                not bool(target_field.metadata.get("is_primary_key")),
                _normalized_name(source_field.label) != _normalized_name(target_field.label),
                str(source_field.metadata.get("data_type"))
                != str(target_field.metadata.get("data_type")),
                target_object.label,
                target_field.label,
            )
            candidates.append((rank, pair))
        candidates.sort(key=lambda item: item[0])
        if candidates:
            grouped.append(candidates)

    selected: list[RelationshipPair] = []
    target_index = 0
    while len(selected) < max_pairs:
        added = False
        for candidates in grouped:
            if target_index >= len(candidates):
                continue
            selected.append(candidates[target_index][1])
            added = True
            if len(selected) >= max_pairs:
                break
        if not added:
            break
        target_index += 1
    return tuple(selected)


def add_profile_candidates(
    graph: GraphDocument,
    profiles: tuple[RelationshipPairProfile, ...],
    *,
    min_source_coverage: float,
    min_overlap_count: int,
    min_target_uniqueness: float,
) -> tuple[GraphDocument, tuple[GraphEdge, ...]]:
    if not 0.0 <= min_source_coverage <= 1.0:
        raise RelationshipFailure("invalid_threshold", "Source coverage must be between 0 and 1.")
    if min_overlap_count < 1:
        raise RelationshipFailure("invalid_threshold", "Minimum overlap count must be positive.")
    if not 0.0 <= min_target_uniqueness <= 1.0:
        raise RelationshipFailure("invalid_threshold", "Target uniqueness must be between 0 and 1.")

    qualifying = [
        profile
        for profile in profiles
        if profile.overlap_count >= min_overlap_count
        and profile.source_coverage >= min_source_coverage
        and profile.target_uniqueness >= min_target_uniqueness
        and not _pair_exists(graph, profile.pair)
    ]
    best_by_source: dict[tuple[str, str, str], RelationshipPairProfile] = {}
    for profile in qualifying:
        key = (
            profile.pair.from_namespace,
            profile.pair.from_object,
            profile.pair.from_field,
        )
        current_best = best_by_source.get(key)
        if current_best is None or _profile_rank(profile) > _profile_rank(current_best):
            best_by_source[key] = profile

    edges: list[GraphEdge] = []
    current = graph
    for profile in sorted(
        best_by_source.values(),
        key=lambda item: (
            item.pair.from_namespace,
            item.pair.from_object,
            item.pair.from_field,
            item.pair.to_namespace,
            item.pair.to_object,
            item.pair.to_field,
        ),
    ):
        source = resolve_field(current, _from_reference(profile.pair))
        target = resolve_field(current, _to_reference(profile.pair))
        edge = _candidate_edge(
            source,
            target,
            origin="profile_probe",
            state="draft",
            reason="Bounded value-domain overlap suggests a possible join.",
            profile=profile,
        )
        current = replace(current, edges=(*current.edges, edge))
        edges.append(edge)
    return current, tuple(edges)


def relationship_candidates(graph: GraphDocument) -> tuple[GraphEdge, ...]:
    return tuple(edge for edge in graph.edges if edge.type == "relationship_candidate")


def decide_relationship(
    graph: GraphDocument,
    *,
    edge_id: str,
    state: str,
    reason: str,
) -> tuple[GraphDocument, GraphEdge]:
    if state not in {"validated", "rejected"}:
        raise RelationshipFailure("invalid_relationship_state", f"Unsupported state: {state}")
    if not reason.strip():
        raise RelationshipFailure(
            "missing_relationship_reason",
            "A relationship decision requires a non-empty reason.",
        )
    selected = next(
        (
            edge
            for edge in graph.edges
            if edge.id == edge_id and edge.type == "relationship_candidate"
        ),
        None,
    )
    if selected is None:
        raise RelationshipFailure(
            "relationship_not_found",
            f"Relationship candidate not found: {edge_id}",
        )
    metadata = dict(selected.metadata)
    metadata.pop("change_review", None)
    metadata["state"] = state
    metadata["review"] = {"reason": reason.strip(), "source": "human"}
    updated_edge = replace(selected, metadata=metadata)
    updated_edges = tuple(updated_edge if edge.id == edge_id else edge for edge in graph.edges)
    return replace(graph, edges=updated_edges), updated_edge


def usable_relationships(graph: GraphDocument) -> tuple[GraphEdge, ...]:
    return tuple(
        edge
        for edge in graph.edges
        if edge.type == "foreign_key"
        or (
            edge.type == "relationship_candidate"
            and edge.metadata.get("state") == "validated"
        )
    )


def resolve_object(graph: GraphDocument, reference: str) -> GraphNode:
    normalized = reference.strip().lower()
    matches = [
        node
        for node in _object_nodes(graph)
        if node.label.lower() == normalized
        or str(node.metadata.get("name") or "").lower() == normalized
    ]
    if len(matches) != 1:
        code = "object_not_found" if not matches else "ambiguous_object"
        raise RelationshipFailure(code, f"Could not resolve one graph object: {reference}")
    return matches[0]


def resolve_field(graph: GraphDocument, reference: str) -> ResolvedField:
    normalized = reference.strip().lower()
    fields_by_object = _fields_by_object(graph)
    matches: list[ResolvedField] = []
    for object_node in _object_nodes(graph):
        for field_node in fields_by_object.get(object_node.id, []):
            if f"{object_node.label}.{field_node.label}".lower() == normalized:
                matches.append(ResolvedField(object_node, field_node))
    if len(matches) != 1:
        code = "field_not_found" if not matches else "ambiguous_field"
        raise RelationshipFailure(code, f"Could not resolve one graph field: {reference}")
    return matches[0]


def _candidate_edge(
    source: ResolvedField,
    target: ResolvedField,
    *,
    origin: str,
    state: str,
    reason: str,
    profile: RelationshipPairProfile | None,
) -> GraphEdge:
    pair = _pair(source, target)
    digest = hashlib.sha256(
        f"{source.field_node.id}\n{target.field_node.id}".encode()
    ).hexdigest()[:20]
    metadata: dict[str, object] = {
        **pair.to_dict(),
        "candidate_kind": "join_candidate",
        "origin": origin,
        "reason": reason,
        "state": state,
    }
    if profile is not None:
        confidence = 0.2 + (0.35 * profile.source_coverage)
        confidence += 0.2 * profile.target_uniqueness
        confidence += 0.15 * profile.target_coverage
        confidence += min(0.1, profile.overlap_count / 100)
        metadata.update(
            {
                "confidence": round(min(0.98, confidence), 4),
                "overlap_count": profile.overlap_count,
                "profile_row_limit": profile.profile_row_limit,
                "source_coverage": round(profile.source_coverage, 6),
                "source_distinct_count": profile.source_distinct_count,
                "source_non_null_count": profile.source_non_null_count,
                "target_coverage": round(profile.target_coverage, 6),
                "target_distinct_count": profile.target_distinct_count,
                "target_non_null_count": profile.target_non_null_count,
                "target_uniqueness": round(profile.target_uniqueness, 6),
            }
        )
    return GraphEdge(
        id=f"relationship_candidate:{digest}",
        source_id=source.object_node.id,
        target_id=target.object_node.id,
        type="relationship_candidate",
        metadata=metadata,
    )


def _ensure_pair_is_new(graph: GraphDocument, pair: RelationshipPair) -> None:
    if _pair_exists(graph, pair):
        raise RelationshipFailure(
            "relationship_exists",
            f"Relationship already exists: {_from_reference(pair)} -> {_to_reference(pair)}",
        )


def _pair_exists(graph: GraphDocument, pair: RelationshipPair) -> bool:
    for edge in graph.edges:
        metadata = edge.metadata
        if edge.type in {"foreign_key", "relationship_candidate"} and (
            metadata.get("from_fields") == [pair.from_field]
            and metadata.get("to_fields") == [pair.to_field]
            and graph.node_by_id()[edge.source_id].label
            == f"{pair.from_namespace}.{pair.from_object}"
            and graph.node_by_id()[edge.target_id].label == f"{pair.to_namespace}.{pair.to_object}"
        ):
            return True
        if edge.type == "relationship_candidate" and all(
            metadata.get(key) == value for key, value in pair.to_dict().items()
        ):
            return True
    return False


def _pair(source: ResolvedField, target: ResolvedField) -> RelationshipPair:
    return RelationshipPair(
        from_namespace=str(source.object_node.metadata["namespace"]),
        from_object=str(source.object_node.metadata["name"]),
        from_field=source.field_node.label,
        to_namespace=str(target.object_node.metadata["namespace"]),
        to_object=str(target.object_node.metadata["name"]),
        to_field=target.field_node.label,
    )


def _from_reference(pair: RelationshipPair) -> str:
    return f"{pair.from_namespace}.{pair.from_object}.{pair.from_field}"


def _to_reference(pair: RelationshipPair) -> str:
    return f"{pair.to_namespace}.{pair.to_object}.{pair.to_field}"


def _object_nodes(graph: GraphDocument) -> list[GraphNode]:
    return [node for node in graph.nodes if node.type in {"table", "view"}]


def _fields_by_object(graph: GraphDocument) -> dict[str, list[GraphNode]]:
    result: dict[str, list[GraphNode]] = {}
    for node in graph.nodes:
        if node.type == "field":
            result.setdefault(str(node.metadata.get("object_id")), []).append(node)
    return result


def _field_position(field: GraphNode) -> int:
    return int(field.metadata.get("position") or 9999)


def _profile_rank(profile: RelationshipPairProfile) -> tuple[float, float, float, int]:
    return (
        profile.source_coverage,
        profile.target_uniqueness,
        profile.target_coverage,
        profile.overlap_count,
    )


def _normalized_name(value: str) -> str:
    return re.sub(r"(?:^|_)(?:id|key)$", "", value.lower()).replace("_", "")


def _type_family(data_type: str) -> str:
    name = data_type.lower().split("(", 1)[0]
    if name in {"bit", "boolean"}:
        return "bool"
    if name in {"binary", "image", "rowversion", "timestamp", "varbinary"}:
        return "binary"
    if name in {
        "bigint",
        "decimal",
        "float",
        "int",
        "integer",
        "money",
        "numeric",
        "real",
        "smallint",
        "smallmoney",
        "tinyint",
    }:
        return "number"
    if name in {"date", "datetime", "datetime2", "datetimeoffset", "smalldatetime", "time"}:
        return "date"
    if name in {"char", "nchar", "ntext", "nvarchar", "text", "uniqueidentifier", "varchar"}:
        return "text"
    return "unknown"
