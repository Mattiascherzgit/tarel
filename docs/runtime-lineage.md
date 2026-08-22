# Runtime lineage

TAREL has an experimental import boundary for immutable SQL, MongoDB, and federated DuckDB execution
evidence. It is separate from static workflow lineage: a succeeded or failed query attempt is not
represented as a reusable ETL definition.

The caller must sanitize the observation before import. TAREL accepts only:

- a run ID and exact persisted graph revision;
- ordered, unique call IDs;
- a logical source alias and, for SQL, a supported dialect (`duckdb`, `postgresql`, `sqlite`, or
  `sqlserver`);
- an explicit read-only `select`, `find`, or `aggregate` operation declaration;
- a SHA-256 of the statement or MongoDB request, never its text, filter, pipeline, or values;
- exact table, view, or field node IDs from the graph;
- for success, bounded column names, row count, deterministic result SHA-256, and optional
  truncation evidence;
- for failure, a safe error code rather than a database error message.

SQL events may also carry a non-negative caller-measured `duration_ms`. `row_count` describes the
bounded result represented by the hash; `truncated: true` says that the caller stopped before all
available rows were returned. TAREL does not infer a total row count.

A direct read-only query against a persistent DuckDB source is a `sql_query` with
`dialect: "duckdb"`, a logical source alias, and graph-bound inputs. It is deliberately different
from a `federated_query` with `engine: "duckdb"`: the latter is a temporary computation over
results from earlier source calls and therefore has `consumes` dependencies rather than a source
alias. Callers must not relabel either event to make it fit the other shape.

A `federated_query` event declares the DuckDB engine, a read-only `select`, a statement hash, and
one or more prior call IDs in `consumes`. Dependencies must occur earlier and must have status
`succeeded` or `accepted`; failed attempts can never become transformation inputs. Successful
transformations retain the same bounded result evidence as source SQL. An `accepted` status marks
the selected federated result without discarding other succeeded or failed attempts.

A `mongo_query` event declares `find` or `aggregate`, a logical source alias, a sanitized request
hash, and exact graph object or field inputs. Its success and failure evidence follows the same
bounded rules as SQL. A successful MongoDB call can be consumed by a later federated query and is
then included in `trace-runtime` origins.

Unknown fields fail closed. SQL text, MongoDB filters and pipelines, documents, raw rows,
connection URLs, credentials, timestamps, result values, and free-form errors are outside the
contract and are not persisted.

```bash
tarel lineage import-runtime local-run-001 \
  --source sanitized-runtime-input.json \
  --format json

tarel lineage show-runtime local-run-001 --format json
tarel lineage list-runtime
tarel lineage trace-runtime local-run-001 accepted-duckdb-call --format json
```

The input contract is `tarel.runtime-lineage-input.v0.1`; the stored contract is
`tarel.runtime-lineage.v0.1`. Imports are create-only and fail if the graph revision has changed or
an input node cannot be resolved exactly. Files live below `.tarel/runtime-lineage/` and are not
mixed into static lineage documents.

`duration_ms` and `truncated` are optional additions to the v0.1 event shapes. Existing v0.1
artifacts that omit them remain valid and round-trip without synthetic null fields.

`trace-runtime` follows explicit `consumes` edges backwards and returns every reached call plus the
exact graph-bound table and field origins. A failed call cannot be selected as an evidence trace
endpoint.

This slice records direct SQL attempts (including DuckDB), MongoDB attempts, and federated DuckDB
result dependencies. A Lab adapter that previously filtered direct SQL dialects must include
`duckdb`; it can then project those observations without marking the run partial merely because of
the dialect. TAREL still does not execute DuckDB or MongoDB queries.

Final answer claims and browser rendering remain explicit follow-up work rather than being
approximated with static workflow edges. The read-only browser currently has no runtime-lineage
projection; import, validation, storage, export, CLI display, and trace projection are covered by
this contract.
