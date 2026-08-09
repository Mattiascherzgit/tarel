"""Deterministic transformations for write-centred static lineage."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from tarel.lineage.contracts import (
    LineageAnalysis,
    LineageAnalysisFailure,
    LineageClaim,
    LineageDefinition,
    LineageDocument,
    LineageEvidence,
    LineageExcludedWrite,
    LineageFailure,
    LineageMaterialization,
    LineageStep,
    LineageWriteSource,
    LineageWriteUnit,
    validate_lineage_document,
)
from tarel.lineage.coverage import write_markers
from tarel.lineage.source import LineageInput, SourceDefinition, stable_id
from tarel.lineage.tasks import lineage_task, require_current_source

_IDENTIFIER = re.compile(r"[a-z0-9_#$]+(?:\s*\.\s*[a-z0-9_#$]+)*", re.IGNORECASE)
_WRITE_OPERATIONS = frozenset({"delete", "insert", "merge", "select_into", "truncate", "update"})
_SOURCE_ROLES = frozenset(
    {"audit", "business_data", "control", "deduplication", "filter", "lookup", "unknown"}
)
_EXCLUSION_KINDS = frozenset({"dynamic_sql", "local_intermediate", "unresolved"})
_INVALID_TARGETS = frozenset(
    {
        "delete",
        "exec",
        "execute",
        "from",
        "insert",
        "merge",
        "n/a",
        "none",
        "null",
        "select",
        "truncate",
        "unknown",
        "update",
    }
)


@dataclass(frozen=True, slots=True)
class ProcessCall:
    target: str
    state: str

    def to_dict(self) -> dict[str, str]:
        return {"state": self.state, "target": self.target}


@dataclass(frozen=True, slots=True)
class ProcessStep:
    id: str
    name: str
    definition_id: str
    definition: str
    depends_on: tuple[str, ...]
    calls: tuple[ProcessCall, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "calls": [item.to_dict() for item in self.calls],
            "definition": self.definition,
            "definition_id": self.definition_id,
            "depends_on": list(self.depends_on),
            "id": self.id,
            "name": self.name,
        }


@dataclass(frozen=True, slots=True)
class TableLineage:
    source: str
    target: str
    via_definition: str
    state: str
    write_unit_id: str
    role: str
    via: tuple[str, ...]
    derivation: str = "explicit_write_unit"

    def to_dict(self) -> dict[str, object]:
        return {
            "derivation": self.derivation,
            "role": self.role,
            "source": self.source,
            "state": self.state,
            "target": self.target,
            "via": list(self.via),
            "via_definition": self.via_definition,
            "write_unit_id": self.write_unit_id,
        }


def build_lineage(name: str, source: LineageInput) -> LineageDocument:
    by_external = source.definition_by_external_id()
    definitions = tuple(
        sorted(
            (
                LineageDefinition(
                    id=item.id,
                    external_id=item.external_id,
                    kind=item.kind,
                    name=item.name,
                    qualified_name=item.qualified_name,
                    language=item.language,
                    source_reference=item.source_reference,
                    content_hash=item.content_hash,
                    revision=item.revision,
                )
                for item in source.definitions
            ),
            key=lambda item: item.id,
        )
    )
    steps = tuple(
        LineageStep(
            id=item.id,
            external_id=item.external_id,
            name=item.name,
            definition_id=by_external[item.definition_external_id].id,
            depends_on=tuple(
                next(step.id for step in source.steps if step.external_id == dependency)
                for dependency in item.depends_on_external_ids
            ),
        )
        for item in source.steps
    )
    materializations = tuple(
        sorted(
            (
                LineageMaterialization(
                    id=stable_id(
                        "materialization",
                        item.mode,
                        f"{by_external[item.definition_external_id].id}\n"
                        f"{item.target}\n{item.source_reference}",
                    ),
                    definition_id=by_external[item.definition_external_id].id,
                    target=item.target,
                    mode=item.mode,
                    state="draft",
                    evidence=LineageEvidence(
                        source="declared_materialization",
                        reference=item.source_reference,
                        reason=(
                            f"The source manifest declares this definition as a {item.mode} "
                            "materialization target."
                        ),
                        line_start=1,
                        line_end=1,
                    ),
                )
                for item in source.materializations
            ),
            key=lambda item: item.id,
        )
    )
    claims = tuple(
        sorted(
            (
                LineageClaim(
                    id=stable_id(
                        "claim",
                        item.operation,
                        f"{by_external[item.definition_external_id].id}\n"
                        f"{item.target}\n{item.source_reference}\n"
                        f"{item.line_start}\n{item.line_end}",
                    ),
                    definition_id=by_external[item.definition_external_id].id,
                    operation=item.operation,
                    target=item.target,
                    state="draft",
                    evidence=LineageEvidence(
                        source="declared_reference",
                        reference=(
                            f"{item.source_reference}:{item.line_start}-{item.line_end}"
                        ),
                        reason=item.reason,
                        line_start=item.line_start,
                        line_end=item.line_end,
                    ),
                )
                for item in source.observations
            ),
            key=lambda item: item.id,
        )
    )
    document = LineageDocument(
        name=name,
        source_kind=source.source_kind,
        source_name=source.source_name,
        source_reference=source.source_reference,
        source_revision=source.revision,
        workflow_id=source.workflow_id,
        workflow_name=source.workflow_name,
        definitions=definitions,
        steps=steps,
        claims=claims,
        materializations=materializations,
    )
    validate_lineage_document(document)
    return document


def apply_lineage_proposal(
    document: LineageDocument,
    source: LineageInput,
    payload: dict[str, Any],
) -> LineageDocument:
    require_current_source(document, source)
    _fields(payload, {"analysis", "definition_id", "task_id"}, "proposal")
    definition_id = _text(payload.get("definition_id"), "definition_id")
    definition = source.definition_by_id().get(definition_id)
    if definition is None:
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Unknown lineage definition: {definition_id}",
        )
    if _text(payload.get("task_id"), "task_id") != lineage_task(document, definition).id:
        raise LineageFailure(
            "stale_lineage_proposal",
            "Lineage proposal does not match the current definition.",
        )
    analysis_data = _mapping(payload.get("analysis"), "analysis")
    _fields(
        analysis_data,
        {"excluded_writes", "observations", "summary", "warnings", "writes"},
        "analysis",
    )
    warnings = _strings(analysis_data.get("warnings"), "warnings")
    proposed_claims = tuple(
        _proposal_claim(definition, item)
        for item in _mappings(analysis_data.get("observations"), "observations")
    )
    proposed_units = tuple(
        _proposal_write_unit(definition, item)
        for item in _mappings(analysis_data.get("writes"), "writes")
    )
    excluded = tuple(
        _proposal_excluded_write(definition, item)
        for item in _mappings(analysis_data.get("excluded_writes"), "excluded_writes")
    )
    _reject_duplicates(proposed_claims, proposed_units, excluded)
    _validate_write_coverage(definition, proposed_units, excluded)
    replaceable_claims = tuple(
        item for item in document.claims if item.evidence.source != "declared_reference"
    )
    if any(
        item.definition_id == definition_id
        and item.reviews
        and item.state != "review_required"
        for item in (*replaceable_claims, *document.write_units)
    ):
        raise LineageFailure(
            "reviewed_lineage_item",
            "A new proposal cannot overwrite human-reviewed lineage items.",
        )

    analyses = [item for item in document.analyses if item.definition_id != definition_id]
    analyses.append(
        LineageAnalysis(
            definition_id=definition_id,
            definition_revision=definition.revision,
            summary=_text(analysis_data.get("summary"), "summary"),
            warnings=tuple(item.strip() for item in warnings if item.strip()),
            excluded_writes=excluded,
        )
    )
    claims = [
        item
        for item in document.claims
        if item.definition_id != definition_id
        or item.evidence.source == "declared_reference"
    ]
    declared_keys = {
        (item.operation.casefold(), item.target.casefold())
        for item in claims
        if item.definition_id == definition_id and item.evidence.source == "declared_reference"
    }
    claims.extend(
        item
        for item in proposed_claims
        if (item.operation.casefold(), item.target.casefold()) not in declared_keys
    )
    write_units = [item for item in document.write_units if item.definition_id != definition_id]
    write_units.extend(proposed_units)
    updated = replace(
        document,
        analyses=tuple(sorted(analyses, key=lambda item: item.definition_id)),
        analysis_failures=tuple(
            item for item in document.analysis_failures if item.definition_id != definition_id
        ),
        claims=tuple(sorted(claims, key=lambda item: item.id)),
        write_units=tuple(sorted(write_units, key=lambda item: item.id)),
    )
    validate_lineage_document(updated)
    return updated


def record_lineage_analysis_failure(
    document: LineageDocument,
    definition_id: str,
    *,
    code: str,
    provider: str,
    model: str | None,
) -> LineageDocument:
    definition = document.definition_by_id().get(definition_id)
    if definition is None:
        raise LineageFailure(
            "invalid_lineage_analysis_failure",
            f"Unknown lineage definition: {definition_id}",
        )
    failures = [
        item for item in document.analysis_failures if item.definition_id != definition_id
    ]
    failures.append(
        LineageAnalysisFailure(
            definition_id=definition_id,
            definition_revision=definition.revision,
            code=code,
            provider=provider,
            model=model,
        )
    )
    updated = replace(
        document,
        analyses=tuple(
            item for item in document.analyses if item.definition_id != definition_id
        ),
        analysis_failures=tuple(sorted(failures, key=lambda item: item.definition_id)),
    )
    validate_lineage_document(updated)
    return updated


def process_view(document: LineageDocument) -> tuple[ProcessStep, ...]:
    definitions = document.definition_by_id()
    step_by_id = {item.id: item for item in document.steps}
    result = []
    for step in _ordered_steps(document.steps):
        calls = tuple(
            ProcessCall(item.target, item.state)
            for item in sorted(document.claims, key=lambda claim: claim.id)
            if item.definition_id == step.definition_id
            and item.operation == "call"
            and item.state != "rejected"
        )
        result.append(
            ProcessStep(
                id=step.id,
                name=step.name,
                definition_id=step.definition_id,
                definition=definitions[step.definition_id].qualified_name,
                depends_on=tuple(step_by_id[item].name for item in step.depends_on),
                calls=calls,
            )
        )
    return tuple(result)


def table_lineage(document: LineageDocument) -> tuple[TableLineage, ...]:
    definitions = document.definition_by_id()
    result = {
        TableLineage(
            source=source.target,
            target=unit.target,
            via_definition=definitions[unit.definition_id].qualified_name,
            state=unit.state,
            write_unit_id=unit.id,
            role=source.role,
            via=source.via,
        )
        for unit in document.write_units
        if unit.state != "rejected"
        for source in unit.sources
    }
    result.update(
        TableLineage(
            source=claim.target,
            target=materialization.target,
            via_definition=definitions[materialization.definition_id].qualified_name,
            state=(
                "review_required"
                if "review_required" in {claim.state, materialization.state}
                else "draft"
                if "draft" in {claim.state, materialization.state}
                else "validated"
            ),
            write_unit_id=materialization.id,
            role="unknown",
            via=(),
            derivation="declared_materialization",
        )
        for materialization in document.materializations
        if materialization.state != "rejected"
        for claim in document.claims
        if claim.definition_id == materialization.definition_id
        and claim.operation == "read"
        and claim.state != "rejected"
    )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                item.source.casefold(),
                item.target.casefold(),
                item.via_definition.casefold(),
                item.write_unit_id,
            ),
        )
    )


def _proposal_claim(definition: SourceDefinition, data: dict[str, Any]) -> LineageClaim:
    _fields(data, {"line_end", "line_start", "operation", "reason", "target"}, "observation")
    operation = _text(data.get("operation"), "operation")
    if operation not in {"call", "read"}:
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Unsupported observation operation: {operation}",
        )
    target = _text(data.get("target"), "target")
    _reject_placeholder_target(target)
    evidence = _proposal_evidence(definition, data, target)
    return LineageClaim(
        id=stable_id(
            "claim",
            operation,
            f"{definition.id}\n{'.'.join(_parts(target))}\n{evidence.line_start}\n"
            f"{definition.revision}",
        ),
        definition_id=definition.id,
        operation=operation,
        target=target,
        state="draft",
        evidence=evidence,
    )


def _proposal_write_unit(
    definition: SourceDefinition,
    data: dict[str, Any],
) -> LineageWriteUnit:
    _fields(
        data,
        {
            "line_end",
            "line_start",
            "operation",
            "reason",
            "sources",
            "target",
            "warnings",
        },
        "write unit",
    )
    operation = _text(data.get("operation"), "write operation")
    if operation not in _WRITE_OPERATIONS:
        raise LineageFailure("invalid_lineage_proposal", f"Unsupported write: {operation}")
    target = _text(data.get("target"), "write target")
    _reject_placeholder_target(target)
    evidence = _proposal_evidence(definition, data, target)
    sources = tuple(
        _proposal_write_source(definition, item)
        for item in _mappings(data.get("sources"), "write sources")
    )
    warnings = _strings(data.get("warnings"), "write warnings")
    return LineageWriteUnit(
        id=stable_id(
            "write",
            operation,
            f"{definition.id}\n{'.'.join(_parts(target))}\n{evidence.line_start}\n"
            f"{definition.revision}",
        ),
        definition_id=definition.id,
        operation=operation,
        target=target,
        state="draft",
        evidence=evidence,
        sources=sources,
        warnings=tuple(item.strip() for item in warnings if item.strip()),
    )


def _proposal_write_source(
    definition: SourceDefinition,
    data: dict[str, Any],
) -> LineageWriteSource:
    _fields(
        data,
        {"line_end", "line_start", "reason", "role", "target", "via"},
        "write source",
    )
    target = _text(data.get("target"), "write source target")
    _reject_placeholder_target(target)
    role = _text(data.get("role"), "write source role")
    if role not in _SOURCE_ROLES:
        raise LineageFailure("invalid_lineage_proposal", f"Unsupported source role: {role}")
    return LineageWriteSource(
        target=target,
        role=role,
        via=_strings(data.get("via"), "write source via"),
        evidence=_proposal_evidence(definition, data, target),
    )


def _proposal_excluded_write(
    definition: SourceDefinition,
    data: dict[str, Any],
) -> LineageExcludedWrite:
    _fields(
        data,
        {"disposition", "line_end", "line_start", "operation", "reason", "target"},
        "excluded write",
    )
    operation = _text(data.get("operation"), "excluded-write operation")
    disposition = _text(data.get("disposition"), "excluded-write disposition")
    if operation not in _WRITE_OPERATIONS or disposition not in _EXCLUSION_KINDS:
        raise LineageFailure("invalid_lineage_proposal", "Invalid excluded write.")
    target = _text(data.get("target"), "excluded-write target")
    return LineageExcludedWrite(
        operation=operation,
        target=target,
        disposition=disposition,
        evidence=_proposal_evidence(definition, data, target),
    )


def _proposal_evidence(
    definition: SourceDefinition,
    data: dict[str, Any],
    target: str,
) -> LineageEvidence:
    start = data.get("line_start")
    end = data.get("line_end")
    lines = definition.content.splitlines()
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or start < 1
        or end < start
        or end > len(lines)
    ):
        raise LineageFailure(
            "invalid_lineage_proposal",
            "Lineage evidence lines are outside the source.",
        )
    cited = "\n".join(lines[start - 1 : end])
    if not _reference_present(target, cited):
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Lineage target is not present in the cited source lines: {target}",
        )
    return LineageEvidence(
        source="coding_agent",
        reference=f"{definition.source_reference}:{start}-{end}",
        reason=_text(data.get("reason"), "reason"),
        line_start=start,
        line_end=end,
    )


def _validate_write_coverage(
    definition: SourceDefinition,
    units: tuple[LineageWriteUnit, ...],
    excluded: tuple[LineageExcludedWrite, ...],
) -> None:
    coverage = [
        (item.operation, item.evidence.line_start, item.evidence.line_end)
        for item in (*units, *excluded)
    ]
    markers = write_markers(definition.content)
    uncovered = [
        marker
        for marker in markers
        if not any(
            operation == marker.operation and start == marker.line <= end
            for operation, start, end in coverage
        )
    ]
    if uncovered:
        details = ", ".join(f"{item.operation}@{item.line}" for item in uncovered[:12])
        raise LineageFailure(
            "incomplete_write_coverage",
            f"Proposal did not classify potential write statements: {details}",
        )
    unmatched = [
        (operation, start)
        for operation, start, end in coverage
        if not any(
            marker.operation == operation and start == marker.line <= end for marker in markers
        )
    ]
    if unmatched:
        operation, line = unmatched[0]
        raise LineageFailure(
            "invalid_write_coverage",
            f"Proposal classified a write not found by the coverage guard: {operation}@{line}",
        )


def _reject_duplicates(
    claims: tuple[LineageClaim, ...],
    units: tuple[LineageWriteUnit, ...],
    excluded: tuple[LineageExcludedWrite, ...],
) -> None:
    for values, label in (
        ([item.id for item in claims], "observations"),
        ([item.id for item in units], "write units"),
        (
            [
                (item.operation, item.target.casefold(), item.evidence.line_start)
                for item in excluded
            ],
            "excluded writes",
        ),
    ):
        if len(values) != len(set(values)):
            raise LineageFailure(
                "invalid_lineage_proposal",
                f"Proposal contains duplicate {label}.",
            )


def _ordered_steps(steps: tuple[LineageStep, ...]) -> tuple[LineageStep, ...]:
    order = {item.id: index for index, item in enumerate(steps)}
    remaining = {item.id: set(item.depends_on) for item in steps}
    by_id = {item.id: item for item in steps}
    result = []
    while remaining:
        ready = sorted(
            (key for key, dependencies in remaining.items() if not dependencies),
            key=lambda key: (order[key], key),
        )
        if not ready:
            raise LineageFailure("invalid_lineage", "Workflow dependencies contain a cycle.")
        result.extend(by_id[key] for key in ready)
        ready_ids = set(ready)
        remaining = {
            key: dependencies - ready_ids
            for key, dependencies in remaining.items()
            if key not in ready_ids
        }
    return tuple(result)


def _reference_present(reference: str, evidence: str) -> bool:
    target = _parts(reference)
    if not target:
        return False
    for match in _IDENTIFIER.finditer(_unquote(evidence)):
        candidate = _parts(match.group())
        if len(target) <= len(candidate) and candidate[-len(target) :] == target:
            return True
    return False


def _reject_placeholder_target(target: str) -> None:
    if target.casefold() in _INVALID_TARGETS:
        raise LineageFailure(
            "invalid_lineage_target",
            f"Lineage target must be an object identifier, not {target!r}.",
        )


def _parts(value: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s*\.\s*", ".", _unquote(value).casefold())
    return tuple(item for item in normalized.split(".") if item)


def _unquote(value: str) -> str:
    return value.translate(str.maketrans("", "", '[]"`'))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Lineage proposal {label} must be an object.",
        )
    return value


def _mappings(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Lineage proposal {label} must contain objects.",
        )
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Lineage proposal {label} must contain strings.",
        )
    if len(value) != len(set(value)):
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Lineage proposal {label} contains duplicates.",
        )
    return tuple(item.strip() for item in value if item.strip())


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Lineage proposal {label} must be a non-empty string.",
        )
    return value.strip()


def _fields(data: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(data))
    unexpected = sorted(set(data) - expected)
    if missing:
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Missing lineage {label} fields: {', '.join(missing)}",
        )
    if unexpected:
        raise LineageFailure(
            "invalid_lineage_proposal",
            f"Unsupported lineage {label} fields: {', '.join(unexpected)}",
        )
