"""Experimental contracts for source-faithful semantic-model imports."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

SEMANTIC_IMPORT_CONTRACT_VERSION = "tarel.semantic_import.v0.1"

_IMPORT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_FORMAT_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_DIAGNOSTIC_LEVELS = frozenset({"error", "info", "warning"})


class SemanticFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    media_type: str
    sha256: str
    content: str

    @classmethod
    def from_content(cls, content: str, *, media_type: str) -> SourceSnapshot:
        return cls(
            media_type=media_type,
            sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            content=content,
        )

    def to_dict(self, *, include_content: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "media_type": self.media_type,
            "sha256": self.sha256,
        }
        if include_content:
            payload["content"] = self.content
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceSnapshot:
        return cls(
            media_type=_required_string(data, "media_type"),
            sha256=_required_string(data, "sha256"),
            content=_required_string(data, "content", strip=False),
        )


@dataclass(frozen=True, slots=True)
class SemanticExpression:
    dialect: str
    expression: str

    def to_dict(self) -> dict[str, object]:
        return {"dialect": self.dialect, "expression": self.expression}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticExpression:
        return cls(
            dialect=_required_string(data, "dialect"),
            expression=_required_string(data, "expression"),
        )


@dataclass(frozen=True, slots=True)
class SemanticField:
    id: str
    name: str
    source_reference: str
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    data_type: str | None = None
    is_time: bool | None = None
    expressions: tuple[SemanticExpression, ...] = ()
    graph_node_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "data_type": self.data_type,
            "description": self.description,
            "expressions": [item.to_dict() for item in self.expressions],
            "graph_node_id": self.graph_node_id,
            "id": self.id,
            "is_time": self.is_time,
            "name": self.name,
            "source_reference": self.source_reference,
            "synonyms": list(self.synonyms),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticField:
        return cls(
            id=_required_string(data, "id"),
            name=_required_string(data, "name"),
            source_reference=_required_string(data, "source_reference"),
            description=_optional_string(data.get("description"), "description"),
            synonyms=_strings(data.get("synonyms", []), "synonyms"),
            data_type=_optional_string(data.get("data_type"), "data_type"),
            is_time=_optional_bool(data.get("is_time"), "is_time"),
            expressions=tuple(
                SemanticExpression.from_dict(item)
                for item in _objects(data.get("expressions", []), "expressions")
            ),
            graph_node_id=_optional_string(data.get("graph_node_id"), "graph_node_id"),
        )


@dataclass(frozen=True, slots=True)
class SemanticDataset:
    id: str
    name: str
    source_reference: str
    source: str
    fields: tuple[SemanticField, ...]
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    primary_key: tuple[str, ...] = ()
    graph_node_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "description": self.description,
            "fields": [item.to_dict() for item in self.fields],
            "graph_node_id": self.graph_node_id,
            "id": self.id,
            "name": self.name,
            "primary_key": list(self.primary_key),
            "source": self.source,
            "source_reference": self.source_reference,
            "synonyms": list(self.synonyms),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticDataset:
        return cls(
            id=_required_string(data, "id"),
            name=_required_string(data, "name"),
            source_reference=_required_string(data, "source_reference"),
            source=_required_string(data, "source"),
            fields=tuple(
                SemanticField.from_dict(item)
                for item in _objects(data.get("fields", []), "fields")
            ),
            description=_optional_string(data.get("description"), "description"),
            synonyms=_strings(data.get("synonyms", []), "synonyms"),
            primary_key=_strings(data.get("primary_key", []), "primary_key"),
            graph_node_id=_optional_string(data.get("graph_node_id"), "graph_node_id"),
        )


@dataclass(frozen=True, slots=True)
class SemanticMetric:
    id: str
    name: str
    source_reference: str
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    data_type: str | None = None
    expressions: tuple[SemanticExpression, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "data_type": self.data_type,
            "description": self.description,
            "expressions": [item.to_dict() for item in self.expressions],
            "id": self.id,
            "name": self.name,
            "source_reference": self.source_reference,
            "synonyms": list(self.synonyms),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticMetric:
        return cls(
            id=_required_string(data, "id"),
            name=_required_string(data, "name"),
            source_reference=_required_string(data, "source_reference"),
            description=_optional_string(data.get("description"), "description"),
            synonyms=_strings(data.get("synonyms", []), "synonyms"),
            data_type=_optional_string(data.get("data_type"), "data_type"),
            expressions=tuple(
                SemanticExpression.from_dict(item)
                for item in _objects(data.get("expressions", []), "expressions")
            ),
        )


@dataclass(frozen=True, slots=True)
class SemanticRelationship:
    id: str
    name: str
    source_reference: str
    from_dataset: str
    to_dataset: str
    from_fields: tuple[str, ...]
    to_fields: tuple[str, ...]
    description: str | None = None
    synonyms: tuple[str, ...] = ()
    graph_edge_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "description": self.description,
            "from_dataset": self.from_dataset,
            "from_fields": list(self.from_fields),
            "graph_edge_id": self.graph_edge_id,
            "id": self.id,
            "name": self.name,
            "source_reference": self.source_reference,
            "synonyms": list(self.synonyms),
            "to_dataset": self.to_dataset,
            "to_fields": list(self.to_fields),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticRelationship:
        return cls(
            id=_required_string(data, "id"),
            name=_required_string(data, "name"),
            source_reference=_required_string(data, "source_reference"),
            from_dataset=_required_string(data, "from_dataset"),
            to_dataset=_required_string(data, "to_dataset"),
            from_fields=_strings(data.get("from_fields"), "from_fields"),
            to_fields=_strings(data.get("to_fields"), "to_fields"),
            description=_optional_string(data.get("description"), "description"),
            synonyms=_strings(data.get("synonyms", []), "synonyms"),
            graph_edge_id=_optional_string(data.get("graph_edge_id"), "graph_edge_id"),
        )


@dataclass(frozen=True, slots=True)
class SemanticModel:
    id: str
    name: str
    source_reference: str
    datasets: tuple[SemanticDataset, ...]
    relationships: tuple[SemanticRelationship, ...]
    metrics: tuple[SemanticMetric, ...]
    description: str | None = None
    synonyms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "datasets": [item.to_dict() for item in self.datasets],
            "description": self.description,
            "id": self.id,
            "metrics": [item.to_dict() for item in self.metrics],
            "name": self.name,
            "relationships": [item.to_dict() for item in self.relationships],
            "source_reference": self.source_reference,
            "synonyms": list(self.synonyms),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticModel:
        return cls(
            id=_required_string(data, "id"),
            name=_required_string(data, "name"),
            source_reference=_required_string(data, "source_reference"),
            datasets=tuple(
                SemanticDataset.from_dict(item)
                for item in _objects(data.get("datasets", []), "datasets")
            ),
            relationships=tuple(
                SemanticRelationship.from_dict(item)
                for item in _objects(data.get("relationships", []), "relationships")
            ),
            metrics=tuple(
                SemanticMetric.from_dict(item)
                for item in _objects(data.get("metrics", []), "metrics")
            ),
            description=_optional_string(data.get("description"), "description"),
            synonyms=_strings(data.get("synonyms", []), "synonyms"),
        )


@dataclass(frozen=True, slots=True)
class SemanticDiagnostic:
    level: str
    code: str
    message: str
    source_reference: str

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "level": self.level,
            "message": self.message,
            "source_reference": self.source_reference,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticDiagnostic:
        return cls(
            level=_required_string(data, "level"),
            code=_required_string(data, "code"),
            message=_required_string(data, "message"),
            source_reference=_required_string(data, "source_reference"),
        )


@dataclass(frozen=True, slots=True)
class SemanticSourceEdit:
    target_id: str
    description: str | None
    synonyms: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "description": self.description,
            "reason": self.reason,
            "synonyms": list(self.synonyms),
            "target_id": self.target_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticSourceEdit:
        return cls(
            target_id=_required_string(data, "target_id"),
            description=_optional_string(data.get("description"), "description"),
            synonyms=_strings(data.get("synonyms", []), "synonyms"),
            reason=_required_string(data, "reason"),
        )


@dataclass(frozen=True, slots=True)
class SemanticImportDocument:
    name: str
    graph_name: str
    format_name: str
    format_version: str
    snapshot: SourceSnapshot
    models: tuple[SemanticModel, ...]
    diagnostics: tuple[SemanticDiagnostic, ...] = ()
    edits: tuple[SemanticSourceEdit, ...] = ()
    contract_version: str = SEMANTIC_IMPORT_CONTRACT_VERSION

    @property
    def complete(self) -> bool:
        return not any(item.level == "error" for item in self.diagnostics)

    @property
    def revision(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self, *, include_source_content: bool = True) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "edits": [item.to_dict() for item in self.edits],
            "format_name": self.format_name,
            "format_version": self.format_version,
            "graph_name": self.graph_name,
            "models": [item.to_dict() for item in self.models],
            "name": self.name,
            "snapshot": self.snapshot.to_dict(include_content=include_source_content),
        }

    def summary_dict(self) -> dict[str, object]:
        return {
            "complete": self.complete,
            "diagnostics": len(self.diagnostics),
            "edits": len(self.edits),
            "format_name": self.format_name,
            "format_version": self.format_version,
            "graph_name": self.graph_name,
            "models": len(self.models),
            "name": self.name,
            "revision": self.revision,
            "source_sha256": self.snapshot.sha256,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SemanticImportDocument:
        if data.get("contract_version") != SEMANTIC_IMPORT_CONTRACT_VERSION:
            raise SemanticFailure(
                "unsupported_semantic_import",
                "Unsupported TAREL semantic-import contract.",
            )
        snapshot = data.get("snapshot")
        if not isinstance(snapshot, dict):
            raise SemanticFailure("invalid_semantic_import", "Snapshot must be an object.")
        document = cls(
            name=_required_string(data, "name"),
            graph_name=_required_string(data, "graph_name"),
            format_name=_required_string(data, "format_name"),
            format_version=_required_string(data, "format_version"),
            snapshot=SourceSnapshot.from_dict(snapshot),
            models=tuple(
                SemanticModel.from_dict(item)
                for item in _objects(data.get("models", []), "models")
            ),
            diagnostics=tuple(
                SemanticDiagnostic.from_dict(item)
                for item in _objects(data.get("diagnostics", []), "diagnostics")
            ),
            edits=tuple(
                SemanticSourceEdit.from_dict(item)
                for item in _objects(data.get("edits", []), "edits")
            ),
        )
        validate_semantic_import(document)
        return document


def validate_semantic_import(document: SemanticImportDocument) -> None:
    if document.contract_version != SEMANTIC_IMPORT_CONTRACT_VERSION:
        raise SemanticFailure(
            "unsupported_semantic_import",
            "Unsupported TAREL semantic-import contract.",
        )
    if not _IMPORT_NAME.fullmatch(document.name):
        raise SemanticFailure(
            "invalid_semantic_import_name",
            "Semantic import names may contain letters, numbers, dots, underscores, and hyphens.",
        )
    if not _IMPORT_NAME.fullmatch(document.graph_name):
        raise SemanticFailure("invalid_semantic_import", "Semantic import graph name is invalid.")
    if not _FORMAT_NAME.fullmatch(document.format_name):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic format name is invalid: {document.format_name}",
        )
    if not document.format_version.strip() or not document.snapshot.media_type.strip():
        raise SemanticFailure(
            "invalid_semantic_import",
            "Semantic format version and snapshot media type must not be empty.",
        )
    expected_hash = hashlib.sha256(document.snapshot.content.encode("utf-8")).hexdigest()
    if expected_hash != document.snapshot.sha256:
        raise SemanticFailure(
            "invalid_semantic_snapshot",
            "Semantic source snapshot does not match its SHA-256 digest.",
        )

    targets: dict[str, tuple[str | None, tuple[str, ...]]] = {}
    for model in document.models:
        _add_target(targets, model.id, model.description, model.synonyms)
        for dataset in model.datasets:
            _add_target(targets, dataset.id, dataset.description, dataset.synonyms)
            for field in dataset.fields:
                _add_target(targets, field.id, field.description, field.synonyms)
                _validate_expressions(field.expressions, field.source_reference)
        for relationship in model.relationships:
            _add_target(
                targets,
                relationship.id,
                relationship.description,
                relationship.synonyms,
            )
            if not relationship.from_fields or len(relationship.from_fields) != len(
                relationship.to_fields
            ):
                raise SemanticFailure(
                    "invalid_semantic_import",
                    f"Relationship fields do not align: {relationship.source_reference}",
                )
        for metric in model.metrics:
            _add_target(targets, metric.id, metric.description, metric.synonyms)
            _validate_expressions(metric.expressions, metric.source_reference)

    for diagnostic in document.diagnostics:
        if diagnostic.level not in _DIAGNOSTIC_LEVELS:
            raise SemanticFailure(
                "invalid_semantic_import",
                f"Unsupported semantic diagnostic level: {diagnostic.level}",
            )
    for edit in document.edits:
        if edit.target_id not in targets:
            raise SemanticFailure(
                "invalid_semantic_import",
                f"Semantic source edit references an unknown target: {edit.target_id}",
            )
        if not edit.reason.strip():
            raise SemanticFailure(
                "invalid_semantic_import",
                "Semantic source edits require a non-empty reason.",
            )
        _validate_strings(edit.synonyms, "edit synonyms")


def semantic_target_values(
    document: SemanticImportDocument,
    target_id: str,
) -> tuple[str | None, tuple[str, ...]]:
    targets = _target_values(document)
    try:
        description, synonyms = targets[target_id]
    except KeyError as exc:
        raise SemanticFailure(
            "semantic_target_not_found",
            f"Semantic source target not found: {target_id}",
        ) from exc
    for edit in document.edits:
        if edit.target_id == target_id:
            description, synonyms = edit.description, edit.synonyms
    return description, synonyms


def semantic_target_original_values(
    document: SemanticImportDocument,
    target_id: str,
) -> tuple[str | None, tuple[str, ...]]:
    try:
        return _target_values(document)[target_id]
    except KeyError as exc:
        raise SemanticFailure(
            "semantic_target_not_found",
            f"Semantic source target not found: {target_id}",
        ) from exc


def semantic_target_patch_count(document: SemanticImportDocument, target_id: str) -> int:
    return sum(item.target_id == target_id for item in document.edits)


def _target_values(
    document: SemanticImportDocument,
) -> dict[str, tuple[str | None, tuple[str, ...]]]:
    targets: dict[str, tuple[str | None, tuple[str, ...]]] = {}
    for model in document.models:
        targets[model.id] = (model.description, model.synonyms)
        for dataset in model.datasets:
            targets[dataset.id] = (dataset.description, dataset.synonyms)
            targets.update(
                (field.id, (field.description, field.synonyms)) for field in dataset.fields
            )
        targets.update(
            (item.id, (item.description, item.synonyms))
            for item in (*model.relationships, *model.metrics)
        )
    return targets


def _add_target(
    targets: dict[str, tuple[str | None, tuple[str, ...]]],
    target_id: str,
    description: str | None,
    synonyms: tuple[str, ...],
) -> None:
    if target_id in targets:
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic import contains a duplicate target ID: {target_id}",
        )
    _validate_strings(synonyms, "synonyms")
    targets[target_id] = (description, synonyms)


def _validate_expressions(
    expressions: tuple[SemanticExpression, ...],
    source_reference: str,
) -> None:
    dialects = [item.dialect.casefold() for item in expressions]
    if len(dialects) != len(set(dialects)):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic expressions repeat a dialect: {source_reference}",
        )


def _validate_strings(values: tuple[str, ...], label: str) -> None:
    if any(not item.strip() for item in values) or len(values) != len(set(values)):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic {label} must be unique non-empty strings.",
        )


def _required_string(
    data: dict[str, Any],
    key: str,
    *,
    strip: bool = True,
) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (strip and not value.strip()) or (not strip and not value):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic import field must be a string: {key}",
        )
    return value.strip() if strip else value


def _optional_string(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic import field must be null or a string: {key}",
        )
    return value.strip() or None


def _strings(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic import field must be a string array: {key}",
        )
    result = tuple(item.strip() for item in value)
    _validate_strings(result, key)
    return result


def _objects(value: Any, key: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic import field must be an object array: {key}",
        )
    return tuple(value)


def _optional_bool(value: Any, key: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SemanticFailure(
            "invalid_semantic_import",
            f"Semantic import field must be null or boolean: {key}",
        )
    return value
