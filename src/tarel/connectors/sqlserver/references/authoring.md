# SQL Server connector evidence

## Official sources

- [Object catalog views](https://learn.microsoft.com/en-us/sql/relational-databases/system-catalog-views/object-catalog-views-transact-sql?view=sql-server-ver17)
- [Metadata visibility configuration](https://learn.microsoft.com/en-us/sql/relational-databases/security/metadata-visibility-configuration?view=sql-server-ver17)

## Implemented observations

- `probe` reads server and current-database identity with `SERVERPROPERTY` and `DB_NAME`.
- `discover_catalog` joins `sys.objects`, `sys.schemas`, `sys.columns`, and `sys.types`.
- Discovery includes user tables and views and excludes objects marked `is_ms_shipped`.
- Discovery reads primary keys, foreign keys, and optional object/field `MS_Description` values.
- Results are ordered by namespace, object, kind, and field position.
- `sample_rows` is a separate opt-in capability with bounded rows, fields, and value sizes.
- `probe_relationships` compares at most 50 selected field pairs over bounded row inputs and
  returns aggregate counts only.

## Access behavior

- All capabilities execute `SELECT` statements only.
- An optional namespace filter is passed as a driver parameter.
- Sample identifiers are resolved from catalog metadata and quoted as T-SQL identifiers.
- Relationship probes reuse one connection and never return overlapping field values.
- Visible metadata depends on the permissions of the configured SQL Server principal.

## Not implemented

- Unique constraints beyond primary keys, global relationship scans, and durable profile caches.
- Stored procedures, jobs, and operational lineage.
