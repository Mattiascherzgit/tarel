"""Compile bounded agent context from lexical search and reviewed graph relationships."""

from __future__ import annotations

from dataclasses import dataclass, replace

from tarel.annotations.states import (
    DEFAULT_CONTEXT_ANNOTATION_STATES,
    annotation_is_visible,
)
from tarel.context_output import (
    DEFAULT_MAX_CONTEXT_CHARACTERS,
    ContextField,
    ContextJoin,
    ContextObject,
    ContextOmissions,
    ContextPath,
    ContextResult,
    ContextScope,
    canonical_json,
)
from tarel.graph.contracts import GraphDocument, GraphEdge, GraphNode
from tarel.graph.revision import graph_revision
from tarel.relationships.core import usable_relationships
from tarel.search import SearchHit, SearchResults, search_graph


class ContextFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class _Selection:
    kind: str
    distance: int
    seed_id: str
    object_ids: tuple[str, ...]
    join_ids: tuple[str, ...]


def compile_context(
    graph: GraphDocument,
    query: str,
    *,
    namespace: str | None = None,
    seed_limit: int = 3,
    max_objects: int = 10,
    max_joins: int = 12,
    max_hops: int = 2,
    max_fields_per_object: int = 12,
    max_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS,
    annotation_states: frozenset[str] = DEFAULT_CONTEXT_ANNOTATION_STATES,
) -> ContextResult:
    """Build context without querying source data or invoking an LLM."""
    search = search_graph(
        graph,
        query,
        limit=100,
        namespace=namespace,
        annotation_states=annotation_states,
    )
    return compile_context_from_search(
        graph,
        search,
        namespace=namespace,
        seed_limit=seed_limit,
        max_objects=max_objects,
        max_joins=max_joins,
        max_hops=max_hops,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
        annotation_states=annotation_states,
    )


