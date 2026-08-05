"""Read-only SQLite connector using Python's standard library."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote, urlsplit

from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogRequest,
    CatalogResult,
    ConnectorFailure,
    ConnectorManifest,
    ProbeRequest,
    ProbeResult,
    RelationshipPair,
    RelationshipPairProfile,
    RelationshipProbeRequest,
    RelationshipProbeResult,
    SampleRequest,
    SampleResult,
)

_MAX_SAMPLE_FIELDS = 32
_MAX_SAMPLE_VALUE_CHARS = 256
_MAX_SAMPLE_TOTAL_CHARS = 20_000


class SqliteConnector:
    def __init__(self, manifest: ConnectorManifest) -> None:
        self.manifest = manifest

    def probe(self, request: ProbeRequest) -> ProbeResult:
        path = _database_path(request.url)
        try:
            with closing(_connect_read_only(path)) as connection:
                connection.execute("SELECT 1").fetchone()
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error as exc:
            raise ConnectorFailure(
                "connection_failed",
                f"SQLite probe failed ({type(exc).__name__}).",
            ) from exc
        return ProbeResult(
            connector=self.manifest.name,
            source_type=self.manifest.source_type,
            server_name=None,
            database_name=request.database or path.stem,
            product_version=sqlite3.sqlite_version,
            product_level=f"user_version={user_version}",
            edition="Python stdlib sqlite3",
            capabilities=self.manifest.capabilities,
        )

    def discover_catalog(self, request: CatalogRequest) -> CatalogResult:
        _validate_namespace(request.namespace)
        path = _database_path(request.url)
        try:
            with closing(_connect_read_only(path)) as connection:
                rows = connection.execute(
                    """
                    SELECT name, type
                    FROM sqlite_schema
                    WHERE type IN ('table', 'view')
                      AND name NOT LIKE 'sqlite_%'
                    ORDER BY name, type
                    """
                ).fetchall()
                objects = tuple(_catalog_object(connection, row) for row in rows)
                relationships = _catalog_relationships(connection, rows)
        except ConnectorFailure:
            raise
        except sqlite3.Error as exc:
            raise ConnectorFailure(
                "discovery_failed",
                f"SQLite catalog discovery failed ({type(exc).__name__}).",
            ) from exc
        return CatalogResult(
            connector=self.manifest.name,
            source_type=self.manifest.source_type,
            catalog=request.database or path.stem,
            dialect=self.manifest.dialect,
            objects=objects,
            relationships=relationships,
        )

    def sample_rows(self, request: SampleRequest) -> SampleResult:
        if not 1 <= request.limit <= 10:
            raise ConnectorFailure("invalid_sample_limit", "Sample limit must be between 1 and 10.")
        _validate_namespace(request.namespace)
        path = _database_path(request.url)
        try:
            with closing(_connect_read_only(path)) as connection:
                _require_object(connection, request.object_name)
                fields = _table_fields(connection, request.object_name)
                selected = tuple(
                    str(row["name"])
                    for row in fields
                    if "BLOB" not in str(row["type"] or "").upper()
                )[:_MAX_SAMPLE_FIELDS]
                omitted = tuple(str(row["name"]) for row in fields if row["name"] not in selected)
                if not selected:
                    raise ConnectorFailure(
                        "no_sample_fields",
                        f"No bounded sample fields are available for main.{request.object_name}.",
                    )
                primary_keys = tuple(
                    str(row["name"])
                    for row in sorted(fields, key=lambda item: int(item["pk"]) or 9999)
                    if int(row["pk"]) > 0 and row["name"] in selected
                )
                ordered_by = primary_keys or selected[:1]
                query = (
                    f"SELECT {', '.join(_quote(name) for name in selected)} "
                    f"FROM {_quote(request.object_name)} "
                    f"ORDER BY {', '.join(_quote(name) for name in ordered_by)} LIMIT ?"
                )
                raw_rows = connection.execute(query, (request.limit,)).fetchall()
        except ConnectorFailure:
            raise
        except sqlite3.Error as exc:
            raise ConnectorFailure(
                "sampling_failed",
                f"SQLite sampling failed for main.{request.object_name} "
                f"({type(exc).__name__}).",
            ) from exc
        rows, truncated = _bounded_rows(raw_rows, selected)
        return SampleResult(
            connector=self.manifest.name,
            catalog=request.database or path.stem,
            namespace="main",
            object_name=request.object_name,
            selected_fields=selected,
            omitted_fields=omitted,
            ordered_by=ordered_by,
            rows=rows,
            truncated_values=truncated,
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
        path = _database_path(request.url)
        profiles: list[RelationshipPairProfile] = []
        try:
            with closing(_connect_read_only(path)) as connection:
                for pair in request.pairs:
                    profiles.append(_profile_pair(connection, pair, request.row_limit))
        except ConnectorFailure:
            raise
        except sqlite3.Error as exc:
            raise ConnectorFailure(
                "relationship_probe_failed",
                f"SQLite relationship probe failed ({type(exc).__name__}).",
            ) from exc
        return RelationshipProbeResult(
            connector=self.manifest.name,
            catalog=request.database or path.stem,
            profiles=tuple(profiles),
        )


def create_connector(manifest: ConnectorManifest) -> SqliteConnector:
    return SqliteConnector(manifest)


def _database_path(url: str) -> Path:
    parsed = urlsplit(url)
    if parsed.scheme != "sqlite" or parsed.netloc or parsed.query or parsed.fragment:
        raise ConnectorFailure(
            "unsupported_url",
            "SQLite requires sqlite:///relative.db or sqlite:////absolute.db.",
        )
    if url.startswith("sqlite:////"):
        path = Path(unquote(parsed.path))
    elif url.startswith("sqlite:///"):
        path = Path(unquote(parsed.path.lstrip("/")))
    else:
        raise ConnectorFailure(
            "unsupported_url",
            "SQLite requires sqlite:///relative.db or sqlite:////absolute.db.",
        )
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ConnectorFailure("database_not_found", "SQLite database file does not exist.")
    return resolved


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _validate_namespace(namespace: str | None) -> None:
    if namespace not in {None, "main"}:
        raise ConnectorFailure(
            "namespace_not_found",
            f"SQLite connector exposes only the main namespace, not {namespace}.",
        )


def _catalog_object(connection: sqlite3.Connection, row: sqlite3.Row) -> CatalogObject:
    name = str(row["name"])
    fields = _table_fields(connection, name)
    primary_key_rows = sorted(
        (field for field in fields if int(field["pk"]) > 0),
        key=lambda field: int(field["pk"]),
    )
    primary_key = tuple(str(field["name"]) for field in primary_key_rows)
    return CatalogObject(
        namespace="main",
        name=name,
        kind=str(row["type"]),
        fields=tuple(
            CatalogField(
                name=str(field["name"]),
                position=int(field["cid"]) + 1,
                data_type=str(field["type"] or "BLOB"),
                nullable=not bool(field["notnull"]) and not bool(field["pk"]),
                is_primary_key=bool(field["pk"]),
            )
            for field in fields
        ),
        primary_key=primary_key,
    )


def _catalog_relationships(
    connection: sqlite3.Connection,
    objects: list[sqlite3.Row],
) -> tuple[CatalogRelationship, ...]:
    grouped: dict[tuple[str, int, str], list[sqlite3.Row]] = {}
    for obj in objects:
        if obj["type"] != "table":
            continue
        source = str(obj["name"])
        for row in connection.execute(f"PRAGMA foreign_key_list({_quote(source)})"):
            grouped.setdefault((source, int(row["id"]), str(row["table"])), []).append(row)
    relationships: list[CatalogRelationship] = []
    for (source, _identifier, target), rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: int(row["seq"]))
        from_fields = tuple(str(row["from"]) for row in ordered)
        to_fields = tuple(str(row["to"]) for row in ordered)
        relationships.append(
            CatalogRelationship(
                name=(
                    f"fk_{source}__{'_'.join(from_fields)}__"
                    f"{target}__{'_'.join(to_fields)}"
                ),
                from_namespace="main",
                from_object=source,
                from_fields=from_fields,
                to_namespace="main",
                to_object=target,
                to_fields=to_fields,
            )
        )
    return tuple(relationships)


def _table_fields(connection: sqlite3.Connection, object_name: str) -> list[sqlite3.Row]:
    return list(connection.execute(f"PRAGMA table_xinfo({_quote(object_name)})"))


def _require_object(connection: sqlite3.Connection, object_name: str) -> None:
    row = connection.execute(
        "SELECT 1 FROM sqlite_schema WHERE name = ? AND type IN ('table', 'view')",
        (object_name,),
    ).fetchone()
    if row is None or object_name.startswith("sqlite_"):
        raise ConnectorFailure("object_not_found", f"SQLite object not found: {object_name}")


def _require_field(connection: sqlite3.Connection, object_name: str, field_name: str) -> None:
    _require_object(connection, object_name)
    if field_name not in {str(row["name"]) for row in _table_fields(connection, object_name)}:
        raise ConnectorFailure(
            "field_not_found",
            f"SQLite field not found: main.{object_name}.{field_name}",
        )


def _profile_pair(
    connection: sqlite3.Connection,
    pair: RelationshipPair,
    row_limit: int,
) -> RelationshipPairProfile:
    _validate_namespace(pair.from_namespace)
    _validate_namespace(pair.to_namespace)
    _require_field(connection, pair.from_object, pair.from_field)
    _require_field(connection, pair.to_object, pair.to_field)
    source_values = _profile_values(connection, pair.from_object, pair.from_field, row_limit)
    target_values = _profile_values(connection, pair.to_object, pair.to_field, row_limit)
    source_distinct = set(source_values)
    target_distinct = set(target_values)
    return RelationshipPairProfile(
        pair=pair,
        source_non_null_count=len(source_values),
        source_distinct_count=len(source_distinct),
        target_non_null_count=len(target_values),
        target_distinct_count=len(target_distinct),
        overlap_count=len(source_distinct & target_distinct),
        profile_row_limit=row_limit,
    )


def _profile_values(
    connection: sqlite3.Connection,
    object_name: str,
    field_name: str,
    row_limit: int,
) -> list[object]:
    query = (
        f"SELECT {_quote(field_name)} FROM {_quote(object_name)} "
        f"WHERE {_quote(field_name)} IS NOT NULL LIMIT ?"
    )
    return [row[0] for row in connection.execute(query, (row_limit,))]


def _bounded_rows(
    rows: list[sqlite3.Row],
    selected_fields: tuple[str, ...],
) -> tuple[tuple[dict[str, object], ...], bool]:
    result: list[dict[str, object]] = []
    used_chars = 0
    truncated = False
    for row in rows:
        converted: dict[str, object] = {}
        for field_name in selected_fields:
            value = row[field_name]
            if value is None or isinstance(value, bool | int | float):
                safe_value: object = value
            else:
                text = str(value)
                safe_value = text[:_MAX_SAMPLE_VALUE_CHARS]
                truncated = truncated or len(text) > _MAX_SAMPLE_VALUE_CHARS
            converted[field_name] = safe_value
            used_chars += len(str(safe_value))
        if used_chars > _MAX_SAMPLE_TOTAL_CHARS:
            truncated = True
            break
        result.append(converted)
    return tuple(result), truncated


def _quote(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'
