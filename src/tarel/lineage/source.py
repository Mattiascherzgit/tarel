"""Strict input-only contract for workflow definitions and source code."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tarel.lineage.contracts import LineageFailure

_INPUT_VERSION = "tarel.lineage-input.v0.2"
_READABLE_INPUT_VERSIONS = frozenset({_INPUT_VERSION, "tarel.lineage-input.v0.1"})
_DEFINITION_FIELDS = {
    "content",
    "external_id",
    "kind",
    "language",
    "name",
    "qualified_name",
    "source_reference",
}
_STEP_FIELDS = {"definition_id", "depends_on", "external_id", "name"}
_MATERIALIZATION_FIELDS = {"definition_id", "mode", "source_reference", "target"}
_OBSERVATION_FIELDS = {
    "definition_id",
    "line_end",
    "line_start",
    "operation",
    "reason",
    "source_reference",
    "target",
}
_MATERIALIZATION_MODES = frozenset({"incremental", "table", "view"})


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    external_id: str
    kind: str
    name: str
    qualified_name: str
    language: str
    content: str
    source_reference: str

    @property
    def id(self) -> str:
        return stable_id("definition", self.kind, self.external_id)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()

    @property
    def revision(self) -> str:
        return _hash(
            {
                "content_hash": self.content_hash,
                "external_id": self.external_id,
                "kind": self.kind,
                "language": self.language,
                "name": self.name,
                "qualified_name": self.qualified_name,
                "source_reference": self.source_reference,
            }
        )


@dataclass(frozen=True, slots=True)
class SourceStep:
    external_id: str
    name: str
    definition_external_id: str
    depends_on_external_ids: tuple[str, ...]

    @property
    def id(self) -> str:
        return stable_id("step", "workflow", self.external_id)


@dataclass(frozen=True, slots=True)
class SourceMaterialization:
    definition_external_id: str
    target: str
    mode: str
    source_reference: str


@dataclass(frozen=True, slots=True)
class SourceObservation:
    definition_external_id: str
    operation: str
    target: str
    source_reference: str
    reason: str
    line_start: int
    line_end: int


@dataclass(frozen=True, slots=True)
class LineageInput:
    source_kind: str
    source_name: str
    source_reference: str
    workflow_external_id: str
    workflow_name: str
    definitions: tuple[SourceDefinition, ...]
    steps: tuple[SourceStep, ...]
    materializations: tuple[SourceMaterialization, ...] = ()
    observations: tuple[SourceObservation, ...] = ()

    @property
    def workflow_id(self) -> str:
        return stable_id("workflow", "workflow", self.workflow_external_id)

    @property
    def revision(self) -> str:
        payload = {
            "definitions": [
                {**asdict(item), "content": item.content_hash} for item in self.definitions
            ],
            "source_kind": self.source_kind,
            "source_name": self.source_name,
            "source_reference": self.source_reference,
            "materializations": [asdict(item) for item in self.materializations],
            "observations": [asdict(item) for item in self.observations],
            "steps": [asdict(item) for item in self.steps],
            "workflow_external_id": self.workflow_external_id,
            "workflow_name": self.workflow_name,
        }
        return _hash(payload)

    def definition_by_id(self) -> dict[str, SourceDefinition]:
        return {item.id: item for item in self.definitions}

    def definition_by_external_id(self) -> dict[str, SourceDefinition]:
        return {item.external_id: item for item in self.definitions}


def load_lineage_input(path: Path) -> LineageInput:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise LineageFailure("lineage_input_not_found", f"Lineage input not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Could not read lineage input: {path}",
        ) from exc
    if not isinstance(payload, dict):
        raise LineageFailure("invalid_lineage_input", "Lineage input root must be an object.")
    input_version = payload.get("format_version")
    if input_version not in _READABLE_INPUT_VERSIONS:
        raise LineageFailure(
            "unsupported_lineage_input",
            "Unsupported TAREL lineage input contract.",
        )
    root_fields = {"definitions", "format_version", "source", "workflow"}
    if input_version == _INPUT_VERSION:
        root_fields.update({"materializations", "observations"})
    _fields(payload, root_fields, "root")
    source = _mapping(payload.get("source"), "source")
    workflow = _mapping(payload.get("workflow"), "workflow")
    _fields(source, {"kind", "name", "reference"}, "source")
    _fields(workflow, {"external_id", "name", "steps"}, "workflow")

    definitions = tuple(
        _definition(item) for item in _mappings(payload.get("definitions"), "definitions")
    )
    if not definitions:
        raise LineageFailure("invalid_lineage_input", "Lineage input requires definitions.")
    external_ids = [item.external_id for item in definitions]
    if len(external_ids) != len(set(external_ids)):
        raise LineageFailure("invalid_lineage_input", "Definitions require unique external IDs.")

    materializations = (
        tuple(
            _materialization(item, set(external_ids))
            for item in _mappings(payload.get("materializations"), "materializations")
        )
        if input_version == _INPUT_VERSION
        else ()
    )
    materialized_definitions = [item.definition_external_id for item in materializations]
    if len(materialized_definitions) != len(set(materialized_definitions)):
        raise LineageFailure(
            "invalid_lineage_input",
            "Each definition may declare at most one materialization target.",
        )

    observations = (
        tuple(
            _observation(item, set(external_ids))
            for item in _mappings(payload.get("observations"), "observations")
        )
        if input_version == _INPUT_VERSION
        else ()
    )

    steps = tuple(
        _step(item, set(external_ids))
        for item in _mappings(workflow.get("steps"), "workflow steps")
    )
    if not steps:
        raise LineageFailure("invalid_lineage_input", "Lineage workflow requires steps.")
    step_ids = [item.external_id for item in steps]
    if len(step_ids) != len(set(step_ids)):
        raise LineageFailure("invalid_lineage_input", "Workflow steps require unique external IDs.")
    _validate_dependencies(steps)

    return LineageInput(
        source_kind=_text(source, "kind"),
        source_name=_text(source, "name"),
        source_reference=_text(source, "reference"),
        workflow_external_id=_text(workflow, "external_id"),
        workflow_name=_text(workflow, "name"),
        definitions=definitions,
        steps=steps,
        materializations=materializations,
        observations=observations,
    )


def stable_id(namespace: str, kind: str, value: str) -> str:
    digest = hashlib.sha256(f"{kind}\n{value}".encode()).hexdigest()[:24]
    return f"lineage:{namespace}:{digest}"


def _definition(data: dict[str, Any]) -> SourceDefinition:
    _fields(data, _DEFINITION_FIELDS, "definition")
    kind = _text(data, "kind")
    if kind not in {"procedure", "query", "script"}:
        raise LineageFailure("invalid_lineage_input", f"Unsupported definition kind: {kind}")
    return SourceDefinition(
        external_id=_text(data, "external_id"),
        kind=kind,
        name=_text(data, "name"),
        qualified_name=_text(data, "qualified_name"),
        language=_text(data, "language"),
        content=_text(data, "content", strip=False),
        source_reference=_text(data, "source_reference"),
    )


def _step(data: dict[str, Any], definitions: set[str]) -> SourceStep:
    _fields(data, _STEP_FIELDS, "workflow step")
    definition_id = _text(data, "definition_id")
    if definition_id not in definitions:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Workflow step references an unknown definition: {definition_id}",
        )
    depends_on = _strings(data.get("depends_on"), "step depends_on")
    external_id = _text(data, "external_id")
    if external_id in depends_on:
        raise LineageFailure("invalid_lineage_input", "Workflow step cannot depend on itself.")
    return SourceStep(
        external_id=external_id,
        name=_text(data, "name"),
        definition_external_id=definition_id,
        depends_on_external_ids=depends_on,
    )


def _materialization(
    data: dict[str, Any],
    definitions: set[str],
) -> SourceMaterialization:
    _fields(data, _MATERIALIZATION_FIELDS, "materialization")
    definition_id = _text(data, "definition_id")
    if definition_id not in definitions:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Materialization references an unknown definition: {definition_id}",
        )
    mode = _text(data, "mode")
    if mode not in _MATERIALIZATION_MODES:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Unsupported materialization mode: {mode}",
        )
    return SourceMaterialization(
        definition_external_id=definition_id,
        target=_text(data, "target"),
        mode=mode,
        source_reference=_text(data, "source_reference"),
    )


def _observation(
    data: dict[str, Any],
    definitions: set[str],
) -> SourceObservation:
    _fields(data, _OBSERVATION_FIELDS, "observation")
    definition_id = _text(data, "definition_id")
    if definition_id not in definitions:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Observation references an unknown definition: {definition_id}",
        )
    operation = _text(data, "operation")
    if operation not in {"call", "read"}:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Unsupported observation operation: {operation}",
        )
    line_start = _positive_integer(data.get("line_start"), "observation line_start")
    line_end = _positive_integer(data.get("line_end"), "observation line_end")
    if line_end < line_start:
        raise LineageFailure("invalid_lineage_input", "Observation line range is invalid.")
    return SourceObservation(
        definition_external_id=definition_id,
        operation=operation,
        target=_text(data, "target"),
        source_reference=_text(data, "source_reference"),
        reason=_text(data, "reason"),
        line_start=line_start,
        line_end=line_end,
    )


def _validate_dependencies(steps: tuple[SourceStep, ...]) -> None:
    known = {item.external_id for item in steps}
    for item in steps:
        unknown = set(item.depends_on_external_ids) - known
        if unknown:
            raise LineageFailure(
                "invalid_lineage_input",
                f"Workflow step has unknown dependencies: {', '.join(sorted(unknown))}",
            )
    remaining = {item.external_id: set(item.depends_on_external_ids) for item in steps}
    while remaining:
        ready = {key for key, values in remaining.items() if not values}
        if not ready:
            raise LineageFailure("invalid_lineage_input", "Workflow dependencies contain a cycle.")
        remaining = {key: values - ready for key, values in remaining.items() if key not in ready}


def _hash(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LineageFailure("invalid_lineage_input", f"Lineage {label} must be an object.")
    return value


def _mappings(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise LineageFailure(
            "invalid_lineage_input",
            f"Lineage {label} must contain objects.",
        )
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise LineageFailure(
            "invalid_lineage_input",
            f"Lineage {label} must be an array of non-empty strings.",
        )
    if len(value) != len(set(value)):
        raise LineageFailure("invalid_lineage_input", f"Lineage {label} contains duplicates.")
    return tuple(value)


def _text(data: dict[str, Any], key: str, *, strip: bool = True) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LineageFailure("invalid_lineage_input", f"Lineage field must be a string: {key}")
    return value.strip() if strip else value


def _positive_integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Lineage {label} must be a positive integer.",
        )
    return value


def _fields(data: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(data))
    unexpected = sorted(set(data) - expected)
    if missing:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Missing {label} fields: {', '.join(missing)}",
        )
    if unexpected:
        raise LineageFailure(
            "invalid_lineage_input",
            f"Unsupported {label} fields: {', '.join(unexpected)}",
        )