def compile_context_from_search(
    graph: GraphDocument,
    search: SearchResults,
    *,
    namespace: str | None = None,
    seed_limit: int = 3,
    max_objects: int = 10,
    max_joins: int = 12,
    max_hops: int = 2,
    max_fields_per_object: int = 12,
    max_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS,
    annotation_states: frozenset[str] = DEFAULT_CONTEXT_ANNOTATION_STATES,
    scope: ContextScope | None = None,
) -> ContextResult:
    """Expand caller-supplied retrieval anchors through reviewed graph relationships."""
    _validate_budgets(
        seed_limit=seed_limit,
        max_objects=max_objects,
        max_joins=max_joins,
        max_hops=max_hops,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
    )
    if search.graph != graph.name:
        raise ContextFailure("graph_mismatch", "Search results belong to a different graph.")
    search_by_id = {hit.id: hit for hit in search.hits}
    seeds = search.hits[:seed_limit]
    node_by_id = graph.node_by_id()
    joins = tuple(_context_join(edge, node_by_id) for edge in usable_relationships(graph))
    adjacency = _adjacency(joins)

    selected: dict[str, _Selection] = {
        hit.id: _Selection(
            kind="search",
            distance=0,
            seed_id=hit.id,
            object_ids=(hit.id,),
            join_ids=(),
        )
        for hit in seeds
    }
    selected_join_ids: list[str] = []
    paths: list[ContextPath] = []
    frontier = [selected[hit.id] for hit in seeds]

    for distance in range(1, max_hops + 1):
        candidates: list[tuple[tuple[object, ...], str, ContextJoin, _Selection]] = []
        for current in frontier:
            current_id = current.object_ids[-1]
            for neighbor_id, join, direction_rank in adjacency.get(current_id, ()):
                if neighbor_id in selected:
                    continue
                hit = search_by_id.get(neighbor_id)
                rank = (
                    -(hit.score if hit else 0),
                    _join_rank(join),
                    direction_rank,
                    node_by_id[neighbor_id].label.casefold(),
                    join.id,
                    node_by_id[current.seed_id].label.casefold(),
                )
                candidates.append((rank, neighbor_id, join, current))

        next_frontier: list[_Selection] = []
        for _rank, neighbor_id, join, current in sorted(candidates, key=lambda item: item[0]):
            if neighbor_id in selected:
                continue
            if len(selected) >= max_objects or len(selected_join_ids) >= max_joins:
                break
            selection = _Selection(
                kind="relationship",
                distance=distance,
                seed_id=current.seed_id,
                object_ids=(*current.object_ids, neighbor_id),
                join_ids=(*current.join_ids, join.id),
            )
            selected[neighbor_id] = selection
            selected_join_ids.append(join.id)
            next_frontier.append(selection)
            paths.append(
                ContextPath(
                    seed=node_by_id[selection.seed_id].label,
                    target=node_by_id[neighbor_id].label,
                    objects=tuple(node_by_id[item].label for item in selection.object_ids),
                    joins=selection.join_ids,
                )
            )
        frontier = next_frontier
        if not frontier or len(selected) >= max_objects or len(selected_join_ids) >= max_joins:
            break

    included_joins, omitted_joins = _included_joins(
        joins,
        selected_ids=set(selected),
        traversal_ids=selected_join_ids,
        seed_ids={hit.id for hit in seeds},
        search_scores={object_id: hit.score for object_id, hit in search_by_id.items()},
        limit=max_joins,
    )
    objects = _context_objects(
        graph,
        selected,
        seeds=seeds,
        search_by_id=search_by_id,
        joins=included_joins,
        max_fields=max_fields_per_object,
        annotation_states=annotation_states,
    )
    omitted_objects = max(0, len(search.hits) - len(objects))
    omitted_fields = sum(item.omitted_fields for item in objects)
    omissions = _context_omissions(
        objects=omitted_objects,
        fields=omitted_fields,
        joins=omitted_joins,
    )
    result = ContextResult(
        graph=graph.name,
        graph_revision=graph_revision(graph),
        scope=scope or ContextScope(namespace=namespace),
        query=search.query,
        terms=search.terms,
        objects=objects,
        joins=included_joins,
        paths=tuple(paths),
        seed_limit=seed_limit,
        max_objects=max_objects,
        max_joins=max_joins,
        max_hops=max_hops,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
        stable_characters=0,
        context_characters=0,
        omissions=omissions,
        retrieval_mode=search.mode,
        annotation_states=annotation_states,
    )
    return _fit_character_budget(result)


def compile_context_prefix(
    graph: GraphDocument,
    *,
    namespace: str | None = None,
    max_objects: int = 250,
    max_joins: int = 500,
    max_fields_per_object: int = 50,
    max_characters: int = 500_000,
    annotation_states: frozenset[str] = DEFAULT_CONTEXT_ANNOTATION_STATES,
    scope: ContextScope | None = None,
) -> ContextResult:
    """Compile one query-independent graph or workspace scope for prompt-prefix reuse."""
    _validate_prefix_budgets(
        max_objects=max_objects,
        max_joins=max_joins,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
    )
    eligible_nodes = sorted(
        (
            node
            for node in graph.nodes
            if node.type in {"table", "view"}
            and (
                namespace is None
                or str(node.metadata.get("namespace") or "").casefold() == namespace.casefold()
            )
        ),
        key=lambda node: (node.label.casefold(), node.id),
    )
    if not eligible_nodes:
        raise ContextFailure(
            "empty_context_scope",
            "The selected context prefix scope contains no tables or views.",
        )
    selected_nodes = eligible_nodes[:max_objects]
    selected_ids = {node.id for node in selected_nodes}
    node_by_id = graph.node_by_id()
    eligible_joins = tuple(
        sorted(
            (
                _context_join(edge, node_by_id)
                for edge in usable_relationships(graph)
                if edge.source_id in selected_ids and edge.target_id in selected_ids
            ),
            key=lambda join: (
                _join_rank(join),
                join.from_object.casefold(),
                join.to_object.casefold(),
                join.id,
            ),
        )
    )
    joins = eligible_joins[:max_joins]
    selected = {
        node.id: _Selection(
            kind="scope",
            distance=0,
            seed_id=node.id,
            object_ids=(node.id,),
            join_ids=(),
        )
        for node in selected_nodes
    }
    objects = _context_objects(
        graph,
        selected,
        seeds=(),
        search_by_id={},
        joins=joins,
        max_fields=max_fields_per_object,
        annotation_states=annotation_states,
    )
    result = ContextResult(
        graph=graph.name,
        graph_revision=graph_revision(graph),
        scope=scope or ContextScope(mode="graph_prefix", namespace=namespace),
        query="",
        terms=(),
        objects=objects,
        joins=joins,
        paths=(),
        seed_limit=1,
        max_objects=max_objects,
        max_joins=max_joins,
        max_hops=0,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
        stable_characters=0,
        context_characters=0,
        omissions=_context_omissions(
            objects=max(0, len(eligible_nodes) - len(selected_nodes)),
            fields=sum(item.omitted_fields for item in objects),
            joins=max(0, len(eligible_joins) - len(joins)),
        ),
        retrieval_mode="scope",
        annotation_states=annotation_states,
    )
    return _fit_character_budget(result)


