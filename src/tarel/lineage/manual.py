"""Human-authored lineage overlays without modifying imported source documents."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from tarel.lineage.contracts import (
    LineageAnalysis,
    LineageDefinition,
    LineageDocument,
    LineageEvidence,
    LineageFailure,
    LineageStep,
    LineageWriteSource,
    LineageWriteUnit,
    validate_lineage_document,
)
from tarel.lineage.source import stable_id

_JOB_KINDS = frozenset({"procedure", "script"})
_WRITE_OPERATIONS = frozenset(
    {"delete", "insert", "merge", "select_into", "truncate", "update"}
)
_SOURCE_ROLES = frozenset(
    {"audit", "business_data", "control", "deduplication", "filter", "lookup", "unknown"}
)


def create_manual_lineage(name: str) -> LineageDocument:
    clean_name = _text(name, "lineage name")
    document = LineageDocument(
        name=clean_name,
        source_kind="manual",
        source_name=f"Manual lineage overlay: {clean_name}",
        source_reference=f"tarel:manual:{clean_name}",
        source_revision="0" * 64,
        workflow_id=stable_id("workflow", "manual", clean_name),
        workflow_name=f"Manual lineage: {clean_name}",
        definitions=(),
        steps=(),
    )
    document = _with_revision(document)
    validate_lineage_document(document)
    return document


def add_manual_job(
    document: LineageDocument,
    *,
    kind: str,
    name: str,
    qualified_name: str,
    language: str,
    source_reference: str,
    description: str,
) -> tuple[LineageDocument, LineageDefinition]:
    _require_manual(document)
    clean_kind = _text(kind, "job kind")
    if clean_kind not in _JOB_KINDS:
        raise LineageFailure("invalid_manual_job", f"Unsupported manual job kind: {clean_kind}")
    clean_name = _text(name, "job name")
    clean_qualified_name = _text(qualified_name, "qualified job name")
    clean_language = _text(language, "job language")
    clean_reference = _text(source_reference, "job source reference")
    clean_description = _text(description, "job description")
    if any(
        item.qualified_name.casefold() == clean_qualified_name.casefold()
        for item in document.definitions
    ):
        raise LineageFailure(
            "manual_job_exists",
            f"Manual job already exists: {clean_qualified_name}",
        )

    identity = f"{document.name}\n{clean_qualified_name.casefold()}"
    definition_id = stable_id("definition", "manual", identity)
    descriptor = json.dumps(
        {
            "description": clean_description,
            "kind": clean_kind,
            "language": clean_language,
            "name": clean_name,
            "qualified_name": clean_qualified_name,
            "source_reference": clean_reference,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    definition_revision = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()
    definition = LineageDefinition(
        id=definition_id,
        external_id=f"manual:{clean_qualified_name}",
        kind=clean_kind,
        name=clean_name,
        qualified_name=clean_qualified_name,
        language=clean_language,
        source_reference=clean_reference,
        content_hash=definition_revision,
        revision=definition_revision,
    )
    step = LineageStep(
        id=stable_id("step", "manual", identity),
        external_id=f"manual-step:{clean_qualified_name}",
        name=clean_name,
        definition_id=definition.id,
        depends_on=(),
    )
    analysis = LineageAnalysis(
        definition_id=definition.id,
        definition_revision=definition.revision,
        summary=clean_description,
        warnings=(),
    )
    updated = replace(
        document,
        definitions=tuple(sorted((*document.definitions, definition), key=lambda item: item.id)),
        steps=tuple(sorted((*document.steps, step), key=lambda item: item.id)),
        analyses=tuple(sorted((*document.analyses, analysis), key=lambda item: item.definition_id)),
    )
    updated = _with_revision(updated)
    validate_lineage_document(updated)
    return updated, definition


def add_manual_hop(
    document: LineageDocument,
    *,
    job_reference: str,
    source: str,
    target: str,
    operation: str,
    role: str,
    evidence_reference: str,
    reason: str,
    line_start: int = 1,
    line_end: int = 1,
) -> tuple[LineageDocument, LineageWriteUnit]:
    _require_manual(document)
    definition = _resolve_job(document, job_reference)
    clean_source = _text(source, "lineage source")
    clean_target = _text(target, "lineage target")
    clean_operation = _text(operation, "lineage operation")
    clean_role = _text(role, "lineage source role")
    clean_reference = _text(evidence_reference, "evidence reference")
    clean_reason = _text(reason, "evidence reason")
    if clean_operation not in _WRITE_OPERATIONS:
        raise LineageFailure(
            "invalid_manual_hop",
            f"Unsupported manual write operation: {clean_operation}",
        )
    if clean_role not in _SOURCE_ROLES:
        raise LineageFailure("invalid_manual_hop", f"Unsupported source role: {clean_role}")
    if (
        isinstance(line_start, bool)
        or isinstance(line_end, bool)
        or not isinstance(line_start, int)
        or not isinstance(line_end, int)
        or line_start < 1
        or line_end < line_start
    ):
        raise LineageFailure("invalid_manual_hop", "Manual evidence lines are invalid.")
    duplicate = next(
        (
            item
            for item in document.write_units
            if item.definition_id == definition.id
            and item.operation == clean_operation
            and item.target.casefold() == clean_target.casefold()
            and any(value.target.casefold() == clean_source.casefold() for value in item.sources)
        ),
        None,
    )
    if duplicate is not None:
        raise LineageFailure(
            "manual_hop_exists",
            f"Manual lineage hop already exists: {clean_source} -> {clean_target}",
        )

    evidence = LineageEvidence(
        source="human",
        reference=clean_reference,
        reason=clean_reason,
        line_start=line_start,
        line_end=line_end,
    )
    identity = (
        f"{definition.id}\n{clean_operation}\n{clean_source.casefold()}\n"
        f"{clean_target.casefold()}\n{clean_reference}\n{line_start}\n{line_end}"
    )
    unit = LineageWriteUnit(
        id=stable_id("write", "manual", identity),
        definition_id=definition.id,
        operation=clean_operation,
        target=clean_target,
        state="draft",
        evidence=evidence,
        sources=(
            LineageWriteSource(
                target=clean_source,
                role=clean_role,
                via=(),
                evidence=evidence,
            ),
        ),
    )
    updated = replace(
        document,
        write_units=tuple(sorted((*document.write_units, unit), key=lambda item: item.id)),
    )
    updated = _with_revision(updated)
    validate_lineage_document(updated)
    return updated, unit


def _resolve_job(document: LineageDocument, reference: str) -> LineageDefinition:
    normalized = _text(reference, "job reference").casefold()
    matches = [
        item
        for item in document.definitions
        if normalized in {item.id.casefold(), item.qualified_name.casefold()}
    ]
    if len(matches) != 1:
        code = "manual_job_not_found" if not matches else "ambiguous_manual_job"
        raise LineageFailure(code, f"Could not resolve one manual job: {reference}")
    return matches[0]


def _require_manual(document: LineageDocument) -> None:
    if document.source_kind != "manual":
        raise LineageFailure(
            "manual_overlay_required",
            "Manual jobs and hops must be stored in a manual lineage overlay.",
        )


def _with_revision(document: LineageDocument) -> LineageDocument:
    payload = {
        "analyses": [item.to_dict() for item in document.analyses],
        "claims": [item.to_dict() for item in document.claims],
        "definitions": [item.to_dict() for item in document.definitions],
        "steps": [item.to_dict() for item in document.steps],
        "write_units": [item.to_dict() for item in document.write_units],
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return replace(document, source_revision=hashlib.sha256(raw).hexdigest())


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LineageFailure("invalid_manual_lineage", f"Manual {label} must be text.")
    return value.strip()
