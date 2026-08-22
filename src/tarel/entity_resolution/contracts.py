"""Strict contracts for bounded entity-resolution hypotheses."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from typing import Any

ENTITY_RESOLUTION_CONTRACT_VERSION = "tarel.entity-resolution-candidate.v0.1"
ENTITY_RESOLUTION_STATES = frozenset({"candidate", "rejected", "reviewed"})
ENTITY_RESOLUTION_EVIDENCE_LEVELS = frozenset(
    {"population_tested", "proposed", "sample_tested"}
)
ENTITY_RESOLUTION_MODES = frozenset(
    {"confirmed_only", "confirmed_then_candidates", "include_candidates"}
)
ENTITY_RESOLUTION_OPERATIONS = frozenset(
    {"casefold", "collapse_whitespace", "strip_punctuation", "trim", "unicode_nfkc"}
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_REASON_LENGTH = 1_000
_MAX_OPERATIONS = 8


class EntityResolutionFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class EntityResolutionRule:
    kind: str
    operations: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind, "operations": list(self.operations)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityResolutionRule:
        _fields(data, {"kind", "operations"}, "entity-resolution rule")
        kind = _choice(data.get("kind"), "rule kind", frozenset({"normalized_exact"}))
        operations = _string_array(data.get("operations"), "rule operations")
        if not operations or len(operations) > _MAX_OPERATIONS:
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                f"A rule requires between 1 and {_MAX_OPERATIONS} operations.",
            )
        if len(operations) != len(set(operations)):
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Entity-resolution rule operations must be unique.",
            )
        unknown = set(operations) - ENTITY_RESOLUTION_OPERATIONS
        if unknown:
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Unsupported entity-resolution rule operation: " + sorted(unknown)[0],
            )
        return cls(kind=kind, operations=operations)


@dataclass(frozen=True, slots=True)
class EntityResolutionEvidence:
    level: str
    evaluated_count: int
    matched_count: int
    collision_count: int
    counterexample_count: int
    coverage: float
    collision_rate: float
    confidence: float

    def to_dict(self) -> dict[str, object]:
        return {
            "collision_count": self.collision_count,
            "collision_rate": self.collision_rate,
            "confidence": self.confidence,
            "counterexample_count": self.counterexample_count,
            "coverage": self.coverage,
            "evaluated_count": self.evaluated_count,
            "level": self.level,
            "matched_count": self.matched_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityResolutionEvidence:
        _fields(
            data,
            {
                "collision_count",
                "collision_rate",
                "confidence",
                "counterexample_count",
                "coverage",
                "evaluated_count",
                "level",
                "matched_count",
            },
            "entity-resolution evidence",
        )
        evidence = cls(
            level=_choice(
                data.get("level"),
                "evidence level",
                ENTITY_RESOLUTION_EVIDENCE_LEVELS,
            ),
            evaluated_count=_integer(data.get("evaluated_count"), "evaluated_count"),
            matched_count=_integer(data.get("matched_count"), "matched_count"),
            collision_count=_integer(data.get("collision_count"), "collision_count"),
            counterexample_count=_integer(
                data.get("counterexample_count"),
                "counterexample_count",
            ),
            coverage=_rate(data.get("coverage"), "coverage"),
            collision_rate=_rate(data.get("collision_rate"), "collision_rate"),
            confidence=_rate(data.get("confidence"), "confidence"),
        )
        _validate_evidence(evidence)
        return evidence


@dataclass(frozen=True, slots=True)
class EntityResolutionProvenance:
    run_id: str
    producer: str

    def to_dict(self) -> dict[str, str]:
        return {"producer": self.producer, "run_id": self.run_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityResolutionProvenance:
        _fields(data, {"producer", "run_id"}, "entity-resolution provenance")
        return cls(
            run_id=_identifier(data.get("run_id"), "run_id"),
            producer=_identifier(data.get("producer"), "producer"),
        )


@dataclass(frozen=True, slots=True)
class EntityResolutionReview:
    decision: str
    reason: str
    source: str = "human"

    def to_dict(self) -> dict[str, str]:
        return {"decision": self.decision, "reason": self.reason, "source": self.source}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityResolutionReview:
        _fields(data, {"decision", "reason", "source"}, "entity-resolution review")
        source = _text(data.get("source"), "review source")
        if source != "human":
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Entity-resolution review source must be human.",
            )
        return cls(
            decision=_choice(
                data.get("decision"),
                "review decision",
                frozenset({"approve", "reject"}),
            ),
            reason=_text(data.get("reason"), "review reason", limit=_MAX_REASON_LENGTH),
        )


@dataclass(frozen=True, slots=True)
class EntityResolutionCandidate:
    id: str
    graph_name: str
    graph_revision: str
    source_field_id: str
    target_field_id: str
    rule: EntityResolutionRule
    evidence: EntityResolutionEvidence
    provenance: EntityResolutionProvenance
    state: str = "candidate"
    review: EntityResolutionReview | None = None
    contract_version: str = ENTITY_RESOLUTION_CONTRACT_VERSION

    @property
    def revision(self) -> str:
        payload = json.dumps(
            self.to_dict(include_revision=False),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def human_reviewed(self) -> bool:
        return self.review is not None

    def to_dict(self, *, include_revision: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": self.contract_version,
            "evidence": self.evidence.to_dict(),
            "graph": {"name": self.graph_name, "revision": self.graph_revision},
            "id": self.id,
            "provenance": self.provenance.to_dict(),
            "review": self.review.to_dict() if self.review else None,
            "rule": self.rule.to_dict(),
            "source_field_id": self.source_field_id,
            "state": self.state,
            "target_field_id": self.target_field_id,
        }
        if include_revision:
            payload["revision"] = self.revision
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EntityResolutionCandidate:
        _fields(
            data,
            {
                "contract_version",
                "evidence",
                "graph",
                "id",
                "provenance",
                "review",
                "rule",
                "source_field_id",
                "state",
                "target_field_id",
            },
            "entity-resolution candidate",
            optional={"revision"},
        )
        if data.get("contract_version") != ENTITY_RESOLUTION_CONTRACT_VERSION:
            raise EntityResolutionFailure(
                "unsupported_entity_resolution",
                "Unsupported TAREL entity-resolution candidate contract.",
            )
        graph = _object(data.get("graph"), "candidate graph")
        _fields(graph, {"name", "revision"}, "candidate graph")
        review_value = data.get("review")
        if review_value is not None and not isinstance(review_value, dict):
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Entity-resolution review must be an object or null.",
            )
        candidate = cls(
            id=_identifier(data.get("id"), "candidate id"),
            graph_name=_text(graph.get("name"), "graph name"),
            graph_revision=_sha256(graph.get("revision"), "graph revision"),
            source_field_id=_text(data.get("source_field_id"), "source_field_id"),
            target_field_id=_text(data.get("target_field_id"), "target_field_id"),
            rule=EntityResolutionRule.from_dict(_object(data.get("rule"), "rule")),
            evidence=EntityResolutionEvidence.from_dict(
                _object(data.get("evidence"), "evidence")
            ),
            provenance=EntityResolutionProvenance.from_dict(
                _object(data.get("provenance"), "provenance")
            ),
            state=_choice(data.get("state"), "candidate state", ENTITY_RESOLUTION_STATES),
            review=EntityResolutionReview.from_dict(review_value) if review_value else None,
        )
        validate_entity_resolution_candidate(candidate)
        expected_revision = data.get("revision")
        if expected_revision is not None and expected_revision != candidate.revision:
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Entity-resolution candidate revision does not match its content.",
            )
        return candidate


@dataclass(frozen=True, slots=True)
class EntityResolutionMatch:
    candidate: EntityResolutionCandidate
    source_reference: str
    target_reference: str

    @property
    def usage(self) -> str:
        return "confirmed" if self.candidate.state == "reviewed" else "exploratory_only"

    @property
    def requires_runtime_validation(self) -> bool:
        return self.candidate.state != "reviewed"

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.to_dict(),
            "requires_runtime_validation": self.requires_runtime_validation,
            "source": self.source_reference,
            "target": self.target_reference,
            "usage": self.usage,
            "warning": (
                None
                if self.candidate.state == "reviewed"
                else "Unreviewed hypothesis; probe it at runtime before presenting a result."
            ),
        }


def validate_entity_resolution_candidate(candidate: EntityResolutionCandidate) -> None:
    if candidate.contract_version != ENTITY_RESOLUTION_CONTRACT_VERSION:
        raise EntityResolutionFailure(
            "unsupported_entity_resolution",
            "Unsupported TAREL entity-resolution candidate contract.",
        )
    _identifier(candidate.id, "candidate id")
    _text(candidate.graph_name, "graph name")
    _sha256(candidate.graph_revision, "graph revision")
    if candidate.source_field_id == candidate.target_field_id:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "Entity-resolution endpoints must be different fields.",
        )
    EntityResolutionRule.from_dict(candidate.rule.to_dict())
    EntityResolutionEvidence.from_dict(candidate.evidence.to_dict())
    EntityResolutionProvenance.from_dict(candidate.provenance.to_dict())
    if candidate.state == "candidate" and candidate.review is not None:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "An unreviewed candidate cannot contain a human review.",
        )
    expected_decision = {"reviewed": "approve", "rejected": "reject"}.get(candidate.state)
    if expected_decision is not None and (
        candidate.review is None or candidate.review.decision != expected_decision
    ):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"State {candidate.state} requires a matching human review.",
        )
    if candidate.review is not None:
        EntityResolutionReview.from_dict(candidate.review.to_dict())


def review_candidate(
    candidate: EntityResolutionCandidate,
    *,
    decision: str,
    reason: str,
) -> EntityResolutionCandidate:
    if candidate.state != "candidate":
        raise EntityResolutionFailure(
            "entity_resolution_already_reviewed",
            f"Entity-resolution candidate is already {candidate.state}: {candidate.id}",
        )
    review = EntityResolutionReview.from_dict(
        {"decision": decision, "reason": reason, "source": "human"}
    )
    changed = replace(
        candidate,
        state="reviewed" if decision == "approve" else "rejected",
        review=review,
    )
    validate_entity_resolution_candidate(changed)
    return changed


def _validate_evidence(evidence: EntityResolutionEvidence) -> None:
    if evidence.matched_count > evidence.evaluated_count:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "matched_count cannot exceed evaluated_count.",
        )
    if evidence.collision_count > evidence.matched_count:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "collision_count cannot exceed matched_count.",
        )
    if evidence.counterexample_count > evidence.evaluated_count:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "counterexample_count cannot exceed evaluated_count.",
        )
    if evidence.level == "proposed":
        if any(
            value != 0
            for value in (
                evidence.evaluated_count,
                evidence.matched_count,
                evidence.collision_count,
                evidence.counterexample_count,
                evidence.coverage,
                evidence.collision_rate,
            )
        ):
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Proposed evidence cannot claim evaluated rows or measured rates.",
            )
        return
    if evidence.evaluated_count == 0:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "Tested entity-resolution evidence requires evaluated rows.",
        )
    expected_coverage = evidence.matched_count / evidence.evaluated_count
    expected_collision_rate = (
        evidence.collision_count / evidence.matched_count
        if evidence.matched_count
        else 0.0
    )
    if not math.isclose(evidence.coverage, expected_coverage, abs_tol=1e-6):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "coverage does not match matched_count / evaluated_count.",
        )
    if not math.isclose(evidence.collision_rate, expected_collision_rate, abs_tol=1e-6):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "collision_rate does not match collision_count / matched_count.",
        )


def _fields(
    data: dict[str, Any],
    required: set[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> None:
    allowed = required | (optional or set())
    if set(data) - allowed or required - set(data):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} has unexpected or missing fields.",
        )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} must be an object.",
        )
    return value


def _text(value: object, label: str, *, limit: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} must be a non-empty string of at most {limit} characters.",
        )
    return value.strip()


def _identifier(value: object, label: str) -> str:
    clean = _text(value, label, limit=128)
    if not _IDENTIFIER.fullmatch(clean):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} may contain letters, numbers, dots, underscores, and hyphens.",
        )
    return clean


def _sha256(value: object, label: str) -> str:
    clean = _text(value, label, limit=64)
    if not _SHA256.fullmatch(clean):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} must be a lowercase SHA-256 value.",
        )
    return clean


def _choice(value: object, label: str, choices: frozenset[str]) -> str:
    clean = _text(value, label)
    if clean not in choices:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"Unsupported {label}: {clean}",
        )
    return clean


def _string_array(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} must be an array of strings.",
        )
    return tuple(_text(item, label) for item in value)


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} must be a non-negative integer.",
        )
    return value


def _rate(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} must be a number between 0 and 1.",
        )
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            f"{label} must be a finite number between 0 and 1.",
        )
    return result