def _validate_budgets(
    *,
    seed_limit: int,
    max_objects: int,
    max_joins: int,
    max_hops: int,
    max_fields_per_object: int,
    max_characters: int,
) -> None:
    if not 1 <= seed_limit <= 20:
        raise ContextFailure("invalid_context_budget", "Seed limit must be between 1 and 20.")
    if not 1 <= max_objects <= 50:
        raise ContextFailure("invalid_context_budget", "Object budget must be between 1 and 50.")
    if seed_limit > max_objects:
        raise ContextFailure("invalid_context_budget", "Seed limit cannot exceed object budget.")
    if not 0 <= max_joins <= 100:
        raise ContextFailure("invalid_context_budget", "Join budget must be between 0 and 100.")
    if not 0 <= max_hops <= 4:
        raise ContextFailure("invalid_context_budget", "Hop budget must be between 0 and 4.")
    if not 1 <= max_fields_per_object <= 100:
        raise ContextFailure(
            "invalid_context_budget",
            "Field budget must be between 1 and 100 per object.",
        )
    if not 1_000 <= max_characters <= 1_000_000:
        raise ContextFailure(
            "invalid_context_budget",
            "Stable context character budget must be between 1000 and 1000000.",
        )


def _validate_prefix_budgets(
    *,
    max_objects: int,
    max_joins: int,
    max_fields_per_object: int,
    max_characters: int,
) -> None:
    if not 1 <= max_objects <= 5_000:
        raise ContextFailure(
            "invalid_context_budget",
            "Prefix object budget must be between 1 and 5000.",
        )
    if not 0 <= max_joins <= 10_000:
        raise ContextFailure(
            "invalid_context_budget",
            "Prefix join budget must be between 0 and 10000.",
        )
    if not 1 <= max_fields_per_object <= 100:
        raise ContextFailure(
            "invalid_context_budget",
            "Prefix field budget must be between 1 and 100 per object.",
        )
    if not 1_000 <= max_characters <= 10_000_000:
        raise ContextFailure(
            "invalid_context_budget",
            "Prefix character budget must be between 1000 and 10000000.",
        )


def _context_omissions(
    *,
    objects: int = 0,
    fields: int = 0,
    joins: int = 0,
    paths: int = 0,
) -> ContextOmissions:
    reasons: list[str] = []
    if objects:
        reasons.append("object_budget")
    if fields:
        reasons.append("field_budget")
    if joins:
        reasons.append("join_budget")
    if paths:
        reasons.append("path_budget")
    return ContextOmissions(
        objects=objects,
        fields=fields,
        joins=joins,
        paths=paths,
        reasons=tuple(reasons),
    )


