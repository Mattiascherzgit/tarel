"""Human review operations over observations and explicit write units."""

from __future__ import annotations

from dataclasses import replace

from tarel.lineage.contracts import (
    LineageClaim,
    LineageDocument,
    LineageFailure,
    LineageReview,
    LineageWriteUnit,
    validate_lineage_document,
)

LineageReviewItem = LineageClaim | LineageWriteUnit


def list_lineage_items(
    document: LineageDocument,
    *,
    states: frozenset[str] | None = None,
) -> tuple[LineageReviewItem, ...]:
    selected = (
        frozenset({"draft", "rejected", "review_required", "validated"})
        if states is None
        else states
    )
    unknown = selected - {"draft", "rejected", "review_required", "validated"}
    if unknown or not selected:
        raise LineageFailure("invalid_lineage_review", "Invalid lineage review-state filter.")
    items = (*document.claims, *document.write_units)
    return tuple(
        sorted(
            (item for item in items if item.state in selected),
            key=lambda item: item.id,
        )
    )


def decide_lineage_item(
    document: LineageDocument,
    item_id: str,
    *,
    decision: str,
    reason: str,
) -> tuple[LineageDocument, LineageReviewItem]:
    if decision not in {"reject", "validate"} or not reason.strip():
        raise LineageFailure(
            "invalid_lineage_review",
            "Lineage review requires a decision and reason.",
        )
    review = LineageReview(decision=decision, reason=reason.strip())
    state = "rejected" if decision == "reject" else "validated"
    claims, claim = _decide(document.claims, item_id, state=state, review=review)
    units, unit = _decide(document.write_units, item_id, state=state, review=review)
    found = claim or unit
    if found is None:
        raise LineageFailure("lineage_item_not_found", f"Lineage item not found: {item_id}")
    updated = replace(document, claims=claims, write_units=units)
    validate_lineage_document(updated)
    return updated, found


def _decide(
    items: tuple[LineageReviewItem, ...],
    item_id: str,
    *,
    state: str,
    review: LineageReview,
) -> tuple[tuple[LineageReviewItem, ...], LineageReviewItem | None]:
    found = None
    result = []
    for item in items:
        if item.id == item_id:
            found = replace(item, state=state, reviews=(*item.reviews, review))
            result.append(found)
        else:
            result.append(item)
    return tuple(result), found
