"""Strict loading and validation for caller-observed catalogs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogResult,
    ConnectorFailure,
)

_CATALOG_FIELDS = {
    "catalog",
    "connector",
    "dialect",
    "objects",
    "relationships",
    "source_type",
    "status",
}
_OBJECT_FIELDS = {"description", "fields", "kind", "name", "namespace", "primary_key"}
_FIELD_FIELDS = {
    "data_type",
    "description",
    "is_primary_key",
    "name",
    "nullable",
    "position",
}
_RELATIONSHIP_FIELDS = {
    "from_fields",
    "from_namespace",
    "from_object",
    "kind",
    "name",
    "to_fields",
    "to_namespace",
    "to_object",
}


def load_catalog_result(path: Path) -> CatalogResult:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConnectorFailure("catalog_not_found", f"Catalog input not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConnectorFailure("invalid_catalog", f"Could not read catalog input: {path}") from exc
    if not isinstance(payload, dict):
        raise ConnectorFailure("invalid_catalog", "Catalog input root must be an object.")
    return catalog_result_from_dict(payload)


def catalog_result_from_dict(data: dict[str, Any]) -> CatalogResult:
    _require_fields(data, _CATALOG_FIELDS, "catalog")
    if data.get("status") != "ok":
        raise ConnectorFailure("invalid_catalog", "Catalog input status must be 'ok'.")
    objects = tuple(_catalog_object(item) for item in _object_list(data.get("objects"), "objects"))
    relationships = tuple(
        _catalog_relationship(item)
        for item in _object_list(data.get("relationships"), "relationships")
    )
    result = CatalogResult(
        connector=_text(data.get("connector"), "connector"),
        source_type=_text(data.get("source_type"), "source_type"),
        catalog=_text(data.get("catalog"), "catalog"),
        dialect=_optional_text(data.get("dialect"), "dialect"),
        objects=objects,
        relationships=relationships,
    )
    validate_catalog_result(result)
    return result


def validate_catalog_result(catalog: CatalogResult) -> None:
    if not isinstance(catalog, CatalogResult):
        raise ConnectorFailure("invalid_catalog", "Catalog input must be a CatalogResult.")
    _text(catalog.connector, "connector")
    _text(catalog.source_type, "source_type")
    _text(catalog.catalog, "catalog")
    _optional_text(catalog.dialect, "dialect")
    if not isinstance(catalog.objects, tuple) or not isinstance(catalog.relationships, tuple):
        raise ConnectorFailure("invalid_catalog", "Catalog collections must be tuples.")

    object_fields: dict[tuple[str, str], set[str]] = {}
    for item in catalog.objects:
        if not isinstance(item, CatalogObject):
            raise ConnectorFailure("invalid_catalog", "Catalog objects are invalid.")
        _text(item.namespace, "object namespace")
        _text(item.name, "object name")
        if item.kind not in {"table", "view"}:
            raise ConnectorFailure(
                "invalid_catalog",
                f"Unsupported catalog object kind: {item.kind}",
            )
        _optional_text(item.description, "object description")
        key = (item.namespace, item.name)
        if key in object_fields:
            raise ConnectorFailure(
                "invalid_catalog",
                f"Duplicate catalog object: {item.namespace}.{item.name}",
            )
        if not isinstance(item.fields, tuple) or not isinstance(item.primary_key, tuple):
            raise ConnectorFailure("invalid_catalog", "Catalog object collections must be tuples.")
        for primary_key in item.primary_key:
            _text(primary_key, "object primary_key")
        names: set[str] = set()
        positions: set[int] = set()
        flagged_primary_keys: set[str] = set()
        for field in item.fields:
            _validate_field(field, item)
            if field.name in names:
                raise ConnectorFailure(
                    "invalid_catalog",
                    f"Duplicate field in {item.namespace}.{item.name}: {field.name}",
                )
            if field.position in positions:
                raise ConnectorFailure(
                    "invalid_catalog",
                    f"Duplicate field position in {item.namespace}.{item.name}: {field.position}",
                )
            names.add(field.name)
            positions.add(field.position)
            if field.is_primary_key:
                flagged_primary_keys.add(field.name)
        if len(item.primary_key) != len(set(item.primary_key)):
            raise ConnectorFailure(
                "invalid_catalog",
                f"Duplicate primary-key field in {item.namespace}.{item.name}.",
            )
        unknown_primary_keys = set(item.primary_key) - names
        if unknown_primary_keys:
            raise ConnectorFailure(
                "invalid_catalog",
                f"Unknown primary-key field in {item.namespace}.{item.name}: "
                f"{sorted(unknown_primary_keys)[0]}",
            )
        if set(item.primary_key) != flagged_primary_keys:
            raise ConnectorFailure(
                "invalid_catalog",
                f"Primary-key metadata is inconsistent in {item.namespace}.{item.name}.",
            )
        object_fields[key] = names

    relationship_keys: set[tuple[str, str, str]] = set()
    for relationship in catalog.relationships:
        _validate_relationship(relationship, object_fields)
        key = (
            relationship.from_namespace,
            relationship.from_object,
            relationship.name,
        )
        if key in relationship_keys:
            raise ConnectorFailure(
                "invalid_catalog",
                f"Duplicate catalog relationship: {relationship.name}",
            )
        relationship_keys.add(key)


def _catalog_object(data: dict[str, Any]) -> CatalogObject:
    _require_fields(data, _OBJECT_FIELDS, "catalog object")
    return CatalogObject(
        namespace=_text(data.get("namespace"), "object namespace"),
        name=_text(data.get("name"), "object name"),
        kind=_text(data.get("kind"), "object kind"),
        fields=tuple(
            _catalog_field(item) for item in _object_list(data.get("fields"), "object fields")
        ),
        description=_optional_text(data.get("description"), "object description"),
        primary_key=_string_tuple(data.get("primary_key"), "object primary_key"),
    )


def _catalog_field(data: dict[str, Any]) -> CatalogField:
    _require_fields(data, _FIELD_FIELDS, "catalog field")
    position = data.get("position")
    nullable = data.get("nullable")
    is_primary_key = data.get("is_primary_key")
    if not isinstance(position, int) or isinstance(position, bool):
        raise ConnectorFailure("invalid_catalog", "Catalog field position must be an integer.")
    if not isinstance(nullable, bool) or not isinstance(is_primary_key, bool):
        raise ConnectorFailure("invalid_catalog", "Catalog field flags must be booleans.")
    return CatalogField(
        name=_text(data.get("name"), "field name"),
        position=position,
        data_type=_text(data.get("data_type"), "field data_type"),
        nullable=nullable,
        description=_optional_text(data.get("description"), "field description"),
        is_primary_key=is_primary_key,
    )


def _catalog_relationship(data: dict[str, Any]) -> CatalogRelationship:
    _require_fields(data, _RELATIONSHIP_FIELDS, "catalog relationship")
    return CatalogRelationship(
        name=_text(data.get("name"), "relationship name"),
        from_namespace=_text(data.get("from_namespace"), "relationship from_namespace"),
        from_object=_text(data.get("from_object"), "relationship from_object"),
        from_fields=_string_tuple(data.get("from_fields"), "relationship from_fields"),
        to_namespace=_text(data.get("to_namespace"), "relationship to_namespace"),
        to_object=_text(data.get("to_object"), "relationship to_object"),
        to_fields=_string_tuple(data.get("to_fields"), "relationship to_fields"),
        kind=_text(data.get("kind"), "relationship kind"),
    )


def _validate_field(field: CatalogField, item: CatalogObject) -> None:
    if not isinstance(field, CatalogField):
        raise ConnectorFailure("invalid_catalog", "Catalog fields are invalid.")
    _text(field.name, "field name")
    _text(field.data_type, "field data_type")
    _optional_text(field.description, "field description")
    if (
        not isinstance(field.position, int)
        or isinstance(field.position, bool)
        or field.position < 1
    ):
        raise ConnectorFailure(
            "invalid_catalog",
            f"Invalid field position in {item.namespace}.{item.name}: {field.name}",
        )
    if not isinstance(field.nullable, bool) or not isinstance(field.is_primary_key, bool):
        raise ConnectorFailure("invalid_catalog", "Catalog field flags must be booleans.")


def _validate_relationship(
    relationship: CatalogRelationship,
    object_fields: dict[tuple[str, str], set[str]],
) -> None:
    if not isinstance(relationship, CatalogRelationship):
        raise ConnectorFailure("invalid_catalog", "Catalog relationships are invalid.")
    _text(relationship.name, "relationship name")
    _text(relationship.from_namespace, "relationship from_namespace")
    _text(relationship.from_object, "relationship from_object")
    _text(relationship.to_namespace, "relationship to_namespace")
    _text(relationship.to_object, "relationship to_object")
    if not isinstance(relationship.from_fields, tuple) or not isinstance(
        relationship.to_fields, tuple
    ):
        raise ConnectorFailure(
            "invalid_catalog",
            "Catalog relationship field collections must be tuples.",
        )
    for field in (*relationship.from_fields, *relationship.to_fields):
        _text(field, "relationship field")
    if relationship.kind != "foreign_key":
        raise ConnectorFailure(
            "invalid_catalog",
            f"Unsupported catalog relationship kind: {relationship.kind}",
        )
    source = (relationship.from_namespace, relationship.from_object)
    target = (relationship.to_namespace, relationship.to_object)
    if source not in object_fields or target not in object_fields:
        raise ConnectorFailure(
            "invalid_catalog",
            f"Catalog relationship references an unknown object: {relationship.name}",
        )
    if not relationship.from_fields or len(relationship.from_fields) != len(relationship.to_fields):
        raise ConnectorFailure(
            "invalid_catalog",
            f"Catalog relationship field counts do not match: {relationship.name}",
        )
    unknown_source = set(relationship.from_fields) - object_fields[source]
    unknown_target = set(relationship.to_fields) - object_fields[target]
    if unknown_source or unknown_target:
        raise ConnectorFailure(
            "invalid_catalog",
            f"Catalog relationship references an unknown field: {relationship.name}",
        )


def _require_fields(data: dict[str, Any], expected: set[str], label: str) -> None:
    if set(data) != expected:
        raise ConnectorFailure("invalid_catalog", f"Unexpected fields in {label}.")


def _object_list(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ConnectorFailure("invalid_catalog", f"Catalog {label} must contain objects.")
    return value


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ConnectorFailure("invalid_catalog", f"Catalog {label} must contain strings.")
    return tuple(_text(item, label) for item in value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConnectorFailure("invalid_catalog", f"Catalog {label} must be a non-empty string.")
    return value


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)
