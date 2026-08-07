"""Deterministic analysis and review coverage for one lineage document."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tarel.lineage.contracts import LineageDocument


@dataclass(frozen=True, slots=True)
class LineageReviewCounts:
    draft: int = 0
    review_required: int = 0
    validated: int = 0
    rejected: int = 0

    @property
    def total(self) -> int:
        return self.draft + self.review_required + self.validated + self.rejected

    def to_dict(self) -> dict[str, int]:
        return {
            "draft": self.draft,
            "rejected": self.rejected,
            "review_required": self.review_required,
            "total": self.total,
            "validated": self.validated,
        }


@dataclass(frozen=True, slots=True)
class LineageDefinitionStatus:
    definition_id: str
    definition_name: str
    analysis_state: str
    failure_code: str | None
    failure_provider: str | None
    failure_model: str | None
    claims: LineageReviewCounts
    write_units: LineageReviewCounts

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_state": self.analysis_state,
            "claims": self.claims.to_dict(),
            "definition_id": self.definition_id,
            "definition_name": self.definition_name,
            "failure": (
                None
                if self.failure_code is None
                else {
                    "code": self.failure_code,
                    "model": self.failure_model,
                    "provider": self.failure_provider,
                }
            ),
            "write_units": self.write_units.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class LineageStatus:
    lineage_name: str
    source_revision: str
    definitions_total: int
    analyses_complete: int
    analyses_failed: int
    analyses_pending: int
    claims: LineageReviewCounts
    write_units: LineageReviewCounts
    definitions: tuple[LineageDefinitionStatus, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_coverage": {
                "complete": self.analyses_complete,
                "failed": self.analyses_failed,
                "pending": self.analyses_pending,
                "total": self.definitions_total,
            },
            "claims": self.claims.to_dict(),
            "definitions": [item.to_dict() for item in self.definitions],
            "lineage": self.lineage_name,
            "source_revision": self.source_revision,
            "write_units": self.write_units.to_dict(),
        }


def lineage_status(document: LineageDocument) -> LineageStatus:
    analyses = {item.definition_id for item in document.analyses}
    failures = {item.definition_id: item for item in document.analysis_failures}
    rows = []
    for definition in sorted(
        document.definitions,
        key=lambda item: (item.qualified_name.casefold(), item.id),
    ):
        failure = failures.get(definition.id)
        if definition.id in analyses:
            analysis_state = "complete"
        elif failure is not None:
            analysis_state = "failed"
        else:
            analysis_state = "pending"
        rows.append(
            LineageDefinitionStatus(
                definition_id=definition.id,
                definition_name=definition.qualified_name,
                analysis_state=analysis_state,
                failure_code=failure.code if failure else None,
                failure_provider=failure.provider if failure else None,
                failure_model=failure.model if failure else None,
                claims=_review_counts(
                    item.state
                    for item in document.claims
                    if item.definition_id == definition.id
                ),
                write_units=_review_counts(
                    item.state
                    for item in document.write_units
                    if item.definition_id == definition.id
                ),
            )
        )
    complete = sum(item.analysis_state == "complete" for item in rows)
    failed = sum(item.analysis_state == "failed" for item in rows)
    return LineageStatus(
        lineage_name=document.name,
        source_revision=document.source_revision,
        definitions_total=len(rows),
        analyses_complete=complete,
        analyses_failed=failed,
        analyses_pending=len(rows) - complete - failed,
        claims=_review_counts(item.state for item in document.claims),
        write_units=_review_counts(item.state for item in document.write_units),
        definitions=tuple(rows),
    )


def _review_counts(states: Iterable[str]) -> LineageReviewCounts:
    counts = {"draft": 0, "rejected": 0, "review_required": 0, "validated": 0}
    for state in states:
        counts[state] += 1
    return LineageReviewCounts(**counts)
