# Retail DWH demo

The `retail-dwh` demo is a small deterministic analytics warehouse used to exercise TAREL through
the same public connector and graph paths as an external source. It uses Python's standard-library
SQLite driver, contains no credentials or personal data, and is created only on explicit request.

## Build and inspect version 1

```bash
tarel demo create retail-dwh
tarel source configure retail-local \
  --connector sqlite \
  --config-ref state:demos/retail-dwh.toml \
  --namespace main
tarel source check retail-local
tarel source probe retail-local
tarel source discover retail-local
tarel connector sample sqlite \
  --config .tarel/demos/retail-dwh.toml \
  --schema main \
  --object F_SLS_01 \
  --limit 3
tarel source build retail-local retail-demo
```

The graph contains date, product, customer, geography, reseller, currency, and channel dimensions;
two sales facts; one return fact; a bridge-like mapping table; and a union view. `F_SLS_01` and
`F_SLS_02` are deliberately abbreviated. Channel evidence exists in `CHNL_CD`, while the plausible
`F_SLS_02.RSLR_KEY -> D_RSLR.RSLR_KEY` relationship has no declared foreign key.

No semantic annotations are pre-approved. Use either the current coding agent or an optional
provider, then review the resulting drafts:

```bash
tarel annotation next retail-demo \
  --samples 5 \
  --config .tarel/demos/retail-dwh.toml
tarel annotation apply retail-demo --input proposal.json
tarel annotation validate retail-demo main.F_SLS_01 \
  --include-fields \
  --reason "Reviewed against the demo schema and bounded samples."
```

Probe and persist the intentionally missing relationship candidate:

```bash
tarel relationship discover retail-demo \
  --object main.F_SLS_02 \
  --field RSLR_KEY \
  --config .tarel/demos/retail-dwh.toml
tarel relationship list retail-demo
```

The candidate remains a draft until a human validates its stable edge ID.

## Exercise search and context

After annotation, useful questions include:

```bash
tarel search retail-demo "internet and reseller sales by year" --mode bm25
tarel context retail-demo "internet and reseller sales by year" --mode bm25
tarel context retail-demo "returns by product and sales channel" --mode bm25
tarel grounding retail-demo "internet and reseller sales by year" \
  --source retail-local --mode bm25
```

SQLite stores the demo's metric rows, but TAREL still emits metadata context rather than executing
an analytical answer query.

## Reproduce schema drift

Save a version-1 context packet, replace only the local demo source with version 2, and refresh:

```bash
tarel context retail-demo "internet and reseller sales by year" \
  --mode bm25 \
  --format json > retail-context-v1.json
tarel demo create retail-dwh --version 2 --force
tarel graph refresh retail-demo --config .tarel/demos/retail-dwh.toml
tarel context impact retail-context-v1.json --graph retail-demo
```

Version 2 changes selected field types and keys, adds and removes fields, renames a reseller field,
and removes one declared relationship. Change Radar preserves affected semantic knowledge, moves
validated claims to `review_required`, and archives removed claims in its revision-bound report.

`--force` is required because creating version 2 replaces the local SQLite file and its generated
configuration. Both stay below `.tarel/` and are excluded from Git and distributions.
