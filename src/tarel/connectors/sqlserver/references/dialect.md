# T-SQL dialect notes

## Official source

- [Transact-SQL syntax conventions](https://learn.microsoft.com/en-us/sql/t-sql/language-elements/transact-sql-syntax-conventions-transact-sql?view=sql-server-ver17)

## Connector behavior

- Manifest dialect identifier: `tsql`.
- Metadata queries use SQL Server `sys.*` catalog views.
- Runtime values use the `pymssql` `%s` parameter binding convention.
- No user-provided identifier is interpolated into discovery SQL.
- Catalog, namespace, object, and field correspond to database, schema, table/view, and column.

## Type rendering

- Character and binary lengths are retained.
- `nvarchar` and `nchar` byte lengths are converted to character lengths.
- Decimal precision and scale are retained.
- Temporal scale is retained for `datetime2`, `datetimeoffset`, and `time`.
