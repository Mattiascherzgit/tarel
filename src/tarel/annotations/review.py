"""Human review operations over persisted graph annotations."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from tarel.annotations.contracts import AnnotationFailure
from tarel.graph.contracts import (
    ANNOTATION_STATES,
    AnnotationProvenance,
    GraphAnnotation,
    GraphDocument,
    GraphFailure,
    GraphNode,
)

_REVIEW_KEY = "annotation_review"
_EDITABLE_KEYS = frozenset(
    {"description", "grain", "role", "semantic_type", "synonyms", "warnings"}
)


@dataclass(frozen=True, slots=True)
class AnnotationReviewRecord:
    reference: str
    node: GraphNode

    def to_dict(self) -> dict[str, object]:
        annotation = self.node.annotation
        return {
            "annotation": annotation.to_dict() if annotation else None,
            "grain": _optional_metadata_string(self.node, "grain"),
            "node_id": self.node.id,
            "node_type": self.node.type,
            "reference": self.reference,
            "review": _review_metadata(self.node),
            "semantic_type": _optional_metadata_string(self.node, "semantic_type"),
        }


def annotation_review_record(
    graph: GraphDocument,
    reference: str,
) -> AnnotationReviewRecord:
    node, resolved_reference = resolve_annotation_target(graph, reference)
    return AnnotationReviewRecord(reference=resolved_reference, node=node)


def list_annotation_reviews(
    graph: GraphDocument,
    *,
    states: frozenset[str] | None = None,
) -> tuple[AnnotationReviewRecord, ...]:
    selected_states = ANNOTATION_STATES if states is None else states
    _validate_states(selected_states)
    object_by_id = {
        node.id: node for node in graph.nodes if node.type in {"table", "view"}
    }
    records: list[AnnotationReviewRecord] = []
    for node in graph.nodes:
        if node.annotation is None or node.annotation.state not in selected_states:
            continue
        if node.type in {"table", "view"}:
            reference = node.label
        elif node.type == "field":
            parent = object_by_id.get(str(node.metadata.get("object_id") or ""))
            if parent is None:
                raise AnnotationFailure(
                    "invalid_annotation_target",
                    f"Annotated field has no parent object: {node.id}",
                )
            reference = f"{parent.label}.{node.label}"
        else:
            continue
        records.append(AnnotationReviewRecord(reference=reference, node=node))
    return tuple(sorted(records, key=lambda item: (item.reference.casefold(), item.node.id)))


def edit_annotation(
    graph: GraphDocument,
    reference: str,
    patch: dict[str, Any],
    *,
    reason: str,
) -> tuple[GraphDocument, AnnotationReviewRecord]:
    node, resolved_reference = resolve_annotation_target(graph, reference)
    annotation = _require_annotation(node)
    clean_reason = _review_reason(reason)
    unknown = set(patch) - _EDITABLE_KEYS
    if unknown:
        raise AnnotationFailure(
            "invalid_annotation_patch",
            f"Unsupported annotation edit fields: {', '.join(sorted(unknown))}",
        )
    if not patch:
        raise AnnotationFailure("invalid_annotation_patch", "Annotation edit cannot be empty.")
    if node.type == "field" and "grain" in patch:
        raise AnnotationFailure(
            "invalid_annotation_patch",
            "grain can only be edited on a table or view annotation.",
        )
    if node.type in {"table", "view"} and "semantic_type" in patch:
        raise AnnotationFailure(
            "invalid_annotation_patch",
            "semantic_type can only be edited on a field annotation.",
        )

    description = (
        _required_patch_string(patch["description"], "description")
        if "description" in patch
        else annotation.description
    )
    role = _optional_patch_string(patch["role"], "role") if "role" in patch else annotation.role
    synonyms = (
        _patch_strings(patch["synonyms"], "synonyms")
        if "synonyms" in patch
        else annotation.synonyms
    )
    warnings = (
        _patch_strings(patch["warnings"], "warnings")
        if "warnings" in patch
        else annotation.warnings
    )
    metadata = _metadata_with_review(node, action="edit", reason=clean_reason)
    if "grain" in patch:
        metadata["grain"] = _optional_patch_string(patch["grain"], "grain")
    if "semantic_type" in patch:
        metadata["semantic_type"] = _optional_patch_string(
            patch["semantic_type"], "semantic_type"
        )
    updated_node = replace(
        node,
        metadata=metadata,
        annotation=replace(
            annotation,
            description=description,
            role=role,
            synonyms=synonyms,
            warnings=warnings,
            confidence=None,
            confidence_reason=None,
            evidence=(),
            provenance=AnnotationProvenance(source="human"),
            state="draft",
        ),
    )
    updated_graph = _replace_node(graph, updated_node)
    return updated_graph, AnnotationReviewRecord(resolved_reference, updated_node)


def decide_annotation(
    graph: GraphDocument,
    reference: str,
    *,
    state: str,
    reason: str,
) -> tuple[GraphDocument, AnnotationReviewRecord]:
    if state not in {"deferred", "rejected", "validated"}:
        raise AnnotationFailure("invalid_annotation_state", f"Unsupported review state: {state}")
    node, resolved_reference = resolve_annotation_target(graph, reference)
    annotation = _require_annotation(node)
    clean_reason = _review_reason(reason)
    action = {"deferred": "defer", "rejected": "reject", "validated": "validate"}[state]
    updated_node = replace(
        node,
        metadata=_metadata_with_review(node, action=action, reason=clean_reason),
        annotation=replace(annotation, state=state),
    )
    updated_graph = _replace_node(graph, updated_node)
    return updated_graph, AnnotationReviewRecord(resolved_reference, updated_node)


def decide_annotation_scope(
    graph: GraphDocument,
    reference: str,
    *,
    state: str,
    reason: str,
    include_fields: bool,
) -> tuple[GraphDocument, tuple[AnnotationReviewRecord, ...]]:
    updated, root = decide_annotation(graph, reference, state=state, reason=reason)
    if not include_fields:
        return updated, (root,)
    if root.node.type not in {"table", "view"}:
        raise AnnotationFailure(
            "invalid_annotation_scope",
            "--include-fields requires a table or view target.",
        )
    fields = sorted(
        (
            node
            for node in updated.nodes
            if node.type == "field" and node.metadata.get("object_id") == root.node.id
        ),
        key=lambda node: (int(node.metadata.get("position") or 9999), node.id),
    )
    records = [root]
    for field in fields:
        updated, record = decide_annotation(
            updated,
            field.id,
            state=state,
            reason=reason,
        )
        records.append(record)
    return updated, tuple(records)


def resolve_annotation_target(
    graph: GraphDocument,
    reference: str,
) -> tuple[GraphNode, str]:
    normalized = reference.strip().casefold()
    object_by_id = {
        node.id: node for node in graph.nodes if node.type in {"table", "view"}
    }
    matches: list[tuple[GraphNode, str]] = []
    for node in graph.nodes:
        if node.type in {"table", "view"}:
            candidates = {node.id.casefold(), node.label.casefold()}
            name = node.metadata.get("name")
            if isinstance(name, str):
                candidates.add(name.casefold())
            if normalized in candidates:
                matches.append((node, node.label))
        elif node.type == "field":
            parent = object_by_id.get(str(node.metadata.get("object_id") or ""))
            if parent is None:
                continue
            full_reference = f"{parent.label}.{node.label}"
            if normalized in {node.id.casefold(), full_reference.casefold()}:
                matches.append((node, full_reference))
    if len(matches) != 1:
        code = "annotation_target_not_found" if not matches else "ambiguous_annotation_target"
        raise AnnotationFailure(code, f"Could not resolve one annotation target: {reference}")
    return matches[0]


def has_human_review(node: GraphNode) -> bool:
    return _REVIEW_KEY in node.metadata


def _metadata_with_review(node: GraphNode, *, action: str, reason: str) -> dict[str, object]:
    metadata = dict(node.metadata)
    metadata.pop("change_review", None)
    existing = _review_metadata(node)
    if existing is None:
        annotation = _require_annotation(node)
        original = {
            "annotation": annotation.to_dict(),
            "grain": _optional_metadata_string(node, "grain"),
            "semantic_type": _optional_metadata_string(node, "semantic_type"),
        }
        events: list[object] = []
    else:
        original = existing["original"]
        events = list(existing["events"])
    events.append({"action": action, "reason": reason, "source": "human"})
    metadata[_REVIEW_KEY] = {"events": events, "original": original}
    return metadata


def _review_metadata(node: GraphNode) -> dict[str, object] | None:
    value = node.metadata.get(_REVIEW_KEY)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AnnotationFailure("invalid_annotation_review", "Annotation review must be an object.")
    original = value.get("original")
    events = value.get("events")
    if not isinstance(original, dict) or not isinstance(events, list):
        raise AnnotationFailure(
            "invalid_annotation_review",
            "Annotation review requires original and events values.",
        )
    original_annotation = original.get("annotation")
    if not isinstance(original_annotation, dict):
        raise AnnotationFailure(
            "invalid_annotation_review",
            "Annotation review requires the original annotation proposal.",
        )
    try:
        GraphAnnotation.from_dict(original_annotation)
    except GraphFailure as exc:
        raise AnnotationFailure(
            "invalid_annotation_review",
            "Annotation review contains an invalid original proposal.",
        ) from exc
    for key in ("grain", "semantic_type"):
        if original.get(key) is not None and not isinstance(original.get(key), str):
            raise AnnotationFailure(
                "invalid_annotation_review",
                f"Annotation review {key} must be a string or null.",
            )
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("action") not in {"defer", "edit", "reject", "validate"}
            or event.get("source") != "human"
            or not isinstance(event.get("reason"), str)
            or not str(event["reason"]).strip()
        ):
            raise AnnotationFailure(
                "invalid_annotation_review",
                "Annotation review contains an invalid event.",
            )
    return value


def _replace_node(graph: GraphDocument, updated: GraphNode) -> GraphDocument:
    return replace(
        graph,
        nodes=tuple(updated if node.id == updated.id else node for node in graph.nodes),
    )


def _require_annotation(node: GraphNode) -> GraphAnnotation:
    if node.annotation is None:
        raise AnnotationFailure(
            "annotation_not_found",
            f"Target does not have an annotation proposal: {node.label}",
        )
    return node.annotation


def _review_reason(value: str) -> str:
    clean = value.strip()
    if not clean:
        raise AnnotationFailure(
            "missing_annotation_review_reason",
            "An annotation review requires a non-empty reason.",
        )
    return clean


def _required_patch_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AnnotationFailure(
            "invalid_annotation_patch",
            f"{label} must be a non-empty string.",
        )
    return value.strip()


def _optional_patch_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnnotationFailure(
            "invalid_annotation_patch",
            f"{label} must be a string or null.",
        )
    return value.strip() or None


def _patch_strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AnnotationFailure(
            "invalid_annotation_patch",
            f"{label} must be an array of strings.",
        )
    return tuple(item.strip() for item in value if item.strip())


def _optional_metadata_string(node: GraphNode, key: str) -> str | None:
    value = node.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _validate_states(states: frozenset[str]) -> None:
    unknown = states - ANNOTATION_STATES
    if unknown:
        raise AnnotationFailure(
            "invalid_annotation_state",
            f"Unsupported annotation states: {', '.join(sorted(unknown))}",
        )
