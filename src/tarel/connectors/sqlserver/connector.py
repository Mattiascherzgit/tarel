"""Read-only SQL Server connector implemented with the optional pymssql driver."""

from __future__ import annotations

import datetime as dt
import decimal
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit

from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogRequest,
    CatalogResult,
    ColumnProfile,
    ConnectorFailure,
    ConnectorManifest,
    ObjectProfileRequest,
    ObjectProfileResult,
    ProbeRequest,
    ProbeResult,
    RelationshipPair,
    RelationshipPairProfile,
    RelationshipProbeRequest,
    RelationshipProbeResult,
    SampleRequest,
    SampleResult,
    ValueCount,
)

_PROBE_SQL = """
SELECT
    CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) AS server_name,
    DB_NAME() AS database_name,
    CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS product_version,
    CAST(SERVERPROPERTY('ProductLevel') AS nvarchar(128)) AS product_level,
    CAST(SERVERPROPERTY('Edition') AS nvarchar(256)) AS edition
"""

_DISCOVER_CATALOG_SQL = """
SELECT
    schemas.name AS namespace_name,
    objects.name AS object_name,
    CASE objects.type WHEN 'U' THEN 'table' ELSE 'view' END AS object_kind,
    columns.name AS field_name,
    columns.column_id AS field_position,
    types.name AS type_name,
    columns.max_length,
    columns.precision,
    columns.scale,
    columns.is_nullable,
    CAST(object_description.value AS nvarchar(max)) AS object_description,
    CAST(field_description.value AS nvarchar(max)) AS field_description
FROM sys.objects AS objects
JOIN sys.schemas AS schemas ON schemas.schema_id = objects.schema_id
JOIN sys.columns AS columns ON columns.object_id = objects.object_id
JOIN sys.types AS types ON types.user_type_id = columns.user_type_id
LEFT JOIN sys.extended_properties AS object_description
  ON object_description.major_id = objects.object_id
 AND object_description.minor_id = 0
 AND object_description.name = 'MS_Description'
LEFT JOIN sys.extended_properties AS field_description
  ON field_description.major_id = objects.object_id
 AND field_description.minor_id = columns.column_id
 AND field_description.name = 'MS_Description'
WHERE objects.type IN ('U', 'V')
  AND objects.is_ms_shipped = 0
ORDER BY schemas.name, objects.name, objects.type, columns.column_id
"""

_DISCOVER_NAMESPACE_SQL = """
SELECT
    schemas.name AS namespace_name,
    objects.name AS object_name,
    CASE objects.type WHEN 'U' THEN 'table' ELSE 'view' END AS object_kind,
    columns.name AS field_name,
    columns.column_id AS field_position,
    types.name AS type_name,
    columns.max_length,
    columns.precision,
    columns.scale,
    columns.is_nullable,
    CAST(object_description.value AS nvarchar(max)) AS object_description,
    CAST(field_description.value AS nvarchar(max)) AS field_description
FROM sys.objects AS objects
JOIN sys.schemas AS schemas ON schemas.schema_id = objects.schema_id
JOIN sys.columns AS columns ON columns.object_id = objects.object_id
JOIN sys.types AS types ON types.user_type_id = columns.user_type_id
LEFT JOIN sys.extended_properties AS object_description
  ON object_description.major_id = objects.object_id
 AND object_description.minor_id = 0
 AND object_description.name = 'MS_Description'
LEFT JOIN sys.extended_properties AS field_description
  ON field_description.major_id = objects.object_id
 AND field_description.minor_id = columns.column_id
 AND field_description.name = 'MS_Description'
WHERE objects.type IN ('U', 'V')
  AND objects.is_ms_shipped = 0
  AND schemas.name = %s
ORDER BY schemas.name, objects.name, objects.type, columns.column_id
"""

