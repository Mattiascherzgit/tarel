"""Strict contracts for sanitized, caller-observed runtime query lineage."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tarel.lineage.contracts import LineageFailure

_INPUT_VERSION = "tarel.runtime-lineage-input.v0.1"
_DOCUMENT_VERSION = "tarel.runtime-lineage.v0.1"
_DIALECTS = frozenset({"postgresql", "sqlite", "sqlserver"})
_SQL_STATUSES = frozenset({"failed", "succeeded"})
_MONGO_OPERATIONS = frozenset({"aggregate", "find"})
_MONGO_STATUSES = frozenset({"failed", "succeeded"})
_FEDERATED_STATUSES = frozenset({"accepted", "failed", "succeeded"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_EVENTS = 1_000
_MAX_INPUTS = 512
_MAX_COLUMNS = 256
_MAX_TEXT_LENGTH = 256


@dataclass(frozen=True, slots=True)
class RuntimeResultEvidence:
    columns: tuple[str, ...]
    row_count: int
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "columns": list(self.columns),
            "row_count": self.row_count,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeResultEvidence:
        _fields(data, {"columns", "row_count", "sha256"}, "result")
        columns = _strings(data.get("columns"), "result columns", limit=_MAX_COLUMNS)
        if not columns or len(columns) != len(set(item.casefold() for item in columns)):
            raise LineageFailure(
                "invalid_runtime_lineage",
                "Runtime result columns must be non-empty and unique.",
            )
        row_count = _integer(data.get("row_count"), "result row_count", minimum=0)
        return cls(
            columns=columns,
            row_count=row_count,
            sha256=_sha256(data.get("sha256"), "result sha256"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeSQLAttemptInput:
    sequence: int
    call_id: str
    status: str
    source: str
    dialect: str
    statement_sha256: str
    inputs: tuple[str, ...]
    result: RuntimeResultEvidence | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "dialect": self.dialect,
            "error_code": self.error_code,
            "inputs": list(self.inputs),
            "kind": "sql_query",
            "operation": "select",
            "result": self.result.to_dict() if self.result else None,
            "sequence": self.sequence,
            "source": self.source,
            "statement_sha256": self.statement_sha256,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeSQLAttemptInput:
        _fields(
            data,
            {
                "call_id",
                "dialect",
                "error_code",
                "inputs",
                "kind",
                "operation",
                "result",
                "sequence",
                "source",
                "statement_sha256",
                "status",
            },
            "SQL event",
        )
        if data.get("kind") != "sql_query":
            raise LineageFailure(
                "invalid_runtime_lineage",
                "This runtime-lineage version supports only sql_query events.",
            )
        if data.get("operation") != "select":
            raise LineageFailure(
                "invalid_runtime_lineage",
                "This runtime-lineage version accepts only declared read-only SELECT attempts.",
            )
        status = _choice(data.get("status"), "SQL event status", _SQL_STATUSES)
        result_value = data.get("result")
        error_value = data.get("error_code")
        if status == "succeeded":
            if not isinstance(result_value, dict) or error_value is not None:
                raise LineageFailure(
                    "invalid_runtime_lineage",
                    "A succeeded SQL event requires result evidence and no error code.",
                )
            result = RuntimeResultEvidence.from_dict(result_value)
            error_code = None
        else:
            if result_value is not None or error_value is None:
                raise LineageFailure(
                    "invalid_runtime_lineage",
                    "A failed SQL event requires an error code and no result evidence.",
                )
            result = None
            error_code = _safe_code(error_value, "SQL event error_code")
        inputs = _strings(data.get("inputs"), "SQL event inputs", limit=_MAX_INPUTS)
        if not inputs or len(inputs) != len(set(inputs)):
            raise LineageFailure(
                "invalid_runtime_lineage",
                "SQL event inputs must contain unique graph node IDs.",
            )
        return cls(
            sequence=_integer(data.get("sequence"), "SQL event sequence", minimum=1),
            call_id=_text(data.get("call_id"), "SQL event call_id"),
            status=status,
            source=_safe_code(data.get("source"), "SQL event source"),
            dialect=_choice(data.get("dialect"), "SQL event dialect", _DIALECTS),
            statement_sha256=_sha256(data.get("statement_sha256"), "statement_sha256"),
            inputs=inputs,
            result=result,
            error_code=error_code,
        )


@dataclass(frozen=True, slots=True)
class RuntimeMongoAttemptInput:
    sequence: int
    call_id: str
    status: str
    source: str
    operation: str
    request_sha256: str
    inputs: tuple[str, ...]
    result: RuntimeResultEvidence | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "error_code": self.error_code,
            "inputs": list(self.inputs),
            "kind": "mongo_query",
            "operation": self.operation,
            "request_sha256": self.request_sha256,
            "result": self.result.to_dict() if self.result else None,
            "sequence": self.sequence,
            "source": self.source,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeMongoAttemptInput:
        _fields(
            data,
            {
                "call_id",
                "error_code",
                "inputs",
                "kind",
                "operation",
                "request_sha256",
                "result",
                "sequence",
                "source",
                "status",
            },
            "MongoDB event",
        )
        if data.get("kind") != "mongo_query":
            raise LineageFailure(
                "invalid_runtime_lineage",
                "This runtime-lineage version supports only mongo_query events here.",
            )
        operation = _choice(
            data.get("operation"),
            "MongoDB event operation",
            _MONGO_OPERATIONS,
        )
        status = _choice(data.get("status"), "MongoDB event status", _MONGO_STATUSES)
        result_value = data.get("result")
        error_value = data.get("error_code")
        if status == "succeeded":
            if not isinstance(result_value, dict) or error_value is not None:
                raise LineageFailure(
                    "invalid_runtime_lineage",
                    "A succeeded MongoDB event requires result evidence and no error code.",
                )
            result = RuntimeResultEvidence.from_dict(result_value)
            error_code = None
        else:
            if result_value is not None or error_value is None:
                raise LineageFailure(
                    "invalid_runtime_lineage",
                    "A failed MongoDB event requires an error code and no result evidence.",
                )
            result = None
            error_code = _safe_code(error_value, "MongoDB event error_code")
        inputs = _strings(data.get("inputs"), "MongoDB event inputs", limit=_MAX_INPUTS)
        if not inputs or len(inputs) != len(set(inputs)):
            raise LineageFailure(
                "invalid_runtime_lineage",
                "MongoDB event inputs must contain unique graph node IDs.",
            )
        return cls(
            sequence=_integer(data.get("sequence"), "MongoDB event sequence", minimum=1),
            call_id=_text(data.get("call_id"), "MongoDB event call_id"),
            status=status,
            source=_safe_code(data.get("source"), "MongoDB event source"),
            operation=operation,
            request_sha256=_sha256(data.get("request_sha256"), "request_sha256"),
            inputs=inputs,
            result=result,
            error_code=error_code,
        )


@dataclass(frozen=True, slots=True)
class RuntimeFederatedQueryInput:
    sequence: int
    call_id: str
    status: str
    statement_sha256: str
    consumes: tuple[str, ...]
    result: RuntimeResultEvidence | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "consumes": list(self.consumes),
            "engine": "duckdb",
            "error_code": self.error_code,
            "kind": "federated_query",
            "operation": "select",
            "result": self.result.to_dict() if self.result else None,
            "sequence": self.sequence,
            "statement_sha256": self.statement_sha256,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeFederatedQueryInput:
        _fields(
            data,
            {
                "call_id",
                "consumes",
                "engine",
                "error_code",
                "kind",
                "operation",
                "result",
                "sequence",
                "statement_sha256",
                "status",
            },
            "federated event",
        )
        if data.get("kind") != "federated_query" or data.get("engine") != "duckdb":
            raise LineageFailure(
                "invalid_runtime_lineage",
                "This runtime-lineage version supports only DuckDB federated_query events.",
            )
        if data.get("operation") != "select":
            raise LineageFailure(
                "invalid_runtime_lineage",
                "Federated runtime lineage accepts only declared read-only SELECT attempts.",
            )
        status = _choice(
            data.get("status"),
            "federated event status",
            _FEDERATED_STATUSES,
        )
        result_value = data.get("result")
        error_value = data.get("error_code")
        if status in {"accepted", "succeeded"}:
            if not isinstance(result_value, dict) or error_value is not None:
                raise LineageFailure(
                    "invalid_runtime_lineage",
                    "A successful federated event requires result evidence and no error code.",
                )
            result = RuntimeResultEvidence.from_dict(result_value)
            error_code = None
        else:
            if result_value is not None or error_value is None:
                raise LineageFailure(
                    "invalid_runtime_lineage",
                    "A failed federated event requires an error code and no result evidence.",
                )
            result = None
            error_code = _safe_code(error_value, "federated event error_code")
        consumes = _strings(
            data.get("consumes"),
            "federated event consumes",
            limit=_MAX_INPUTS,
        )
        if not consumes or len(consumes) != len(set(consumes)):
            raise LineageFailure(
                "invalid_runtime_lineage",
                "Federated events must consume unique prior call IDs.",
            )
        return cls(
            sequence=_integer(data.get("sequence"), "federated event sequence", minimum=1),
            call_id=_text(data.get("call_id"), "federated event call_id"),
            status=status,
            statement_sha256=_sha256(data.get("statement_sha256"), "statement_sha256"),
            consumes=consumes,
            result=result,
            error_code=error_code,
        )


RuntimeEventInput = (
    RuntimeSQLAttemptInput | RuntimeMongoAttemptInput | RuntimeFederatedQueryInput
)


@dataclass(frozen=True, slots=True)
class RuntimeLineageInput:
    run_id: str
    graph_name: str
    graph_revision: str
    events: tuple[RuntimeEventInput, ...]
    contract_version: str = _INPUT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "events": [item.to_dict() for item in self.events],
            "graph": {"name": self.graph_name, "revision": self.graph_revision},
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeLineageInput:
        _fields(data, {"contract_version", "events", "graph", "run_id"}, "runtime input")
        if data.get("contract_version") != _INPUT_VERSION:
            raise LineageFailure(
                "unsupported_runtime_lineage",
                "Unsupported TAREL runtime-lineage input contract.",
            )
        graph = data.get("graph")
        if not isinstance(graph, dict):
            raise LineageFailure("invalid_runtime_lineage", "Runtime graph must be an object.")
        _fields(graph, {"name", "revision"}, "runtime graph")
        events_value = data.get("events")
        if not isinstance(events_value, list) or not 1 <= len(events_value) <= _MAX_EVENTS:
            raise LineageFailure(
                "invalid_runtime_lineage",
                f"Runtime input must contain between 1 and {_MAX_EVENTS} events.",
            )
        events = tuple(
            _runtime_input_event(_object(item, "runtime event")) for item in events_value
        )
        _validate_event_identity(events)
        return cls(
            run_id=_text(data.get("run_id"), "run_id"),
            graph_name=_text(graph.get("name"), "graph name"),
            graph_revision=_sha256(graph.get("revision"), "graph revision"),
            events=events,
        )


@dataclass(frozen=True, slots=True)
class RuntimeInputReference:
    node_id: str
    reference: str
    kind: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "node_id": self.node_id, "reference": self.reference}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeInputReference:
        _fields(data, {"kind", "node_id", "reference"}, "runtime input reference")
        kind = _choice(
            data.get("kind"),
            "runtime input kind",
            frozenset({"field", "table", "view"}),
        )
        return cls(
            node_id=_text(data.get("node_id"), "runtime input node_id"),
            reference=_text(data.get("reference"), "runtime input reference"),
            kind=kind,
        )


@dataclass(frozen=True, slots=True)
class RuntimeSQLAttempt:
    sequence: int
    call_id: str
    status: str
    source: str
    dialect: str
    statement_sha256: str
    inputs: tuple[RuntimeInputReference, ...]
    result: RuntimeResultEvidence | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "dialect": self.dialect,
            "error_code": self.error_code,
            "inputs": [item.to_dict() for item in self.inputs],
            "kind": "sql_query",
            "operation": "select",
            "result": self.result.to_dict() if self.result else None,
            "sequence": self.sequence,
            "source": self.source,
            "statement_sha256": self.statement_sha256,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeSQLAttempt:
        inputs = _objects(data.get("inputs"), "inputs")
        input_event = RuntimeSQLAttemptInput.from_dict(
            {**data, "inputs": [item.get("node_id") for item in inputs]}
        )
        references = tuple(
            RuntimeInputReference.from_dict(item) for item in inputs
        )
        return cls(
            sequence=input_event.sequence,
            call_id=input_event.call_id,
            status=input_event.status,
            source=input_event.source,
            dialect=input_event.dialect,
            statement_sha256=input_event.statement_sha256,
            inputs=references,
            result=input_event.result,
            error_code=input_event.error_code,
        )


@dataclass(frozen=True, slots=True)
class RuntimeMongoAttempt:
    sequence: int
    call_id: str
    status: str
    source: str
    operation: str
    request_sha256: str
    inputs: tuple[RuntimeInputReference, ...]
    result: RuntimeResultEvidence | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "error_code": self.error_code,
            "inputs": [item.to_dict() for item in self.inputs],
            "kind": "mongo_query",
            "operation": self.operation,
            "request_sha256": self.request_sha256,
            "result": self.result.to_dict() if self.result else None,
            "sequence": self.sequence,
            "source": self.source,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeMongoAttempt:
        inputs = _objects(data.get("inputs"), "inputs")
        input_event = RuntimeMongoAttemptInput.from_dict(
            {**data, "inputs": [item.get("node_id") for item in inputs]}
        )
        references = tuple(RuntimeInputReference.from_dict(item) for item in inputs)
        return cls(
            sequence=input_event.sequence,
            call_id=input_event.call_id,
            status=input_event.status,
            source=input_event.source,
            operation=input_event.operation,
            request_sha256=input_event.request_sha256,
            inputs=references,
            result=input_event.result,
            error_code=input_event.error_code,
        )


@dataclass(frozen=True, slots=True)
class RuntimeFederatedQuery:
    sequence: int
    call_id: str
    status: str
    statement_sha256: str
    consumes: tuple[str, ...]
    result: RuntimeResultEvidence | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        return RuntimeFederatedQueryInput(
            sequence=self.sequence,
            call_id=self.call_id,
            status=self.status,
            statement_sha256=self.statement_sha256,
            consumes=self.consumes,
            result=self.result,
            error_code=self.error_code,
        ).to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeFederatedQuery:
        event = RuntimeFederatedQueryInput.from_dict(data)
        return cls(
            sequence=event.sequence,
            call_id=event.call_id,
            status=event.status,
            statement_sha256=event.statement_sha256,
            consumes=event.consumes,
            result=event.result,
            error_code=event.error_code,
        )


RuntimeEvent = RuntimeSQLAttempt | RuntimeMongoAttempt | RuntimeFederatedQuery


@dataclass(frozen=True, slots=True)
class RuntimeLineageDocument:
    name: str
    run_id: str
    graph_name: str
    graph_revision: str
    events: tuple[RuntimeEvent, ...]
    contract_version: str = _DOCUMENT_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "events": [item.to_dict() for item in self.events],
            "graph": {"name": self.graph_name, "revision": self.graph_revision},
            "name": self.name,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeLineageDocument:
        _fields(
            data,
            {"contract_version", "events", "graph", "name", "run_id"},
            "runtime document",
        )
        if data.get("contract_version") != _DOCUMENT_VERSION:
            raise LineageFailure(
                "unsupported_runtime_lineage",
                "Unsupported TAREL runtime-lineage document contract.",
            )
        graph = _object(data.get("graph"), "runtime graph")
        _fields(graph, {"name", "revision"}, "runtime graph")
        events_value = data.get("events")
        if not isinstance(events_value, list) or not 1 <= len(events_value) <= _MAX_EVENTS:
            raise LineageFailure(
                "invalid_runtime_lineage",
                f"Runtime document must contain between 1 and {_MAX_EVENTS} events.",
            )
        events = tuple(
            _runtime_document_event(_object(item, "runtime event"))
            for item in events_value
        )
        _validate_event_identity(events)
        return cls(
            name=_text(data.get("name"), "runtime lineage name"),
            run_id=_text(data.get("run_id"), "run_id"),
            graph_name=_text(graph.get("name"), "graph name"),
            graph_revision=_sha256(graph.get("revision"), "graph revision"),
            events=events,
        )


@dataclass(frozen=True, slots=True)
class RuntimeTraceCall:
    call_id: str
    sequence: int
    kind: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "call_id": self.call_id,
            "kind": self.kind,
            "sequence": self.sequence,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class RuntimeDependency:
    source_call_id: str
    target_call_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_call_id": self.source_call_id,
            "target_call_id": self.target_call_id,
        }


@dataclass(frozen=True, slots=True)
class RuntimeLineageTrace:
    runtime_lineage: str
    start_call_id: str
    graph_name: str
    graph_revision: str
    calls: tuple[RuntimeTraceCall, ...]
    dependencies: tuple[RuntimeDependency, ...]
    origins: tuple[RuntimeInputReference, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "calls": [item.to_dict() for item in self.calls],
            "dependencies": [item.to_dict() for item in self.dependencies],
            "graph": {"name": self.graph_name, "revision": self.graph_revision},
            "origins": [item.to_dict() for item in self.origins],
            "runtime_lineage": self.runtime_lineage,
            "start_call_id": self.start_call_id,
        }


def load_runtime_lineage_input(path: Path) -> RuntimeLineageInput:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except FileNotFoundError as exc:
        raise LineageFailure(
            "runtime_lineage_input_not_found",
            "Runtime input file not found.",
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LineageFailure(
            "invalid_runtime_lineage",
            "Could not read runtime input JSON.",
        ) from exc
    return RuntimeLineageInput.from_dict(_object(payload, "runtime input"))


def validate_runtime_lineage_input(value: RuntimeLineageInput) -> None:
    RuntimeLineageInput.from_dict(value.to_dict())


def validate_runtime_lineage_document(value: RuntimeLineageDocument) -> None:
    RuntimeLineageDocument.from_dict(value.to_dict())


def _validate_event_identity(
    events: tuple[RuntimeEventInput, ...] | tuple[RuntimeEvent, ...],
) -> None:
    sequences = tuple(item.sequence for item in events)
    if sequences != tuple(range(1, len(events) + 1)):
        raise LineageFailure(
            "invalid_runtime_lineage",
            "Runtime event sequences must be contiguous and start at 1.",
        )
    call_ids = tuple(item.call_id for item in events)
    if len(call_ids) != len(set(call_ids)):
        raise LineageFailure("invalid_runtime_lineage", "Runtime call IDs must be unique.")
    prior: dict[str, RuntimeEventInput | RuntimeEvent] = {}
    for event in events:
        if isinstance(event, (RuntimeFederatedQueryInput, RuntimeFederatedQuery)):
            for call_id in event.consumes:
                dependency = prior.get(call_id)
                if dependency is None:
                    raise LineageFailure(
                        "invalid_runtime_lineage",
                        "Federated events may consume only earlier runtime calls.",
                    )
                if dependency.status not in {"accepted", "succeeded"}:
                    raise LineageFailure(
                        "invalid_runtime_lineage",
                        "Federated events cannot consume failed runtime calls.",
                    )
        prior[event.call_id] = event


def _runtime_input_event(data: dict[str, Any]) -> RuntimeEventInput:
    kind = data.get("kind")
    if kind == "sql_query":
        return RuntimeSQLAttemptInput.from_dict(data)
    if kind == "mongo_query":
        return RuntimeMongoAttemptInput.from_dict(data)
    if kind == "federated_query":
        return RuntimeFederatedQueryInput.from_dict(data)
    raise LineageFailure("invalid_runtime_lineage", "Unsupported runtime event kind.")


def _runtime_document_event(data: dict[str, Any]) -> RuntimeEvent:
    kind = data.get("kind")
    if kind == "sql_query":
        return RuntimeSQLAttempt.from_dict(data)
    if kind == "mongo_query":
        return RuntimeMongoAttempt.from_dict(data)
    if kind == "federated_query":
        return RuntimeFederatedQuery.from_dict(data)
    raise LineageFailure("invalid_runtime_lineage", "Unsupported runtime event kind.")


def _unique_object(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise LineageFailure("invalid_runtime_lineage", "Runtime JSON has duplicate keys.")
        result[key] = value
    return result


def _fields(data: dict[str, Any], expected: set[str], label: str) -> None:
    unknown = set(data) - expected
    missing = expected - set(data)
    if unknown or missing:
        raise LineageFailure(
            "invalid_runtime_lineage",
            f"{label} has unexpected or missing fields.",
        )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LineageFailure("invalid_runtime_lineage", f"{label} must be an object.")
    return value


def _objects(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise LineageFailure("invalid_runtime_lineage", f"{label} must be an array.")
    return [_object(item, label) for item in value]


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_TEXT_LENGTH:
        raise LineageFailure(
            "invalid_runtime_lineage",
            f"{label} must be a non-empty string of at most {_MAX_TEXT_LENGTH} characters.",
        )
    return value.strip()


def _strings(value: object, label: str, *, limit: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > limit:
        raise LineageFailure(
            "invalid_runtime_lineage",
            f"{label} must be an array containing at most {limit} strings.",
        )
    return tuple(_text(item, label) for item in value)


def _integer(value: object, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LineageFailure(
            "invalid_runtime_lineage",
            f"{label} must be an integer greater than or equal to {minimum}.",
        )
    return value


def _choice(value: object, label: str, choices: frozenset[str]) -> str:
    selected = _text(value, label)
    if selected not in choices:
        raise LineageFailure(
            "invalid_runtime_lineage",
            f"{label} must be one of: {', '.join(sorted(choices))}.",
        )
    return selected


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise LineageFailure(
            "invalid_runtime_lineage",
            f"{label} must be a lowercase SHA-256 value.",
        )
    return value


def _safe_code(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_CODE.fullmatch(value):
        raise LineageFailure(
            "invalid_runtime_lineage",
            f"{label} must be a safe identifier, not a URL or free-form message.",
        )
    return value
