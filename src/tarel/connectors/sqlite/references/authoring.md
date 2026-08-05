# SQLite connector evidence

## Official sources

- [Python `sqlite3`](https://docs.python.org/3/library/sqlite3.html)
- [SQLite PRAGMA statements](https://www.sqlite.org/pragma.html)
- [SQLite schema table](https://www.sqlite.org/schematab.html)

## Verified boundary

The connector uses Python's standard-library driver and opens an existing database file with
`mode=ro` plus `PRAGMA query_only = ON`. It discovers `sqlite_schema`, `PRAGMA table_xinfo`, and
`PRAGMA foreign_key_list`. The first version exposes only the `main` namespace.
