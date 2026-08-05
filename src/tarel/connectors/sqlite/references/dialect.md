# SQLite dialect notes

- Identifiers use double quotes and embedded quotes are doubled.
- Values and limits use `?` parameter placeholders.
- One database file is one catalog; the first connector exposes only `main`.
- Metadata comes from `sqlite_schema`, `table_xinfo`, and `foreign_key_list`.
- SQLite declared types are retained as observed rather than coerced into another type system.
