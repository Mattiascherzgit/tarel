"""Create deterministic annotation tasks from technical graph objects."""

from __future__ import annotations

import hashlib
import json

from tarel.annotations.contracts import AnnotationFailure, AnnotationTask
from tarel.connectors.contracts import SampleResult
from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.providers.contracts import Message, StructuredRequest

_OBJECT_ROLES = [
    "fact",
    "dimension",
    "bridge",
    "lookup",
    "date",
    "snapshot",
    "transaction",
    "staging",
    "reference",
    "view",
    "other",
]

_FIELD_ROLES = [
    "measure",
    "dimension",
    "date",
    "key",
    "label",
    "status",
    "audit",
    "technical",
    "description",
    "flag",
    "other",
]


def plan_annotation_tasks(
    graph: GraphDocument,
    *,
    namespace: str | None = None,
    objects: set[str] | None = None,
    limit: int | None = None,
    missing_only: bool = True,
    samples_by_target: dict[str, SampleResult] | None = None,
) -> tuple[AnnotationTask, ...]:
    selected: list[GraphNode] = []
    normalized_objects = {item.lower() for item in objects or set()}
    for node in graph.nodes:
        if node.type not in {"table", "view"}:
            continue
        if namespace and str(node.metadata.get("namespace", "")).lower() != namespace.lower():
            continue
        if normalized_objects and not _matches_object(node, normalized_objects):
            continue
        if missing_only and node.annotation is not None:
            continue
        selected.append(node)
    if limit is not None:
        selected = selected[:limit]
    samples = samples_by_target or {}
    return tuple(_task_for_object(graph, node, sample=samples.get(node.id)) for node in selected)


def annotation_task_for_target(graph: GraphDocument, target_id: str) -> AnnotationTask:
    node = graph.node_by_id().get(target_id)
    if node is None or node.type not in {"table", "view"}:
        raise AnnotationFailure("target_not_found", f"Annotatable object not found: {target_id}")
    return _task_for_object(graph, node)


