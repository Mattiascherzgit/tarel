"""Dependency-free persisted contracts for reviewed static lineage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_CONTRACT_VERSION = "tarel.lineage.v0.3"
_READABLE_CONTRACT_VERSIONS = frozenset({_CONTRACT_VERSION, "tarel.lineage.v0.2"})
_DEFINITION_KINDS = frozenset({"procedure", "query", "script"})
_OPERATIONS = frozenset({"call", "read"})
_WRITE_OPERATIONS = frozenset({"delete", "insert", "merge", "select_into", "truncate", "update"})
_SOURCE_ROLES = frozenset(
    {"audit", "business_data", "control", "deduplication", "filter", "lookup", "unknown"}
)
_EXCLUSION_KINDS = frozenset({"dynamic_sql", "local_intermediate", "unresolved"})
_STATES = frozenset({"draft", "rejected", "review_required", "validated"})


class LineageFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class LineageEvidence:
    source: str
    reference: str
    reason: str
    line_start: int
    line_end: int

    def to_dict(self) -> dict[str, object]:
        return {
            "line_end": self.line_end,
            "line_start": self.line_start,
            "reason": self.reason,
            "reference": self.reference,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class LineageWriteSource:
    target: str
    role: str
    via: tuple[str, ...]
    evidence: LineageEvidence

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence": self.evidence.to_dict(),
            "role": self.role,
            "target": self.target,
            "via": list(self.via),
        }


@dataclass(frozen=True, slots=True)
class LineageExcludedWrite:
    operation: str
    target: str
    disposition: str
    evidence: LineageEvidence

    def to_dict(self) -> dict[str, object]:
        return {
            "disposition": self.disposition,
            "evidence": self.evidence.to_dict(),
            "operation": self.operation,
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class LineageReview:
    decision: str
    reason: str
    source: str = "human"

    def to_dict(self) -> dict[str, str]:
        return {"decision": self.decision, "reason": self.reason, "source": self.source}


@dataclass(frozen=True, slots=True)
class LineageDefinition:
    id: str
    external_id: str
    kind: str
    name: str
    qualified_name: str
    language: str
    source_reference: str
    content_hash: str
    revision: str

    def to_dict(self) -> dict[str, str]:
        return {
            "content_hash": self.content_hash,
            "external_id": self.external_id,
            "id": self.id,
            "kind": self.kind,
            "language": self.language,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "revision": self.revision,
            "source_reference": self.source_reference,
        }


@dataclass(frozen=True, slots=True)
class LineageStep:
    id: str
    external_id: str
    name: str
    definition_id: str
    depends_on: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "definition_id": self.definition_id,
            "depends_on": list(self.depends_on),
            "external_id": self.external_id,
            "id": self.id,
            "name": self.name,
        }


@dataclass(frozen=True, slots=True)
class LineageAnalysis:
    definition_id: str
    definition_revision: str
    summary: str
    warnings: tuple[str, ...]
    excluded_writes: tuple[LineageExcludedWrite, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "definition_id": self.definition_id,
            "definition_revision": self.definition_revision,
            "excluded_writes": [item.to_dict() for item in self.excluded_writes],
            "summary": self.summary,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class LineageAnalysisFailure:
    definition_id: str
    definition_revision: str
    code: str
    provider: str
    model: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "definition_id": self.definition_id,
            "definition_revision": self.definition_revision,
            "model": self.model,
            "provider": self.provider,
        }


@dataclass(frozen=True, slots=True)
class LineageClaim:
    id: str
    definition_id: str
    operation: str
    target: str
    state: str
    evidence: LineageEvidence
    reviews: tuple[LineageReview, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "definition_id": self.definition_id,
            "evidence": self.evidence.to_dict(),
            "id": self.id,
            "operation": self.operation,
            "reviews": [item.to_dict() for item in self.reviews],
            "state": self.state,
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class LineageWriteUnit:
    id: str
    definition_id: str
    operation: str
    target: str
    state: str
    evidence: LineageEvidence
    sources: tuple[LineageWriteSource, ...]
    warnings: tuple[str, ...] = ()
    reviews: tuple[LineageReview, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "definition_id": self.definition_id,
            "evidence": self.evidence.to_dict(),
            "id": self.id,
            "operation": self.operation,
            "reviews": [item.to_dict() for item in self.reviews],
            "sources": [item.to_dict() for item in self.sources],
            "state": self.state,
            "target": self.target,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class LineageDocument:
    name: str
    source_kind: str
    source_name: str
    source_reference: str
    source_revision: str
    workflow_id: str
    workflow_name: str
    definitions: tuple[LineageDefinition, ...]
    steps: tuple[LineageStep, ...]
    analyses: tuple[LineageAnalysis, ...] = ()
    analysis_failures: tuple[LineageAnalysisFailure, ...] = ()
    claims: tuple[LineageClaim, ...] = ()
    write_units: tuple[LineageWriteUnit, ...] = ()
    contract_version: str = _CONTRACT_VERSION

    def definition_by_id(self) -> dict[str, LineageDefinition]:
        return {item.id: item for item in self.definitions}

    def claim_by_id(self) -> dict[str, LineageClaim]:
        return {item.id: item for item in self.claims}

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_failures": [item.to_dict() for item in self.analysis_failures],
            "analyses": [item.to_dict() for item in self.analyses],
            "claims": [item.to_dict() for item in self.claims],
            "contract_version": self.contract_version,
            "definitions": [item.to_dict() for item in self.definitions],
            "name": self.name,
            "source_kind": self.source_kind,
            "source_name": self.source_name,
            "source_reference": self.source_reference,
            "source_revision": self.source_revision,
            "steps": [item.to_dict() for item in self.steps],
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "write_units": [item.to_dict() for item in self.write_units],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LineageDocument:
        contract_version = data.get("contract_version")
        if contract_version not in _READABLE_CONTRACT_VERSIONS:
            raise LineageFailure("unsupported_lineage", "Unsupported TAREL lineage contract.")
        if contract_version == "tarel.lineage.v0.2":
            data = {**data, "analysis_failures": []}
        _fields(
            data,
            {
                "analysis_failures",
                "analyses",
                "claims",
                "contract_version",
                "definitions",
                "name",
                "source_kind",
                "source_name",
                "source_reference",
                "source_revision",
                "steps",
                "workflow_id",
                "workflow_name",
                "write_units",
            },
            "document",
        )
        try:
            document = cls(
                name=_text_value(data.get("name"), "name"),
                source_kind=_text_value(data.get("source_kind"), "source_kind"),
                source_name=_text_value(data.get("source_name"), "source_name"),
                source_reference=_text_value(data.get("source_reference"), "source_reference"),
                source_revision=_text_value(data.get("source_revision"), "source_revision"),
                workflow_id=_text_value(data.get("workflow_id"), "workflow_id"),
                workflow_name=_text_value(data.get("workflow_name"), "workflow_name"),
                definitions=tuple(
                    _definition(item) for item in _objects(data.get("definitions"), "definitions")
                ),
                steps=tuple(_step(item) for item in _objects(data.get("steps"), "steps")),
                analyses=tuple(
                    _analysis(item) for item in _objects(data.get("analyses"), "analyses")
                ),
                analysis_failures=tuple(
                    _analysis_failure(item)
                    for item in _objects(data.get("analysis_failures"), "analysis failures")
                ),
                claims=tuple(_claim(item) for item in _objects(data.get("claims"), "claims")),
                write_units=tuple(
                    _write_unit(item) for item in _objects(data.get("write_units"), "write_units")
                ),
            )
        except LineageFailure:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise LineageFailure("invalid_lineage", "Malformed lineage document.") from exc
        validate_lineage_document(document)
        return document


def validate_lineage_document(document: LineageDocument) -> None:
    for value, label in (
        (document.name, "name"),
        (document.source_kind, "source_kind"),
        (document.source_name, "source_name"),
        (document.source_reference, "source_reference"),
        (document.workflow_id, "workflow_id"),
        (document.workflow_name, "workflow_name"),
    ):
        _text_value(value, label)
    _sha256(document.source_revision, "source_revision")

    definition_ids: list[str] = []
    definition_external_ids: list[str] = []
    for item in document.definitions:
        for value, label in (
            (item.id, "definition id"),
            (item.external_id, "definition external_id"),
            (item.kind, "definition kind"),
            (item.name, "definition name"),
            (item.qualified_name, "definition qualified_name"),
            (item.language, "definition language"),
            (item.source_reference, "definition source_reference"),
        ):
            _text_value(value, label)
        if item.kind not in _DEFINITION_KINDS:
            raise LineageFailure("invalid_lineage", f"Unsupported definition kind: {item.kind}")
        _sha256(item.content_hash, "definition content_hash")
        _sha256(item.revision, "definition revision")
        definition_ids.append(item.id)
        definition_external_ids.append(item.external_id)
    _unique(definition_ids, "definition IDs")
    _unique(definition_external_ids, "definition external IDs")
    definitions = document.definition_by_id()

    step_ids: list[str] = []
    step_external_ids: list[str] = []
    for item in document.steps:
        for value, label in (
            (item.id, "step id"),
            (item.external_id, "step external_id"),
            (item.name, "step name"),
            (item.definition_id, "step definition_id"),
        ):
            _text_value(value, label)
        if item.definition_id not in definitions:
            raise LineageFailure(
                "invalid_lineage",
                "Workflow step references an unknown definition.",
            )
        if not all(isinstance(value, str) and value for value in item.depends_on):
            raise LineageFailure("invalid_lineage", "Step dependencies must be strings.")
        step_ids.append(item.id)
        step_external_ids.append(item.external_id)
    _unique(step_ids, "step IDs")
    _unique(step_external_ids, "step external IDs")
    _validate_step_graph(document.steps)

    analysis_ids: list[str] = []
    for item in document.analyses:
        _text_value(item.definition_id, "analysis definition_id")
        if item.definition_id not in definitions:
            raise LineageFailure("invalid_lineage", "Analysis references an unknown definition.")
        _sha256(item.definition_revision, "analysis definition_revision")
        if item.definition_revision != definitions[item.definition_id].revision:
            raise LineageFailure(
                "invalid_lineage",
                "Analysis revision does not match its lineage definition.",
            )
        _text_value(item.summary, "analysis summary")
        if not all(isinstance(value, str) for value in item.warnings):
            raise LineageFailure("invalid_lineage", "Analysis warnings must be strings.")
        for excluded in item.excluded_writes:
            if excluded.operation not in _WRITE_OPERATIONS:
                raise LineageFailure("invalid_lineage", "Invalid excluded-write operation.")
            if excluded.disposition not in _EXCLUSION_KINDS:
                raise LineageFailure("invalid_lineage", "Invalid excluded-write disposition.")
            _text_value(excluded.target, "excluded-write target")
            _validate_evidence(excluded.evidence)
        analysis_ids.append(item.definition_id)
    _unique(analysis_ids, "analysis definition IDs")

    failure_ids: list[str] = []
    for item in document.analysis_failures:
        _text_value(item.definition_id, "analysis failure definition_id")
        if item.definition_id not in definitions:
            raise LineageFailure(
                "invalid_lineage",
                "Analysis failure references an unknown definition.",
            )
        _sha256(item.definition_revision, "analysis failure definition_revision")
        if item.definition_revision != definitions[item.definition_id].revision:
            raise LineageFailure(
                "invalid_lineage",
                "Analysis failure revision does not match its lineage definition.",
            )
        _text_value(item.code, "analysis failure code")
        _text_value(item.provider, "analysis failure provider")
        if item.model is not None:
            _text_value(item.model, "analysis failure model")
        failure_ids.append(item.definition_id)
    _unique(failure_ids, "analysis failure definition IDs")
    if set(failure_ids) & set(analysis_ids):
        raise LineageFailure(
            "invalid_lineage",
            "A definition cannot be both analyzed and failed.",
        )

    claim_ids: list[str] = []
    for item in document.claims:
        for value, label in (
            (item.id, "claim id"),
            (item.definition_id, "claim definition_id"),
            (item.operation, "claim operation"),
            (item.target, "claim target"),
            (item.state, "claim state"),
        ):
            _text_value(value, label)
        if item.definition_id not in definitions:
            raise LineageFailure("invalid_lineage", "Claim references an unknown definition.")
        if item.operation not in _OPERATIONS or item.state not in _STATES:
            raise LineageFailure("invalid_lineage", "Unsupported claim operation or state.")
        _validate_evidence(item.evidence)
        for review in item.reviews:
            _text_value(review.decision, "review decision")
            _text_value(review.reason, "review reason")
            _text_value(review.source, "review source")
            if review.decision not in {"reject", "validate"} or review.source != "human":
                raise LineageFailure("invalid_lineage", "Invalid lineage review.")
        if item.state == "draft" and item.reviews:
            raise LineageFailure("invalid_lineage", "Draft claims cannot contain reviews.")
        if item.state in {"rejected", "validated"}:
            expected = "reject" if item.state == "rejected" else "validate"
            if not item.reviews or item.reviews[-1].decision != expected:
                raise LineageFailure("invalid_lineage", "Claim state requires a matching review.")
        claim_ids.append(item.id)
    _unique(claim_ids, "claim IDs")

    write_unit_ids: list[str] = []
    for item in document.write_units:
        for value, label in (
            (item.id, "write-unit id"),
            (item.definition_id, "write-unit definition_id"),
            (item.operation, "write-unit operation"),
            (item.target, "write-unit target"),
            (item.state, "write-unit state"),
        ):
            _text_value(value, label)
        if item.definition_id not in definitions:
            raise LineageFailure("invalid_lineage", "Write unit references an unknown definition.")
        if item.operation not in _WRITE_OPERATIONS or item.state not in _STATES:
            raise LineageFailure("invalid_lineage", "Unsupported write-unit operation or state.")
        _validate_evidence(item.evidence)
        if not all(isinstance(value, str) for value in item.warnings):
            raise LineageFailure("invalid_lineage", "Write-unit warnings must be strings.")
        source_keys = []
        for source in item.sources:
            _text_value(source.target, "write source target")
            if source.role not in _SOURCE_ROLES:
                raise LineageFailure("invalid_lineage", "Unsupported write-source role.")
            if not all(isinstance(value, str) and value.strip() for value in source.via):
                raise LineageFailure("invalid_lineage", "Write-source via values must be strings.")
            _unique(list(source.via), "write-source via values")
            _validate_evidence(source.evidence)
            source_keys.append(
                (
                    source.target.casefold(),
                    source.evidence.line_start,
                    source.evidence.line_end,
                )
            )
        if len(source_keys) != len(set(source_keys)):
            raise LineageFailure("invalid_lineage", "Write unit contains duplicate sources.")
        _validate_reviews(item.state, item.reviews, "write unit")
        write_unit_ids.append(item.id)
    _unique(write_unit_ids, "write-unit IDs")


def _definition(data: dict[str, Any]) -> LineageDefinition:
    _fields(
        data,
        {
            "content_hash",
            "external_id",
            "id",
            "kind",
            "language",
            "name",
            "qualified_name",
            "revision",
            "source_reference",
        },
        "definition",
    )
    return LineageDefinition(**data)


def _step(data: dict[str, Any]) -> LineageStep:
    _fields(data, {"definition_id", "depends_on", "external_id", "id", "name"}, "step")
    values = {key: value for key, value in data.items() if key != "depends_on"}
    return LineageStep(**values, depends_on=_strings(data.get("depends_on"), "step depends_on"))


def _analysis(data: dict[str, Any]) -> LineageAnalysis:
    _fields(
        data,
        {"definition_id", "definition_revision", "excluded_writes", "summary", "warnings"},
        "analysis",
    )
    values = {
        key: value for key, value in data.items() if key not in {"excluded_writes", "warnings"}
    }
    return LineageAnalysis(
        **values,
        warnings=_strings(data.get("warnings"), "analysis warnings"),
        excluded_writes=tuple(
            _excluded_write(item)
            for item in _objects(data.get("excluded_writes"), "excluded_writes")
        ),
    )


def _analysis_failure(data: dict[str, Any]) -> LineageAnalysisFailure:
    _fields(
        data,
        {"code", "definition_id", "definition_revision", "model", "provider"},
        "analysis failure",
    )
    model = data.get("model")
    if model is not None and not isinstance(model, str):
        raise LineageFailure("invalid_lineage", "Analysis failure model must be a string or null.")
    return LineageAnalysisFailure(
        definition_id=data.get("definition_id"),
        definition_revision=data.get("definition_revision"),
        code=data.get("code"),
        provider=data.get("provider"),
        model=model,
    )


def _claim(data: dict[str, Any]) -> LineageClaim:
    _fields(
        data,
        {"definition_id", "evidence", "id", "operation", "reviews", "state", "target"},
        "claim",
    )
    reviews = tuple(_review(item) for item in _objects(data.get("reviews"), "claim reviews"))
    values = {key: value for key, value in data.items() if key not in {"evidence", "reviews"}}
    return LineageClaim(
        **values,
        evidence=_evidence(data.get("evidence"), "claim evidence"),
        reviews=reviews,
    )


def _write_unit(data: dict[str, Any]) -> LineageWriteUnit:
    _fields(
        data,
        {
            "definition_id",
            "evidence",
            "id",
            "operation",
            "reviews",
            "sources",
            "state",
            "target",
            "warnings",
        },
        "write unit",
    )
    evidence = _evidence(data.get("evidence"), "write-unit evidence")
    reviews = tuple(_review(item) for item in _objects(data.get("reviews"), "write-unit reviews"))
    values = {
        key: value
        for key, value in data.items()
        if key not in {"evidence", "reviews", "sources", "warnings"}
    }
    return LineageWriteUnit(
        **values,
        evidence=evidence,
        reviews=reviews,
        sources=tuple(
            _write_source(item) for item in _objects(data.get("sources"), "write-unit sources")
        ),
        warnings=_strings(data.get("warnings"), "write-unit warnings"),
    )


def _write_source(data: dict[str, Any]) -> LineageWriteSource:
    _fields(data, {"evidence", "role", "target", "via"}, "write source")
    return LineageWriteSource(
        target=data.get("target"),
        role=data.get("role"),
        via=_strings(data.get("via"), "write-source via"),
        evidence=_evidence(data.get("evidence"), "write-source evidence"),
    )


def _excluded_write(data: dict[str, Any]) -> LineageExcludedWrite:
    _fields(data, {"disposition", "evidence", "operation", "target"}, "excluded write")
    return LineageExcludedWrite(
        disposition=data.get("disposition"),
        operation=data.get("operation"),
        target=data.get("target"),
        evidence=_evidence(data.get("evidence"), "excluded-write evidence"),
    )


def _evidence(value: Any, label: str) -> LineageEvidence:
    if not isinstance(value, dict):
        raise LineageFailure("invalid_lineage", f"Lineage {label} must be an object.")
    _fields(value, {"line_end", "line_start", "reason", "reference", "source"}, label)
    return LineageEvidence(**value)


def _review(data: dict[str, Any]) -> LineageReview:
    _fields(data, {"decision", "reason", "source"}, "review")
    return LineageReview(**data)


def _validate_evidence(item: LineageEvidence) -> None:
    for value, label in (
        (item.source, "evidence source"),
        (item.reference, "evidence reference"),
        (item.reason, "evidence reason"),
    ):
        _text_value(value, label)
    if not _positive(item.line_start) or not _positive(item.line_end):
        raise LineageFailure("invalid_lineage", "Evidence lines must be positive integers.")
    if item.line_end < item.line_start:
        raise LineageFailure("invalid_lineage", "Evidence line range is invalid.")


def _validate_reviews(
    state: str,
    reviews: tuple[LineageReview, ...],
    label: str,
) -> None:
    for review in reviews:
        _text_value(review.decision, f"{label} review decision")
        _text_value(review.reason, f"{label} review reason")
        _text_value(review.source, f"{label} review source")
        if review.decision not in {"reject", "validate"} or review.source != "human":
            raise LineageFailure("invalid_lineage", f"Invalid {label} review.")
    if state == "draft" and reviews:
        raise LineageFailure("invalid_lineage", f"Draft {label} cannot contain reviews.")
    if state in {"rejected", "validated"}:
        expected = "reject" if state == "rejected" else "validate"
        if not reviews or reviews[-1].decision != expected:
            raise LineageFailure("invalid_lineage", f"{label.capitalize()} state requires review.")


def _validate_step_graph(steps: tuple[LineageStep, ...]) -> None:
    known = {item.id for item in steps}
    remaining: dict[str, set[str]] = {}
    for item in steps:
        dependencies = set(item.depends_on)
        if len(dependencies) != len(item.depends_on) or item.id in dependencies:
            raise LineageFailure("invalid_lineage", "Invalid workflow step dependencies.")
        if not dependencies <= known:
            raise LineageFailure("invalid_lineage", "Workflow step has unknown dependencies.")
        remaining[item.id] = dependencies
    while remaining:
        ready = {key for key, values in remaining.items() if not values}
        if not ready:
            raise LineageFailure("invalid_lineage", "Workflow dependencies contain a cycle.")
        remaining = {key: values - ready for key, values in remaining.items() if key not in ready}


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise LineageFailure("invalid_lineage", f"Lineage {label} must contain objects.")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LineageFailure("invalid_lineage", f"Lineage {label} must contain strings.")
    return tuple(value)


def _fields(data: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(data))
    unexpected = sorted(set(data) - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if unexpected:
            details.append(f"unsupported: {', '.join(unexpected)}")
        raise LineageFailure(
            "invalid_lineage",
            f"Invalid lineage {label} fields ({'; '.join(details)}).",
        )


def _text_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LineageFailure("invalid_lineage", f"Lineage {label} must be a non-empty string.")
    return value


def _sha256(value: Any, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise LineageFailure("invalid_lineage", f"Lineage {label} must be a SHA-256 value.")


def _positive(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise LineageFailure("invalid_lineage", f"Lineage contains duplicate {label}.")