_PRIMARY_KEYS_SQL = """
SELECT
    schemas.name AS namespace_name,
    objects.name AS object_name,
    columns.name AS field_name,
    index_columns.key_ordinal
FROM sys.key_constraints AS constraints
JOIN sys.objects AS objects ON objects.object_id = constraints.parent_object_id
JOIN sys.schemas AS schemas ON schemas.schema_id = objects.schema_id
JOIN sys.index_columns AS index_columns
  ON index_columns.object_id = constraints.parent_object_id
 AND index_columns.index_id = constraints.unique_index_id
JOIN sys.columns AS columns
  ON columns.object_id = index_columns.object_id
 AND columns.column_id = index_columns.column_id
WHERE constraints.type = 'PK'
{namespace_filter}
ORDER BY schemas.name, objects.name, index_columns.key_ordinal
"""

_FOREIGN_KEYS_SQL = """
SELECT
    foreign_keys.name AS relationship_name,
    source_schemas.name AS from_namespace,
    source_objects.name AS from_object,
    source_columns.name AS from_field,
    target_schemas.name AS to_namespace,
    target_objects.name AS to_object,
    target_columns.name AS to_field,
    foreign_key_columns.constraint_column_id AS field_position
FROM sys.foreign_keys AS foreign_keys
JOIN sys.foreign_key_columns AS foreign_key_columns
  ON foreign_key_columns.constraint_object_id = foreign_keys.object_id
JOIN sys.objects AS source_objects
  ON source_objects.object_id = foreign_keys.parent_object_id
JOIN sys.schemas AS source_schemas
  ON source_schemas.schema_id = source_objects.schema_id
JOIN sys.columns AS source_columns
  ON source_columns.object_id = source_objects.object_id
 AND source_columns.column_id = foreign_key_columns.parent_column_id
JOIN sys.objects AS target_objects
  ON target_objects.object_id = foreign_keys.referenced_object_id
JOIN sys.schemas AS target_schemas
  ON target_schemas.schema_id = target_objects.schema_id
JOIN sys.columns AS target_columns
  ON target_columns.object_id = target_objects.object_id
 AND target_columns.column_id = foreign_key_columns.referenced_column_id
WHERE foreign_keys.is_ms_shipped = 0
{namespace_filter}
ORDER BY source_schemas.name, source_objects.name, foreign_keys.name,
         foreign_key_columns.constraint_column_id
"""

_SAMPLE_FIELDS_SQL = """
SELECT
    columns.name AS field_name,
    types.name AS type_name,
    columns.max_length,
    columns.precision,
    columns.scale,
    columns.column_id,
    CASE WHEN EXISTS (
        SELECT 1
        FROM sys.key_constraints AS constraints
        JOIN sys.index_columns AS index_columns
          ON index_columns.object_id = constraints.parent_object_id
         AND index_columns.index_id = constraints.unique_index_id
        WHERE constraints.type = 'PK'
          AND constraints.parent_object_id = objects.object_id
          AND index_columns.column_id = columns.column_id
    ) THEN 1 ELSE 0 END AS is_primary_key
FROM sys.objects AS objects
JOIN sys.schemas AS schemas ON schemas.schema_id = objects.schema_id
JOIN sys.columns AS columns ON columns.object_id = objects.object_id
JOIN sys.types AS types ON types.user_type_id = columns.user_type_id
WHERE schemas.name = %s
  AND objects.name = %s
  AND objects.type IN ('U', 'V')
  AND objects.is_ms_shipped = 0
ORDER BY columns.column_id
"""

_UNSAFE_SAMPLE_TYPES = {
    "binary",
    "geography",
    "geometry",
    "hierarchyid",
    "image",
    "ntext",
    "rowversion",
    "sql_variant",
    "text",
    "timestamp",
    "varbinary",
    "xml",
}
_MAX_SAMPLE_FIELDS = 32
_MAX_SAMPLE_VALUE_CHARS = 256
_MAX_SAMPLE_TOTAL_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class _ConnectionTarget:
    host: str
    port: int
    username: str
    password: str
    database: str