def _fit_character_budget(result: ContextResult) -> ContextResult:
    current = _with_character_counts(result)
    while current.context_characters > current.max_characters:
        trimmed = _trim_context(current)
        if trimmed is None:
            raise ContextFailure(
                "context_character_budget_too_small",
                "Context cannot fit the character budget without dropping the only object.",
            )
        current = _with_character_counts(trimmed)
    return current


def _trim_context(result: ContextResult) -> ContextResult | None:
    objects = list(result.objects)
    for index in range(len(objects) - 1, -1, -1):
        item = objects[index]
        if not item.fields:
            continue
        objects[index] = replace(
            item,
            fields=item.fields[:-1],
            omitted_fields=item.omitted_fields + 1,
        )
        return replace(
            result,
            objects=tuple(objects),
            omissions=_increment_omissions(result.omissions, fields=1),
        )

    if result.paths:
        return replace(
            result,
            paths=result.paths[:-1],
            omissions=_increment_omissions(result.omissions, paths=1),
        )

    if result.joins:
        removed = result.joins[-1]
        paths = tuple(path for path in result.paths if removed.id not in path.joins)
        return replace(
            result,
            joins=result.joins[:-1],
            paths=paths,
            omissions=_increment_omissions(
                result.omissions,
                joins=1,
                paths=len(result.paths) - len(paths),
            ),
        )

    if len(objects) > 1:
        removed = objects.pop()
        joins = tuple(
            join
            for join in result.joins
            if removed.id not in {join.from_object_id, join.to_object_id}
        )
        paths = tuple(path for path in result.paths if removed.label not in path.objects)
        return replace(
            result,
            objects=tuple(objects),
            joins=joins,
            paths=paths,
            omissions=_increment_omissions(
                result.omissions,
                objects=1,
                joins=len(result.joins) - len(joins),
                paths=len(result.paths) - len(paths),
            ),
        )
    return None


def _increment_omissions(
    omissions: ContextOmissions,
    *,
    objects: int = 0,
    fields: int = 0,
    joins: int = 0,
    paths: int = 0,
) -> ContextOmissions:
    reasons = list(omissions.reasons)
    if "character_budget" not in reasons:
        reasons.append("character_budget")
    return ContextOmissions(
        objects=omissions.objects + objects,
        fields=omissions.fields + fields,
        joins=omissions.joins + joins,
        paths=omissions.paths + paths,
        reasons=tuple(reasons),
    )


def _stable_character_count(result: ContextResult) -> int:
    return len(canonical_json(result.stable_dict()))


def _with_character_counts(result: ContextResult) -> ContextResult:
    current = replace(
        result,
        stable_characters=_stable_character_count(result),
        context_characters=0,
    )
    for _iteration in range(4):
        measured = _context_character_count(current)
        if measured == current.context_characters:
            return current
        current = replace(current, context_characters=measured)
    return current


def _context_character_count(result: ContextResult) -> int:
    return len(result.canonical_json())


