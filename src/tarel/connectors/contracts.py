"""Small contracts shared by TAREL and source connectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ConnectorFailure(RuntimeError):
    """A connector failure safe to present at the CLI boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ConnectorManifest:
    contract_version: int
    name: str
    version: str
    source_type: str
    entrypoint: str
    extra: str
    capabilities: tuple[str, ...]
    dependencies: tuple[str, ...]
    permissions: tuple[str, ...]
    dialect: str | None
    references: tuple[str, ...]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ConnectorManifest:
        connector = data.get("connector")
        if not isinstance(connector, dict):
            raise ConnectorFailure("invalid_manifest", "Manifest requires a [connector] table.")

        try:
            manifest = cls(
                contract_version=int(connector["contract_version"]),
                name=_required_string(connector, "name"),
                version=_required_string(connector, "version"),
                source_type=_required_string(connector, "source_type"),
                entrypoint=_required_string(connector, "entrypoint"),
                extra=_required_string(connector, "extra"),
                capabilities=_string_tuple(connector, "capabilities"),
                dependencies=_string_tuple(connector, "dependencies"),
                permissions=_string_tuple(connector, "permissions"),
                dialect=_optional_string(connector, "dialect"),
                references=_optional_string_tuple(connector, "references"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorFailure("invalid_manifest", "Manifest contains invalid fields.") from exc

        if manifest.contract_version != 1:
            raise ConnectorFailure(
                "unsupported_contract",
                f"Connector contract {manifest.contract_version} is not supported.",
            )
        if "probe" not in manifest.capabilities:
            raise ConnectorFailure(
                "invalid_manifest",
                "Connector must declare the probe capability.",
            )
        if "discover_catalog" not in manifest.capabilities:
            raise ConnectorFailure(
                "invalid_manifest",
                "Connector must declare the discover_catalog capability.",
            )
        if any(permission != "read" for permission in manifest.permissions):
            raise ConnectorFailure(
                "unsafe_manifest",
                "Only read permission is accepted by the current connector host.",
            )
        return manifest


@dataclass(frozen=True, slots=True)
class ConnectorCheck:
    name: str
    version: str
    source_type: str
    available: bool
    capabilities: tuple[str, ...]
    permissions: tuple[str, ...]
    missing_dependencies: tuple[str, ...]
    extra: str
    dialect: str | None
    references: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "capabilities": list(self.capabilities),
            "dialect": self.dialect,
            "extra": self.extra,
            "missing_dependencies": list(self.missing_dependencies),
            "name": self.name,
            "permissions": list(self.permissions),
            "references": list(self.references),
            "source_type": self.source_type,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class ProbeRequest:
    url: str = field(repr=False)
    database: str | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    connector: str
    source_type: str
    server_name: str | None
    database_name: str
    product_version: str | None
    product_level: str | None
    edition: str | None
    capabilities: tuple[str, ...]
    read_only: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "capabilities": list(self.capabilities),
            "connector": self.connector,
            "database_name": self.database_name,
            "edition": self.edition,
            "product_level": self.product_level,
            "product_version": self.product_version,
            "read_only": self.read_only,
            "server_name": self.server_name,
            "source_type": self.source_type,
            "status": "ok",
        }


@dataclass(frozen=True, slots=True)
class CatalogRequest:
    url: str = field(repr=False)
    database: str | None = None
    namespace: str | None = None


@dataclass(frozen=True, slots=True)
class CatalogField:
    name: str
    position: int
    data_type: str
    nullable: bool
    description: str | None = None
    is_primary_key: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "data_type": self.data_type,
            "description": self.description,
            "is_primary_key": self.is_primary_key,
            "name": self.name,
            "nullable": self.nullable,
            "position": self.position,
        }


@dataclass(frozen=True, slots=True)
class CatalogObject:
    namespace: str
    name: str
    kind: str
    fields: tuple[CatalogField, ...]
    description: str | None = None
    primary_key: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": [item.to_dict() for item in self.fields],
            "description": self.description,
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
            "primary_key": list(self.primary_key),
        }


@dataclass(frozen=True, slots=True)
class CatalogRelationship:
    name: str
    from_namespace: str
    from_object: str
    from_fields: tuple[str, ...]
    to_namespace: str
    to_object: str
    to_fields: tuple[str, ...]
    kind: str = "foreign_key"

    def to_dict(self) -> dict[str, object]:
        return {
            "from_fields": list(self.from_fields),
            "from_namespace": self.from_namespace,
            "from_object": self.from_object,
            "kind": self.kind,
            "name": self.name,
            "to_fields": list(self.to_fields),
            "to_namespace": self.to_namespace,
            "to_object": self.to_object,
        }


@dataclass(frozen=True, slots=True)
class CatalogResult:
    connector: str
    source_type: str
    catalog: str
    dialect: str | None
    objects: tuple[CatalogObject, ...]
    relationships: tuple[CatalogRelationship, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog": self.catalog,
            "connector": self.connector,
            "dialect": self.dialect,
            "objects": [item.to_dict() for item in self.objects],
            "relationships": [item.to_dict() for item in self.relationships],
            "source_type": self.source_type,
            "status": "ok",
        }


@dataclass(frozen=True, slots=True)
class SampleRequest:
    url: str = field(repr=False)
    database: str | None
    namespace: str
    object_name: str
    limit: int = 3