class SqlServerConnector:
    def __init__(self, manifest: ConnectorManifest) -> None:
        self.manifest = manifest

    def probe(self, request: ProbeRequest) -> ProbeResult:
        target = _parse_target(request)
        try:
            import pymssql
        except ImportError as exc:  # Defensive: the host normally catches this first.
            raise ConnectorFailure(
                "missing_dependency",
                "SQL Server requires the optional pymssql dependency.",
            ) from exc

        connection: Any | None = None
        cursor: Any | None = None
        row: Any = None
        try:
            connection = pymssql.connect(
                server=target.host,
                port=str(target.port),
                user=target.username,
                password=target.password,
                database=target.database,
                login_timeout=5,
                timeout=15,
                as_dict=True,
            )
            cursor = connection.cursor(as_dict=True)
            cursor.execute(_PROBE_SQL)
            row = cursor.fetchone()
        except Exception as exc:
            raise ConnectorFailure(
                "connection_failed",
                f"SQL Server probe failed for {target.host}/{target.database} "
                f"({type(exc).__name__}).",
            ) from exc
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()
            if connection is not None:
                with suppress(Exception):
                    connection.close()

        if not isinstance(row, dict) or not row.get("database_name"):
            raise ConnectorFailure(
                "invalid_response",
                "SQL Server probe returned no database identity.",
            )

        return ProbeResult(
            connector=self.manifest.name,
            source_type=self.manifest.source_type,
            server_name=_optional_text(row.get("server_name")),
            database_name=str(row["database_name"]),
            product_version=_optional_text(row.get("product_version")),
            product_level=_optional_text(row.get("product_level")),
            edition=_optional_text(row.get("edition")),
            capabilities=self.manifest.capabilities,
        )

    def discover_catalog(self, request: CatalogRequest) -> CatalogResult:
        target = _parse_target(request)
        try:
            import pymssql
        except ImportError as exc:  # Defensive: the host normally catches this first.
            raise ConnectorFailure(
                "missing_dependency",
                "SQL Server requires the optional pymssql dependency.",
            ) from exc

        connection: Any | None = None
        cursor: Any | None = None
        try:
            connection = pymssql.connect(
                server=target.host,
                port=str(target.port),
                user=target.username,
                password=target.password,
                database=target.database,
                login_timeout=5,
                timeout=30,
                as_dict=True,
            )
            cursor = connection.cursor(as_dict=True)
            if request.namespace is None:
                cursor.execute(_DISCOVER_CATALOG_SQL)
            else:
                cursor.execute(_DISCOVER_NAMESPACE_SQL, (request.namespace,))
            rows = cursor.fetchall()
            primary_key_rows = _execute_optional_namespace_query(
                cursor,
                _PRIMARY_KEYS_SQL,
                request.namespace,
                "schemas.name",
            )
            foreign_key_rows = _execute_optional_namespace_query(
                cursor,
                _FOREIGN_KEYS_SQL,
                request.namespace,
                "source_schemas.name",
            )
        except Exception as exc:
            raise ConnectorFailure(
                "discovery_failed",
                f"SQL Server catalog discovery failed for {target.host}/{target.database} "
                f"({type(exc).__name__}).",
            ) from exc
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()
            if connection is not None:
                with suppress(Exception):
                    connection.close()

        return CatalogResult(
            connector=self.manifest.name,
            source_type=self.manifest.source_type,
            catalog=target.database,
            dialect=self.manifest.dialect,
            objects=_catalog_objects(rows, primary_key_rows),
            relationships=_catalog_relationships(foreign_key_rows),
        )

    def sample_rows(self, request: SampleRequest) -> SampleResult:
        if not 1 <= request.limit <= 10:
            raise ConnectorFailure("invalid_sample_limit", "Sample limit must be between 1 and 10.")
        target = _parse_target(request)
        try:
            import pymssql
        except ImportError as exc:
            raise ConnectorFailure(
                "missing_dependency",
                "SQL Server requires the optional pymssql dependency.",
            ) from exc

        connection: Any | None = None
        cursor: Any | None = None
        try:
            connection = pymssql.connect(
                server=target.host,
                port=str(target.port),
                user=target.username,
                password=target.password,
                database=target.database,
                login_timeout=5,
                timeout=30,
                as_dict=True,
            )
            cursor = connection.cursor(as_dict=True)
            cursor.execute(_SAMPLE_FIELDS_SQL, (request.namespace, request.object_name))
            field_rows = cursor.fetchall()
            selected, omitted, ordered_by = _sample_fields(field_rows)
            if not selected:
                raise ConnectorFailure(
                    "no_sample_fields",
                    f"No bounded sample fields are available for "
                    f"{request.namespace}.{request.object_name}.",
                )
            select_list = ", ".join(_quote_identifier(name) for name in selected)
            qualified_name = (
                f"{_quote_identifier(request.namespace)}."
                f"{_quote_identifier(request.object_name)}"
            )
            order_clause = ""
            if ordered_by:
                order_clause = " ORDER BY " + ", ".join(
                    _quote_identifier(name) for name in ordered_by
                )
            query = (
                f"SELECT TOP ({request.limit}) {select_list} "
                f"FROM {qualified_name}{order_clause}"
            )
            cursor.execute(query)
            raw_rows = cursor.fetchall()
        except ConnectorFailure:
            raise
        except Exception as exc:
            raise ConnectorFailure(
                "sampling_failed",
                f"SQL Server sampling failed for {target.host}/{target.database}/"
                f"{request.namespace}.{request.object_name} ({type(exc).__name__}).",
            ) from exc
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()
            if connection is not None:
                with suppress(Exception):
                    connection.close()

        rows, truncated = _bounded_sample_rows(raw_rows, selected)
        return SampleResult(
            connector=self.manifest.name,
            catalog=target.database,
            namespace=request.namespace,
            object_name=request.object_name,
            selected_fields=selected,
            omitted_fields=omitted,
            ordered_by=ordered_by,
            rows=rows,
            truncated_values=truncated,
        )

    def profile_object(self, request: ObjectProfileRequest) -> ObjectProfileResult:
        _validate_profile_request(request)
        target = _parse_target(request)
        try:
            import pymssql
        except ImportError as exc:
            raise ConnectorFailure(
                "missing_dependency",
                "SQL Server requires the optional pymssql dependency.",
            ) from exc

        connection: Any | None = None
        cursor: Any | None = None
        try:
            connection = pymssql.connect(
                server=target.host,
                port=str(target.port),
                user=target.username,
                password=target.password,
                database=target.database,
                login_timeout=5,
                timeout=30,
                as_dict=True,
            )
            cursor = connection.cursor(as_dict=True)
            cursor.execute(_SAMPLE_FIELDS_SQL, (request.namespace, request.object_name))
            field_rows = cursor.fetchall()
            if not field_rows:
                raise ConnectorFailure(
                    "object_not_found",
                    f"SQL Server object not found: {request.namespace}.{request.object_name}",
                )
            selected, _omitted, primary_keys = _sample_fields(field_rows)
            ordered_by = primary_keys or selected[:1]
            if not ordered_by:
                raise ConnectorFailure(
                    "no_profile_fields",
                    f"No bounded profile fields are available for "
                    f"{request.namespace}.{request.object_name}.",
                )
            cursor.execute(
                _profile_row_count_query(request, ordered_by, request.row_limit + 1)
            )
            count_row = cursor.fetchone()
            if count_row is None:
                raise ConnectorFailure(
                    "profiling_failed",
                    "SQL Server profiling returned no bounded row count.",
                )
            observed_rows = int(count_row["row_count"])
            complete = observed_rows <= request.row_limit
            columns = tuple(
                _profile_column(
                    cursor,
                    request,
                    field_row,
                    ordered_by,
                    profile_complete=complete,
                )
                for field_row in field_rows
            )
        except ConnectorFailure:
            raise
        except Exception as exc:
            raise ConnectorFailure(
                "profiling_failed",
                f"SQL Server profiling failed for {target.host}/{target.database}/"
                f"{request.namespace}.{request.object_name} ({type(exc).__name__}).",
            ) from exc
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()
            if connection is not None:
                with suppress(Exception):
                    connection.close()

        return ObjectProfileResult(
            connector=self.manifest.name,
            catalog=target.database,
            namespace=request.namespace,
            object_name=request.object_name,
            row_limit=request.row_limit,
            rows_profiled=min(observed_rows, request.row_limit),
            complete=complete,
            ordered_by=ordered_by,
            columns=columns,
            includes_values=any(column.values for column in columns),
        )

    def probe_relationships(self, request: RelationshipProbeRequest) -> RelationshipProbeResult:
        if not 1 <= len(request.pairs) <= 50:
            raise ConnectorFailure(
                "invalid_relationship_probe",
                "Relationship probes require between 1 and 50 field pairs.",
            )
        if not 1 <= request.row_limit <= 100_000:
            raise ConnectorFailure(
                "invalid_profile_row_limit",
                "Relationship profile row limit must be between 1 and 100000.",
            )
        target = _parse_target(request)
        try:
            import pymssql
        except ImportError as exc:
            raise ConnectorFailure(
                "missing_dependency",
                "SQL Server requires the optional pymssql dependency.",
            ) from exc

        connection: Any | None = None
        cursor: Any | None = None
        profiles: list[RelationshipPairProfile] = []
        try:
            connection = pymssql.connect(
                server=target.host,
                port=str(target.port),
                user=target.username,
                password=target.password,
                database=target.database,
                login_timeout=5,
                timeout=30,
                as_dict=True,
            )
            cursor = connection.cursor(as_dict=True)
            for pair in request.pairs:
                cursor.execute(_relationship_probe_query(pair, request.row_limit))
                row = cursor.fetchone()
                if row is None:
                    raise ConnectorFailure(
                        "relationship_probe_failed",
                        "SQL Server relationship probe returned no aggregate row.",
                    )
                profiles.append(
                    RelationshipPairProfile(
                        pair=pair,
                        source_non_null_count=int(row["source_non_null_count"] or 0),
                        source_distinct_count=int(row["source_distinct_count"] or 0),
                        target_non_null_count=int(row["target_non_null_count"] or 0),
                        target_distinct_count=int(row["target_distinct_count"] or 0),
                        overlap_count=int(row["overlap_count"] or 0),
                        profile_row_limit=request.row_limit,
                    )
                )
        except ConnectorFailure:
            raise
        except Exception as exc:
            raise ConnectorFailure(
                "relationship_probe_failed",
                f"SQL Server relationship probe failed for {target.host}/{target.database} "
                f"({type(exc).__name__}).",
            ) from exc
        finally:
            if cursor is not None:
                with suppress(Exception):
                    cursor.close()
            if connection is not None:
                with suppress(Exception):
                    connection.close()

        return RelationshipProbeResult(
            connector=self.manifest.name,
            catalog=target.database,
            profiles=tuple(profiles),
        )


