"""Compile ephemeral source enrichment and aggregate key-pattern evidence."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from tarel.connectors.contracts import ObjectProfileResult, RelationshipPair, SampleResult
from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.relationships.core import TransformedRelationshipProfile
from tarel.sources.contracts import SourceProfile

ENRICHMENT_CONTRACT_VERSION = "tarel.enrichment.v0.1"
MIN_PATTERN_MATCHES = 3
MIN_PATTERN_COVERAGE = 0.8
MIN_TRANSFORMED_OVERLAP = 2
MIN_TRANSFORMED_SOURCE_COVERAGE = 0.6
MIN_TRANSFORMED_TARGET_UNIQUENESS = 0.9
_TOKEN = re.compile(r"[A-Za-z]+|\d+|[^A-Za-z\d]+")
_NUMERIC_TYPES = (
    "bigint",
    "decimal",
    "double",
    "float",
    "int",
    "integer",
    "numeric",
    "real",
    "smallint",
    "tinyint",
)


@dataclass(frozen=True, slots=True)
class EnrichmentFailure:
    operation: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "operation": self.operation}


@dataclass(frozen=True, slots=True)
class KeyPatternComponent:
    index: int
    start: int
    length: int
    kind: str = "digits"

    def to_dict(self) -> dict[str, object]:
        return {
            "index": self.index,
            "kind": self.kind,
            "length": self.length,
            "start": self.start,
        }


@dataclass(frozen=True, slots=True)
class KeyPatternProfile:
    field: str
    pattern: str
    sample_count: int
    match_count: int
    coverage: float
    components: tuple[KeyPatternComponent, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "components": [item.to_dict() for item in self.components],
            "coverage": round(self.coverage, 6),
            "field": self.field,
            "match_count": self.match_count,
            "pattern": self.pattern,
            "sample_count": self.sample_count,
        }


@dataclass(frozen=True, slots=True)
class EnrichedObject:
    id: str
    label: str
    profile: ObjectProfileResult | None
    sample: SampleResult | None
    key_patterns: tuple[KeyPatternProfile, ...] = ()
    failures: tuple[EnrichmentFailure, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "failures": [item.to_dict() for item in self.failures],
            "id": self.id,
            "key_patterns": [item.to_dict() for item in self.key_patterns],
            "label": self.label,
            "profile": self.profile.to_dict() if self.profile is not None else None,
            "sample": self.sample.to_dict() if self.sample is not None else None,
        }


@dataclass(frozen=True, slots=True)
class EnrichmentWorkfile:
    source: str
    source_revision: str
    graph: str
    graph_revision: str
    permissions: tuple[str, ...]
    objects: tuple[EnrichedObject, ...]
    transformed_join_candidates: tuple[TransformedRelationshipProfile, ...] = ()
    contract_version: str = ENRICHMENT_CONTRACT_VERSION

    @property
    def samples_present(self) -> bool:
        return any(item.sample is not None for item in self.objects)

    @property
    def complete(self) -> bool:
        return all(not item.failures for item in self.objects)

    def to_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "contract_version": self.contract_version,
            "graph": {"name": self.graph, "revision": self.graph_revision},
            "objects": [item.to_dict() for item in self.objects],
            "permissions": list(self.permissions),
            "source": {"name": self.source, "revision": self.source_revision},
            "transformed_join_candidates": [
                item.to_dict() for item in self.transformed_join_candidates
            ],
        }


@dataclass(frozen=True, slots=True)
class _PatternObservation:
    profile: KeyPatternProfile
    component_values: tuple[tuple[str, ...], ...]


def compile_enrichment_workfile(
    graph: GraphDocument,
    source: SourceProfile,
    *,
    graph_revision: str,
    profiles: dict[str, ObjectProfileResult],
    samples: dict[str, SampleResult],
    failures: dict[str, tuple[EnrichmentFailure, ...]],
) -> EnrichmentWorkfile:
    patterns, candidates = infer_key_patterns(graph, samples)
    objects = tuple(
        EnrichedObject(
            id=node.id,
            label=node.label,
            profile=profiles.get(node.id),
            sample=samples.get(node.id),
            key_patterns=patterns.get(node.id, ()),
            failures=failures.get(node.id, ()),
        )
        for node in _object_nodes(graph)
    )
    return EnrichmentWorkfile(
        source=source.name,
        source_revision=source.revision,
        graph=graph.name,
        graph_revision=graph_revision,
        permissions=source.enrichment_permissions,
        objects=objects,
        transformed_join_candidates=candidates,
    )


def infer_key_patterns(
    graph: GraphDocument,
    samples: dict[str, SampleResult],
) -> tuple[
    dict[str, tuple[KeyPatternProfile, ...]],
    tuple[TransformedRelationshipProfile, ...],
]:
    observations: dict[tuple[str, str], _PatternObservation] = {}
    patterns: dict[str, list[KeyPatternProfile]] = defaultdict(list)
    for object_node in _object_nodes(graph):
        sample = samples.get(object_node.id)
        if sample is None:
            continue
        for field in sample.selected_fields:
            values = tuple(
                str(row[field])
                for row in sample.rows
                if row.get(field) is not None and isinstance(row.get(field), str)
            )
            observation = _dominant_pattern(field, values)
            if observation is None:
                continue
            observations[(object_node.id, field)] = observation
            patterns[object_node.id].append(observation.profile)

    candidates = _transformed_candidates(graph, samples, observations)
    return (
        {
            object_id: tuple(sorted(items, key=lambda item: (item.field, item.pattern)))
            for object_id, items in patterns.items()
        },
        candidates,
    )


def _dominant_pattern(field: str, values: tuple[str, ...]) -> _PatternObservation | None:
    if len(values) < 3:
        return None
    grouped: dict[
        str,
        tuple[tuple[KeyPatternComponent, ...], list[tuple[str, ...]]],
    ] = {}
    for value in values:
        shaped = _shape(value)
        if shaped is None:
            continue
        pattern, components, extracted = shaped
        current = grouped.get(pattern)
        if current is None:
            grouped[pattern] = (components, [extracted])
        else:
            current[1].append(extracted)
    if not grouped:
        return None
    pattern, (components, matches) = min(
        grouped.items(),
        key=lambda item: (-len(item[1][1]), item[0]),
    )
    match_count = len(matches)
    coverage = match_count / len(values)
    if match_count < MIN_PATTERN_MATCHES or coverage < MIN_PATTERN_COVERAGE:
        return None
    component_values = tuple(
        tuple(match[index] for match in matches) for index in range(len(components))
    )
    return _PatternObservation(
        profile=KeyPatternProfile(
            field=field,
            pattern=pattern,
            sample_count=len(values),
            match_count=match_count,
            coverage=coverage,
            components=components,
        ),
        component_values=component_values,
    )


def _shape(
    value: str,
) -> tuple[str, tuple[KeyPatternComponent, ...], tuple[str, ...]] | None:
    matches = tuple(_TOKEN.finditer(value))
    if not matches or "".join(match.group(0) for match in matches) != value:
        return None
    components: list[KeyPatternComponent] = []
    extracted: list[str] = []
    rendered: list[str] = []
    for match in matches:
        token = match.group(0)
        if token.isdigit():
            index = len(components) + 1
            components.append(
                KeyPatternComponent(
                    index=index,
                    start=match.start(),
                    length=len(token),
                )
            )
            extracted.append(token)
            rendered.append(f"{{digits_{index}:{len(token)}}}")
        else:
            rendered.append(token)
    if not components or not any(character.isalpha() for character in value):
        return None
    return "".join(rendered), tuple(components), tuple(extracted)


def _transformed_candidates(
    graph: GraphDocument,
    samples: dict[str, SampleResult],
    observations: dict[tuple[str, str], _PatternObservation],
) -> tuple[TransformedRelationshipProfile, ...]:
    node_by_id = graph.node_by_id()
    target_fields = tuple(
        node
        for node in graph.nodes
        if node.type == "field"
        and (
            bool(node.metadata.get("is_primary_key"))
            or int(node.metadata.get("position") or 0) == 1
        )
    )
    candidates: list[TransformedRelationshipProfile] = []
    for (source_object_id, source_field), observation in observations.items():
        source_object = node_by_id[source_object_id]
        for component, source_values in zip(
            observation.profile.components,
            observation.component_values,
            strict=True,
        ):
            for target_field in target_fields:
                target_object_id = str(target_field.metadata.get("object_id") or "")
                if target_object_id == source_object_id:
                    continue
                target_sample = samples.get(target_object_id)
                if target_sample is None or target_field.label not in target_sample.selected_fields:
                    continue
                target_values = tuple(
                    row[target_field.label]
                    for row in target_sample.rows
                    if row.get(target_field.label) is not None
                )
                profile = _candidate_profile(
                    source_object,
                    source_field,
                    observation.profile,
                    component,
                    source_values,
                    node_by_id[target_object_id],
                    target_field,
                    target_values,
                    target_sample,
                )
                if profile is not None:
                    candidates.append(profile)
    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                item.pair.from_namespace,
                item.pair.from_object,
                item.pair.from_field,
                item.component_index,
                item.pair.to_namespace,
                item.pair.to_object,
                item.pair.to_field,
            ),
        )
    )


def _candidate_profile(
    source_object: GraphNode,
    source_field: str,
    pattern: KeyPatternProfile,
    component: KeyPatternComponent,
    source_values: tuple[str, ...],
    target_object: GraphNode,
    target_field: GraphNode,
    target_values: tuple[object, ...],
    target_sample: SampleResult,
) -> TransformedRelationshipProfile | None:
    numeric_target = _is_numeric_type(str(target_field.metadata.get("data_type") or ""))
    normalized_source = {_normalize_value(value, numeric=numeric_target) for value in source_values}
    normalized_target = {
        _normalize_value(value, numeric=numeric_target) for value in target_values
    }
    normalized_source.discard(None)
    normalized_target.discard(None)
    overlap_count = len(normalized_source & normalized_target)
    source_coverage = overlap_count / max(1, len(normalized_source))
    target_uniqueness = len(normalized_target) / max(1, len(target_values))
    if (
        overlap_count < MIN_TRANSFORMED_OVERLAP
        or source_coverage < MIN_TRANSFORMED_SOURCE_COVERAGE
        or target_uniqueness < MIN_TRANSFORMED_TARGET_UNIQUENESS
    ):
        return None
    return TransformedRelationshipProfile(
        pair=RelationshipPair(
            from_namespace=str(source_object.metadata["namespace"]),
            from_object=str(source_object.metadata["name"]),
            from_field=source_field,
            to_namespace=str(target_object.metadata["namespace"]),
            to_object=str(target_object.metadata["name"]),
            to_field=target_field.label,
        ),
        pattern=pattern.pattern,
        component_index=component.index,
        component_start=component.start,
        component_length=component.length,
        pattern_sample_count=pattern.sample_count,
        pattern_match_count=pattern.match_count,
        pattern_coverage=pattern.coverage,
        source_distinct_count=len(normalized_source),
        target_non_null_count=len(target_values),
        target_distinct_count=len(normalized_target),
        overlap_count=overlap_count,
        sample_row_limit=len(target_sample.rows),
    )


def _normalize_value(value: object, *, numeric: bool) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if numeric:
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
        if not number.is_finite() or number != number.to_integral_value():
            return None
        return format(number.to_integral_value(), "f")
    return text


def _is_numeric_type(data_type: str) -> bool:
    normalized = data_type.casefold().strip()
    base = re.split(r"[\s(]", normalized, maxsplit=1)[0]
    return base in _NUMERIC_TYPES


def _object_nodes(graph: GraphDocument) -> tuple[GraphNode, ...]:
    return tuple(
        sorted(
            (node for node in graph.nodes if node.type in {"table", "view"}),
            key=lambda node: (node.label.casefold(), node.id),
        )
    )
