"""Shared annotation-state policy for search and context projection."""

from __future__ import annotations

from tarel.annotations.contracts import AnnotationFailure
from tarel.graph.contracts import ANNOTATION_STATES, GraphAnnotation

DEFAULT_CONTEXT_ANNOTATION_STATES = frozenset(
    {"deferred", "draft", "review_required", "validated"}
)


def selected_annotation_states(
    states: frozenset[str] | None = None,
    *,
    validated_only: bool = False,
) -> frozenset[str]:
    if states is not None and validated_only:
        raise AnnotationFailure(
            "conflicting_annotation_filter",
            "--validated-only cannot be combined with --annotation-state.",
        )
    selected = frozenset({"validated"}) if validated_only else (
        states if states is not None else DEFAULT_CONTEXT_ANNOTATION_STATES
    )
    unknown = selected - ANNOTATION_STATES
    if unknown or not selected:
        label = ", ".join(sorted(unknown)) if unknown else "empty selection"
        raise AnnotationFailure(
            "invalid_annotation_state",
            f"Invalid annotation-state filter: {label}",
        )
    return selected


def annotation_is_visible(
    annotation: GraphAnnotation | None,
    states: frozenset[str],
) -> bool:
    return annotation is not None and annotation.state in states