def create_connector(manifest: ConnectorManifest) -> SqlServerConnector:
    return SqlServerConnector(manifest)


def _parse_target(
    request: (
        ProbeRequest
        | CatalogRequest
        | SampleRequest
        | ObjectProfileRequest
        | RelationshipProbeRequest
    ),
) -> _ConnectionTarget:
    parsed = urlsplit(request.url)
    if parsed.scheme != "mssql+pymssql":
        raise ConnectorFailure(
            "unsupported_url",
            "SQL Server currently requires an mssql+pymssql connection URL.",
        )
    if not parsed.hostname or parsed.username is None or parsed.password is None:
        raise ConnectorFailure(
            "invalid_url",
            "SQL Server URL requires host, username, and password.",
        )

    configured_database = unquote(parsed.path.lstrip("/"))
    database = request.database or configured_database
    if not database:
        raise ConnectorFailure("missing_database", "SQL Server probe requires a database.")

    try:
        port = parsed.port or 1433
    except ValueError as exc:
        raise ConnectorFailure("invalid_url", "SQL Server URL contains an invalid port.") from exc

    return _ConnectionTarget(
        host=parsed.hostname,
        port=port,
        username=unquote(parsed.username),
        password=unquote(parsed.password),
        database=database,
    )


def _optional_text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _catalog_objects(
    rows: list[dict[str, Any]],
    primary_key_rows: list[dict[str, Any]],
) -> tuple[CatalogObject, ...]:
    primary_keys: dict[tuple[str, str], list[str]] = {}
    for row in primary_key_rows:
        primary_keys.setdefault(
            (str(row["namespace_name"]), str(row["object_name"])),
            [],
        ).append(str(row["field_name"]))
    grouped: dict[tuple[str, str, str], list[CatalogField]] = {}
    descriptions: dict[tuple[str, str, str], str | None] = {}
    for row in rows:
        key = (
            str(row["namespace_name"]),
            str(row["object_name"]),
            str(row["object_kind"]),
        )
        descriptions[key] = _optional_text(row.get("object_description"))
        primary_key = primary_keys.get((key[0], key[1]), [])
        grouped.setdefault(key, []).append(
            CatalogField(
                name=str(row["field_name"]),
                position=int(row["field_position"]),
                data_type=_format_data_type(row),
                nullable=bool(row["is_nullable"]),
                description=_optional_text(row.get("field_description")),
                is_primary_key=str(row["field_name"]) in primary_key,
            )
        )
    return tuple(
        CatalogObject(
            namespace=namespace,
            name=name,
            kind=kind,
            fields=tuple(fields),
            description=descriptions[(namespace, name, kind)],
            primary_key=tuple(primary_keys.get((namespace, name), [])),
        )
        for (namespace, name, kind), fields in grouped.items()
    )