def _context_join(edge: GraphEdge, node_by_id: dict[str, GraphNode]) -> ContextJoin:
    source = node_by_id.get(edge.source_id)
    target = node_by_id.get(edge.target_id)
    if source is None or target is None:
        raise ContextFailure(
            "invalid_relationship",
            f"Relationship has a missing endpoint: {edge.id}",
        )
    if edge.type == "foreign_key":
        from_fields = _field_names(edge.metadata.get("from_fields"), edge.id)
        to_fields = _field_names(edge.metadata.get("to_fields"), edge.id)
        state = "declared"
        origin = "source"
        reason = None
        confidence = None
        kind = "foreign_key"
        transformation = None
    else:
        from_fields = _field_names([edge.metadata.get("from_field")], edge.id)
        to_fields = _field_names([edge.metadata.get("to_field")], edge.id)
        state = "validated"
        origin = str(edge.metadata.get("origin") or "unknown")
        review = edge.metadata.get("review")
        reason_value = (
            review.get("reason")
            if isinstance(review, dict) and review.get("reason") is not None
            else edge.metadata.get("reason")
        )
        reason = str(reason_value) if reason_value is not None else None
        confidence_value = edge.metadata.get("confidence")
        confidence = (
            float(confidence_value)
            if isinstance(confidence_value, (int, float)) and not isinstance(confidence_value, bool)
            else None
        )
        transformation_value = edge.metadata.get("transformation")
        if transformation_value is not None and not isinstance(transformation_value, dict):
            raise ContextFailure(
                "invalid_relationship",
                f"Relationship transformation is invalid: {edge.id}",
            )
        transformation = transformation_value
        kind = (
            "validated_transformed_candidate"
            if transformation is not None
            else "validated_candidate"
        )
    if len(from_fields) != len(to_fields):
        raise ContextFailure(
            "invalid_relationship",
            f"Relationship field counts do not match: {edge.id}",
        )
    return ContextJoin(
        id=edge.id,
        kind=kind,
        from_object_id=source.id,
        from_object=source.label,
        from_fields=from_fields,
        to_object_id=target.id,
        to_object=target.label,
        to_fields=to_fields,
        state=state,
        origin=origin,
        reason=reason,
        confidence=confidence,
        transformation=transformation,
    )