def _task_for_object(
    graph: GraphDocument,
    node: GraphNode,
    *,
    sample: SampleResult | None = None,
) -> AnnotationTask:
    node_by_id = graph.node_by_id()
    fields = [
        candidate
        for candidate in graph.nodes
        if candidate.type == "field" and candidate.metadata.get("object_id") == node.id
    ]
    context = {
        "catalog": graph.catalog,
        "dialect": graph.dialect,
        "object": {
            "kind": node.type,
            "label": node.label,
            "name": node.metadata.get("name"),
            "namespace": node.metadata.get("namespace"),
            "primary_key": node.metadata.get("primary_key", []),
            "technical_description": node.metadata.get("technical_description"),
        },
        "fields": [
            {
                "data_type": item.metadata.get("data_type"),
                "name": item.label,
                "nullable": item.metadata.get("nullable"),
                "position": item.metadata.get("position"),
                "primary_key": item.metadata.get("is_primary_key", False),
                "technical_description": item.metadata.get("technical_description"),
            }
            for item in fields
        ],
        "relationships": [
            {
                "direction": "outgoing" if edge.source_id == node.id else "incoming",
                "from_fields": edge.metadata.get("from_fields", []),
                "name": edge.metadata.get("name"),
                "other_object": node_by_id[
                    edge.target_id if edge.source_id == node.id else edge.source_id
                ].label,
                "to_fields": edge.metadata.get("to_fields", []),
            }
            for edge in graph.edges
            if edge.type == "foreign_key" and node.id in {edge.source_id, edge.target_id}
        ],
    }
    technical_context = json.dumps(
        context,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    task_id = hashlib.sha256(
        f"{graph.name}\n{node.id}\n{technical_context}".encode()
    ).hexdigest()[:24]
    if sample is not None:
        context["sample"] = sample.to_dict()
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return AnnotationTask(
        id=task_id,
        graph_name=graph.name,
        target_id=node.id,
        target_label=node.label,
        request=StructuredRequest(
            messages=(
                Message(
                    role="system",
                    content=(
                        "You annotate analytical data structures. Use only supplied technical "
                        "evidence. Do not invent business meaning. Express uncertainty through "
                        "confidence, confidence_reason, warnings, and evidence. If bounded sample "
                        "rows are supplied, use them only to recognize semantic patterns. Never "
                        "repeat an actual sample value anywhere in the response—not in prose, "
                        "synonyms, warnings, or evidence. Cite the sampled field without its value."
                    ),
                ),
                Message(
                    role="user",
                    content=(
                        "Propose a draft annotation for this object and every supplied field. "
                        "Return only the requested structured result.\n\n"
                        + serialized
                        + (
                            "\n\nSAMPLE POLICY: Sample rows are sensitive and input-only. Never "
                            "repeat their values. For sample-derived evidence, identify the field "
                            "in reference and set value to null."
                            if sample is not None
                            else ""
                        )
                    ),
                ),
            ),
            schema_name="TarelObjectAnnotation",
            schema=annotation_schema(),
        ),
        protected_values=_protected_values(sample),
    )


def _protected_values(sample: SampleResult | None) -> tuple[str, ...]:
    if sample is None:
        return ()
    values: set[str] = set()
    for row in sample.rows:
        for value in row.values():
            if value is None:
                continue
            if isinstance(value, str):
                values.add(value)
            else:
                values.add(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return tuple(sorted(values))


def annotation_schema() -> dict[str, object]:
    evidence = {
        "additionalProperties": False,
        "properties": {
            "reason": {"type": ["string", "null"]},
            "reference": {"type": "string"},
            "source": {"type": "string"},
            "value": {
                "description": "Use null for sample-derived evidence; never repeat a sample value.",
                "type": ["string", "null"],
            },
        },
        "required": ["source", "reference", "value", "reason"],
        "type": "object",
    }
    field = {
        "additionalProperties": False,
        "properties": {
            "confidence": {"maximum": 1.0, "minimum": 0.0, "type": "number"},
            "confidence_reason": {"type": "string"},
            "description": {"type": "string"},
            "evidence": {"items": evidence, "minItems": 1, "type": "array"},
            "name": {"type": "string"},
            "role": {"enum": [*_FIELD_ROLES, None]},
            "semantic_type": {"type": ["string", "null"]},
            "synonyms": {"items": {"type": "string"}, "type": "array"},
            "warnings": {"items": {"type": "string"}, "type": "array"},
        },
        "required": [
            "name",
            "description",
            "role",
            "semantic_type",
            "synonyms",
            "warnings",
            "confidence",
            "confidence_reason",
            "evidence",
        ],
        "type": "object",
    }
    return {
        "additionalProperties": False,
        "properties": {
            "confidence": {"maximum": 1.0, "minimum": 0.0, "type": "number"},
            "confidence_reason": {"type": "string"},
            "description": {"type": "string"},
            "evidence": {"items": evidence, "minItems": 1, "type": "array"},
            "fields": {"items": field, "type": "array"},
            "grain": {"type": ["string", "null"]},
            "role": {"enum": [*_OBJECT_ROLES, None]},
            "synonyms": {"items": {"type": "string"}, "type": "array"},
            "warnings": {"items": {"type": "string"}, "type": "array"},
        },
        "required": [
            "description",
            "role",
            "grain",
            "synonyms",
            "warnings",
            "confidence",
            "confidence_reason",
            "evidence",
            "fields",
        ],
        "type": "object",
    }


def _matches_object(node: GraphNode, values: set[str]) -> bool:
    name = str(node.metadata.get("name", ""))
    namespace = str(node.metadata.get("namespace", ""))
    qualified_name = f"{graph_catalog(node)}.{namespace}.{name}".lower()
    candidates = {name.lower(), node.label.lower(), qualified_name}
    return bool(candidates & values)


def graph_catalog(node: GraphNode) -> str:
    return str(node.metadata.get("catalog", ""))