def _catalog_relationships(rows: list[dict[str, Any]]) -> tuple[CatalogRelationship, ...]:
    grouped: dict[tuple[str, str, str, str, str], tuple[list[str], list[str]]] = {}
    for row in rows:
        key = (
            str(row["relationship_name"]),
            str(row["from_namespace"]),
            str(row["from_object"]),
            str(row["to_namespace"]),
            str(row["to_object"]),
        )
        from_fields, to_fields = grouped.setdefault(key, ([], []))
        from_fields.append(str(row["from_field"]))
        to_fields.append(str(row["to_field"]))
    return tuple(
        CatalogRelationship(
            name=name,
            from_namespace=from_namespace,
            from_object=from_object,
            from_fields=tuple(from_fields),
            to_namespace=to_namespace,
            to_object=to_object,
            to_fields=tuple(to_fields),
        )
        for (
            name,
            from_namespace,
            from_object,
            to_namespace,
            to_object,
        ), (from_fields, to_fields) in grouped.items()
    )


def _execute_optional_namespace_query(
    cursor: Any,
    template: str,
    namespace: str | None,
    namespace_column: str,
) -> list[dict[str, Any]]:
    clause = f"AND {namespace_column} = %s" if namespace else ""
    query = template.format(namespace_filter=clause)
    if namespace:
        cursor.execute(query, (namespace,))
    else:
        cursor.execute(query)
    return cursor.fetchall()


