# Runtime lineage

TAREL has an experimental import boundary for immutable SQL, MongoDB, and federated DuckDB execution
evidence. It is separate from static workflow lineage: a succeeded or failed query attempt is not
represented as a reusable ETL definition.

The caller must sanitize the observation before import. TAREL accepts only:

- a run ID and exact persisted graph revision;
- ordered, unique call IDs;
- a logical source alias and, for SQL, a supported dialect;
- an explicit read-only `select`, `find`, or `aggregate` operation declaration;
- a SHA-256 of the statement or MongoDB request, never its text, filter, pipeline, or values;
- exact table, view, or field node IDs from the graph;
- for success, bounded column names, row count, and deterministic result SHA-256;
- for failure, a safe error code rather than a database error message.

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

`trace-runtime` follows explicit `consumes` edges backwards and returns every reached call plus the
exact graph-bound table and field origins. A failed call cannot be selected as an evidence trace
endpoint.

This slice records SQL and MongoDB attempts plus DuckDB result dependencies. Final answer claims
and browser rendering remain explicit follow-up work rather than being approximated with static
workflow edges.