def _field_names(value: object, edge_id: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ContextFailure(
            "invalid_relationship",
            f"Relationship fields are missing or invalid: {edge_id}",
        )
    return tuple(value)


def _adjacency(
    joins: tuple[ContextJoin, ...],
) -> dict[str, tuple[tuple[str, ContextJoin, int], ...]]:
    mutable: dict[str, list[tuple[str, ContextJoin, int]]] = {}
    for join in joins:
        mutable.setdefault(join.from_object_id, []).append((join.to_object_id, join, 0))
        mutable.setdefault(join.to_object_id, []).append((join.from_object_id, join, 1))
    return {
        object_id: tuple(
            sorted(items, key=lambda item: (_join_rank(item[1]), item[2], item[0], item[1].id))
        )
        for object_id, items in mutable.items()
    }


def _join_rank(join: ContextJoin) -> int:
    return 0 if join.kind == "foreign_key" else 1


def _included_joins(
    joins: tuple[ContextJoin, ...],
    *,
    selected_ids: set[str],
    traversal_ids: list[str],
    seed_ids: set[str],
    search_scores: dict[str, int],
    limit: int,
) -> tuple[tuple[ContextJoin, ...], int]:
    by_id = {join.id: join for join in joins}
    ordered = [by_id[join_id] for join_id in traversal_ids]
    seen = set(traversal_ids)
    remaining = sorted(
        (
            join
            for join in joins
            if join.id not in seen
            and join.from_object_id in selected_ids
            and join.to_object_id in selected_ids
        ),
        key=lambda join: (
            0
            if join.from_object_id in seed_ids and join.to_object_id in seed_ids
            else 1,
            -(
                search_scores.get(join.from_object_id, 0)
                + search_scores.get(join.to_object_id, 0)
            ),
            _join_rank(join),
            join.from_object.casefold(),
            join.to_object.casefold(),
            join.id,
        ),
    )
    eligible = [*ordered, *remaining]
    return tuple(eligible[:limit]), max(0, len(eligible) - limit)


def _context_objects(
    graph: GraphDocument,
    selected: dict[str, _Selection],
    *,
    seeds: tuple[SearchHit, ...],
    search_by_id: dict[str, SearchHit],
    joins: tuple[ContextJoin, ...],
    max_fields: int,
    annotation_states: frozenset[str],
) -> tuple[ContextObject, ...]:
    node_by_id = graph.node_by_id()
    seed_rank = {hit.id: position for position, hit in enumerate(seeds)}
    ordered_ids = sorted(
        selected,
        key=lambda object_id: (
            0 if object_id in seed_rank else 1,
            seed_rank.get(object_id, selected[object_id].distance),
            -(search_by_id[object_id].score if object_id in search_by_id else 0),
            node_by_id[object_id].label.casefold(),
        ),
    )
    fields_by_object: dict[str, list[GraphNode]] = {}
    for node in graph.nodes:
        object_id = node.metadata.get("object_id")
        if node.type == "field" and isinstance(object_id, str):
            fields_by_object.setdefault(object_id, []).append(node)

    return tuple(
        _context_object(
            node_by_id[object_id],
            selection=selected[object_id],
            search_hit=search_by_id.get(object_id),
            fields=fields_by_object.get(object_id, []),
            joins=joins,
            max_fields=max_fields,
            annotation_states=annotation_states,
        )
        for object_id in ordered_ids
    )


def _context_object(
    node: GraphNode,
    *,
    selection: _Selection,
    search_hit: SearchHit | None,
    fields: list[GraphNode],
    joins: tuple[ContextJoin, ...],
    max_fields: int,
    annotation_states: frozenset[str],
) -> ContextObject:
    search_fields = {field.id: field for field in search_hit.fields} if search_hit else {}
    join_field_names: set[str] = set()
    for join in joins:
        if join.from_object_id == node.id:
            join_field_names.update(join.from_fields)
        if join.to_object_id == node.id:
            join_field_names.update(join.to_fields)

    ranked: list[tuple[tuple[object, ...], ContextField]] = []
    for field in fields:
        search_field = search_fields.get(field.id)
        is_join = field.label in join_field_names
        is_primary = bool(field.metadata.get("is_primary_key"))
        annotation = (
            field.annotation
            if annotation_is_visible(field.annotation, annotation_states)
            else None
        )
        is_annotated = annotation is not None
        reasons: list[str] = []
        if search_field:
            reasons.append("search")
        if is_join:
            reasons.append("join")
        if is_primary:
            reasons.append("primary_key")
        if is_annotated:
            reasons.append("annotation")
        if not reasons:
            reasons.append("schema")
        description = (
            annotation.description
            if annotation
            else _optional_string(field.metadata.get("technical_description"))
        )
        context_field = ContextField(
            id=field.id,
            name=field.label,
            data_type=str(field.metadata.get("data_type") or "unknown"),
            nullable=bool(field.metadata.get("nullable")),
            description=description,
            role=annotation.role if annotation else None,
            semantic_type=(
                _optional_string(field.metadata.get("semantic_type"))
                if field.annotation is None or annotation is not None
                else None
            ),
            annotation_state=field.annotation.state if field.annotation else None,
            reasons=tuple(reasons),
        )
        rank = (
            0 if search_field else 1,
            -(search_field.score if search_field else 0),
            0 if is_join else 1,
            0 if is_primary else 1,
            0 if is_annotated else 1,
            int(field.metadata.get("position") or 9999),
            field.label.casefold(),
        )
        ranked.append((rank, context_field))
    selected_fields = tuple(
        item for _rank, item in sorted(ranked, key=lambda item: item[0])[:max_fields]
    )
    annotation = (
        node.annotation if annotation_is_visible(node.annotation, annotation_states) else None
    )
    return ContextObject(
        id=node.id,
        label=node.label,
        type=node.type,
        selection=selection.kind,
        distance=selection.distance,
        search_score=search_hit.score if search_hit else None,
        search_reasons=search_hit.reasons if search_hit else (),
        description=(
            annotation.description
            if annotation
            else _optional_string(node.metadata.get("technical_description"))
        ),
        role=annotation.role if annotation else None,
        grain=(
            _optional_string(node.metadata.get("grain"))
            if node.annotation is None or annotation is not None
            else None
        ),
        warnings=annotation.warnings if annotation else (),
        annotation_state=node.annotation.state if node.annotation else None,
        fields=selected_fields,
        omitted_fields=max(0, len(fields) - len(selected_fields)),
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