def _sample_fields(
    rows: list[dict[str, Any]],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    selected: list[str] = []
    omitted: list[str] = []
    primary_keys: list[str] = []
    for row in rows:
        name = str(row["field_name"])
        type_name = str(row["type_name"]).lower()
        max_length = int(row["max_length"])
        is_safe = (
            type_name not in _UNSAFE_SAMPLE_TYPES
            and max_length != -1
            and (max_length <= 1024 or max_length == 0)
            and len(selected) < _MAX_SAMPLE_FIELDS
        )
        if is_safe:
            selected.append(name)
            if bool(row["is_primary_key"]):
                primary_keys.append(name)
        else:
            omitted.append(name)
    return tuple(selected), tuple(omitted), tuple(primary_keys)


def _bounded_sample_rows(
    rows: list[dict[str, Any]],
    selected_fields: tuple[str, ...],
) -> tuple[tuple[dict[str, object], ...], bool]:
    result: list[dict[str, object]] = []
    used_chars = 0
    truncated = False
    for row in rows:
        converted: dict[str, object] = {}
        for field_name in selected_fields:
            value, value_truncated = _sample_value(row.get(field_name))
            converted[field_name] = value
            truncated = truncated or value_truncated
            used_chars += len(str(value))
        if used_chars > _MAX_SAMPLE_TOTAL_CHARS:
            truncated = True
            break
        result.append(converted)
    return tuple(result), truncated


def _sample_value(value: Any) -> tuple[object, bool]:
    if value is None or isinstance(value, bool | int | float):
        return value, False
    if isinstance(value, decimal.Decimal):
        return str(value), False
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat(), False
    if isinstance(value, uuid.UUID):
        return str(value), False
    text = str(value)
    if len(text) > _MAX_SAMPLE_VALUE_CHARS:
        return text[:_MAX_SAMPLE_VALUE_CHARS] + "…", True
    return text, False


def _validate_profile_request(request: ObjectProfileRequest) -> None:
    if not 1 <= request.row_limit <= 100_000:
        raise ConnectorFailure(
            "invalid_profile_row_limit",
            "Object profile row limit must be between 1 and 100000.",
        )
    if not 1 <= request.small_domain_limit <= 100:
        raise ConnectorFailure(
            "invalid_small_domain_limit",
            "Small-domain limit must be between 1 and 100.",
        )


def _profile_row_count_query(
    request: ObjectProfileRequest,
    ordered_by: tuple[str, ...],
    limit: int,
) -> str:
    qualified_name = (
        f"{_quote_identifier(request.namespace)}.{_quote_identifier(request.object_name)}"
    )
    order_clause = ", ".join(_quote_identifier(name) for name in ordered_by)
    return (
        "SELECT COUNT_BIG(1) AS row_count FROM ("
        f"SELECT TOP ({limit}) 1 AS present FROM {qualified_name} ORDER BY {order_clause}"
        ") AS profile_rows"
    )


def _profile_column(
    cursor: Any,
    request: ObjectProfileRequest,
    field_row: dict[str, Any],
    ordered_by: tuple[str, ...],
    *,
    profile_complete: bool,
) -> ColumnProfile:
    name = str(field_row["field_name"])
    type_name = str(field_row["type_name"]).lower()
    data_type = _format_data_type(field_row)
    max_length = int(field_row["max_length"])
    if (
        type_name in _UNSAFE_SAMPLE_TYPES
        or max_length == -1
        or (max_length > 1024 and max_length != 0)
    ):
        return ColumnProfile(
            name=name,
            data_type=data_type,
            status="omitted",
            reason="unsupported_type",
            non_null_count=None,
            null_count=None,
            distinct_count=None,
            min_value=None,
            max_value=None,
            min_length=None,
            max_length=None,
        )
    qualified_name = (
        f"{_quote_identifier(request.namespace)}.{_quote_identifier(request.object_name)}"
    )
    field = _quote_identifier(name)
    order_clause = ", ".join(_quote_identifier(item) for item in ordered_by)
    value_expression = _profile_value_expression(field, type_name)
    length_expressions = (
        "MIN(LEN(value)) AS min_length, MAX(LEN(value)) AS max_length"
        if type_name in {"char", "nchar", "varchar", "nvarchar"}
        else "NULL AS min_length, NULL AS max_length"
    )
    query = f"""
WITH profile_rows AS (
    SELECT TOP ({request.row_limit}) {value_expression} AS value
    FROM {qualified_name}
    ORDER BY {order_clause}
)
SELECT
    COUNT_BIG(value) AS non_null_count,
    COUNT_BIG(1) - COUNT_BIG(value) AS null_count,
    COUNT_BIG(DISTINCT value) AS distinct_count,
    MIN(value) AS min_value,
    MAX(value) AS max_value,
    {length_expressions}
FROM profile_rows
"""
    cursor.execute(query)
    row = cursor.fetchone()
    if row is None:
        raise ConnectorFailure(
            "profiling_failed",
            f"SQL Server returned no profile for {request.namespace}.{request.object_name}.{name}.",
        )
    distinct_count = int(row["distinct_count"] or 0)
    values: tuple[ValueCount, ...] = ()
    values_complete = False
    if (
        request.include_values
        and profile_complete
        and distinct_count <= request.small_domain_limit
    ):
        cursor.execute(
            f"""
WITH profile_rows AS (
    SELECT TOP ({request.row_limit}) {value_expression} AS value
    FROM {qualified_name}
    ORDER BY {order_clause}
)
SELECT value, COUNT_BIG(1) AS value_count
FROM profile_rows
WHERE value IS NOT NULL
GROUP BY value
ORDER BY value_count DESC, value
"""
        )
        values = tuple(
            ValueCount(
                value=_sample_value(item.get("value"))[0],
                count=int(item["value_count"]),
            )
            for item in cursor.fetchall()
        )
        values_complete = profile_complete
    return ColumnProfile(
        name=name,
        data_type=data_type,
        status="profiled",
        reason=None,
        non_null_count=int(row["non_null_count"] or 0),
        null_count=int(row["null_count"] or 0),
        distinct_count=distinct_count,
        min_value=_sample_value(row.get("min_value"))[0],
        max_value=_sample_value(row.get("max_value"))[0],
        min_length=int(row["min_length"]) if row.get("min_length") is not None else None,
        max_length=int(row["max_length"]) if row.get("max_length") is not None else None,
        values=values,
        values_complete=values_complete,
    )


def _profile_value_expression(field: str, type_name: str) -> str:
    if type_name == "bit":
        return f"CONVERT(int, {field})"
    if type_name == "uniqueidentifier":
        return f"CONVERT(nvarchar(36), {field})"
    return field


def _quote_identifier(identifier: str) -> str:
    return f"[{identifier.replace(']', ']]')}]"


def _relationship_probe_query(pair: RelationshipPair, row_limit: int) -> str:
    source_table = (
        f"{_quote_identifier(pair.from_namespace)}.{_quote_identifier(pair.from_object)}"
    )
    target_table = f"{_quote_identifier(pair.to_namespace)}.{_quote_identifier(pair.to_object)}"
    source_field = _quote_identifier(pair.from_field)
    target_field = _quote_identifier(pair.to_field)
    return f"""
WITH source_rows AS (
    SELECT TOP ({row_limit}) {source_field} AS value
    FROM {source_table}
    WHERE {source_field} IS NOT NULL
),
target_rows AS (
    SELECT TOP ({row_limit}) {target_field} AS value
    FROM {target_table}
    WHERE {target_field} IS NOT NULL
),
source_values AS (
    SELECT DISTINCT value FROM source_rows
),
target_values AS (
    SELECT DISTINCT value FROM target_rows
)
SELECT
    (SELECT COUNT_BIG(1) FROM source_rows) AS source_non_null_count,
    (SELECT COUNT_BIG(1) FROM source_values) AS source_distinct_count,
    (SELECT COUNT_BIG(1) FROM target_rows) AS target_non_null_count,
    (SELECT COUNT_BIG(1) FROM target_values) AS target_distinct_count,
    (
        SELECT COUNT_BIG(1)
        FROM source_values
        INNER JOIN target_values ON target_values.value = source_values.value
    ) AS overlap_count
"""


def _format_data_type(row: dict[str, Any]) -> str:
    name = str(row["type_name"])
    max_length = int(row["max_length"])
    precision = int(row["precision"])
    scale = int(row["scale"])
    if name in {"varchar", "char", "varbinary", "binary"}:
        length = "max" if max_length == -1 else str(max_length)
        return f"{name}({length})"
    if name in {"nvarchar", "nchar"}:
        length = "max" if max_length == -1 else str(max_length // 2)
        return f"{name}({length})"
    if name in {"decimal", "numeric"}:
        return f"{name}({precision},{scale})"
    if name in {"datetime2", "datetimeoffset", "time"}:
        return f"{name}({scale})"
    return name