@dataclass(frozen=True, slots=True)
class SampleResult:
    connector: str
    catalog: str
    namespace: str
    object_name: str
    selected_fields: tuple[str, ...]
    omitted_fields: tuple[str, ...]
    ordered_by: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
    truncated_values: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog": self.catalog,
            "connector": self.connector,
            "namespace": self.namespace,
            "object_name": self.object_name,
            "omitted_fields": list(self.omitted_fields),
            "ordered_by": list(self.ordered_by),
            "rows": list(self.rows),
            "selected_fields": list(self.selected_fields),
            "status": "ok",
            "truncated_values": self.truncated_values,
        }


@dataclass(frozen=True, slots=True)
class ValueCount:
    value: object
    count: int

    def to_dict(self) -> dict[str, object]:
        return {"count": self.count, "value": self.value}


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    name: str
    data_type: str
    status: str
    reason: str | None
    non_null_count: int | None
    null_count: int | None
    distinct_count: int | None
    min_value: object | None
    max_value: object | None
    min_length: int | None
    max_length: int | None
    values: tuple[ValueCount, ...] = ()
    values_complete: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "data_type": self.data_type,
            "distinct_count": self.distinct_count,
            "max_length": self.max_length,
            "max_value": self.max_value,
            "min_length": self.min_length,
            "min_value": self.min_value,
            "name": self.name,
            "non_null_count": self.non_null_count,
            "null_count": self.null_count,
            "reason": self.reason,
            "status": self.status,
            "values": [item.to_dict() for item in self.values],
            "values_complete": self.values_complete,
        }


@dataclass(frozen=True, slots=True)
class ObjectProfileRequest:
    url: str = field(repr=False)
    database: str | None
    namespace: str
    object_name: str
    row_limit: int = 10_000
    small_domain_limit: int = 20
    include_values: bool = False


@dataclass(frozen=True, slots=True)
class ObjectProfileResult:
    connector: str
    catalog: str
    namespace: str
    object_name: str
    row_limit: int
    rows_profiled: int
    complete: bool
    ordered_by: tuple[str, ...]
    columns: tuple[ColumnProfile, ...]
    includes_values: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog": self.catalog,
            "columns": [item.to_dict() for item in self.columns],
            "complete": self.complete,
            "connector": self.connector,
            "includes_values": self.includes_values,
            "namespace": self.namespace,
            "object_name": self.object_name,
            "ordered_by": list(self.ordered_by),
            "row_limit": self.row_limit,
            "rows_profiled": self.rows_profiled,
            "status": "ok",
        }


@dataclass(frozen=True, slots=True)
class RelationshipPair:
    from_namespace: str
    from_object: str
    from_field: str
    to_namespace: str
    to_object: str
    to_field: str

    def to_dict(self) -> dict[str, str]:
        return {
            "from_field": self.from_field,
            "from_namespace": self.from_namespace,
            "from_object": self.from_object,
            "to_field": self.to_field,
            "to_namespace": self.to_namespace,
            "to_object": self.to_object,
        }


@dataclass(frozen=True, slots=True)
class RelationshipProbeRequest:
    url: str = field(repr=False)
    database: str | None
    pairs: tuple[RelationshipPair, ...]
    row_limit: int = 10_000


@dataclass(frozen=True, slots=True)
class RelationshipPairProfile:
    pair: RelationshipPair
    source_non_null_count: int
    source_distinct_count: int
    target_non_null_count: int
    target_distinct_count: int
    overlap_count: int
    profile_row_limit: int

    @property
    def source_coverage(self) -> float:
        return self.overlap_count / max(1, self.source_distinct_count)

    @property
    def target_coverage(self) -> float:
        return self.overlap_count / max(1, self.target_distinct_count)

    @property
    def target_uniqueness(self) -> float:
        return self.target_distinct_count / max(1, self.target_non_null_count)

    def to_dict(self) -> dict[str, object]:
        return {
            "overlap_count": self.overlap_count,
            "pair": self.pair.to_dict(),
            "profile_row_limit": self.profile_row_limit,
            "source_coverage": round(self.source_coverage, 6),
            "source_distinct_count": self.source_distinct_count,
            "source_non_null_count": self.source_non_null_count,
            "target_coverage": round(self.target_coverage, 6),
            "target_distinct_count": self.target_distinct_count,
            "target_non_null_count": self.target_non_null_count,
            "target_uniqueness": round(self.target_uniqueness, 6),
        }


@dataclass(frozen=True, slots=True)
class RelationshipProbeResult:
    connector: str
    catalog: str
    profiles: tuple[RelationshipPairProfile, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog": self.catalog,
            "connector": self.connector,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "status": "ok",
        }


class Connector(Protocol):
    manifest: ConnectorManifest

    def probe(self, request: ProbeRequest) -> ProbeResult: ...

    def discover_catalog(self, request: CatalogRequest) -> CatalogResult: ...

    def sample_rows(self, request: SampleRequest) -> SampleResult: ...


class ObjectProfileConnector(Protocol):
    def profile_object(self, request: ObjectProfileRequest) -> ObjectProfileResult: ...


class RelationshipProbeConnector(Protocol):
    def probe_relationships(self, request: RelationshipProbeRequest) -> RelationshipProbeResult: ...


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key)
    return value.strip()


def _string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(key)
    return tuple(value)


def _optional_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(key)
    return value.strip()


def _optional_string_tuple(data: dict[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(key)
    return tuple(value)
