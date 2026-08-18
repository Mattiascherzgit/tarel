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
  --namespace main \
  --allow-aggregates \
  --allow-small-domains \
  --allow-raw-samples
tarel source check retail-local
tarel source probe retail-local
tarel source discover retail-local
tarel connector sample sqlite \
  --config .tarel/demos/retail-dwh.toml \
  --schema main \
  --object F_SLS_01 \
  --limit 3

# Aggregate profiles do not include small-domain values by default.
tarel connector profile sqlite \
  --config .tarel/demos/retail-dwh.toml \
  --schema main \
  --object D_CHNL \
  --row-limit 10000

# Explicitly allow observed values for complete small domains in this command result.
tarel connector profile sqlite \
  --config .tarel/demos/retail-dwh.toml \
  --schema main \
  --object D_CHNL \
  --row-limit 10000 \
  --include-values

tarel source build retail-local retail-demo
tarel source enrich retail-local retail-demo --format json
```

Profiles report bounded row coverage, null and distinct counts, min/max values, and text lengths.
Unsupported columns remain visible as omissions. Profile output and raw table previews are ephemeral:
TAREL does not copy them into the graph, retrieval index, or context packets. The separate
`connector sample` command remains an explicit, read-permission-controlled preview and accepts at
most ten rows.

The source permissions are deny-by-default and independent for each logical source:

- `aggregates` permits bounded column profiles with null/distinct counts, min/max, and lengths;
- `small_domains` additionally permits complete value counts for small domains and therefore
  requires `aggregates`;
- `raw_samples` permits at most ten raw rows per table in the command result.

`source enrich` walks every table and view in the bound graph. Its JSON result is an ephemeral
workfile containing the allowed profiles and, only with `raw_samples` permission, up to ten rows
per object. Raw rows are never copied into the graph, retrieval index, context packet, or browser
payload. Individual object failures remain visible in the workfile while other objects continue.

When sampled strings repeat a fixed pattern such as `KST102020KTO102000`, the workfile reports its
coverage and fixed digit segments. The first conservative thresholds require at least three
matching keys, 80% pattern coverage, two overlapping distinct values, 60% sampled source coverage,
and 90% sampled target uniqueness. These numeric thresholds are necessary but not sufficient:

- the source must be a textual key-like field or a clear multi-prefix composite key;
- temporal and ordinary free-text shapes are excluded;
- the literal cue immediately before a digit segment must match a token or acronym in the target
  object or field name;
- at most one ranked target survives for each source segment.

Use `--persist-join-candidates` to write only aggregated overlap metrics and the zero-based segment
transform into draft relationship candidates:

```bash
tarel source enrich retail-local retail-demo \
  --persist-join-candidates \
  --format json
tarel relationship list retail-demo
```

Zero candidates is a normal successful outcome: pattern hints stay in the ephemeral workfile when
the semantic target cue is insufficient. Persisted candidates are not usable by context expansion
until a human validates them. No raw key or sample value is persisted with the candidate.

The graph contains date, product, customer, geography, reseller, currency, and channel dimensions;
two sales facts; one return fact; a bridge-like mapping table; and a union view. `F_SLS_01` and
`F_SLS_02` are deliberately abbreviated. Channel evidence exists in `CHNL_CD`, while the plausible
`F_SLS_02.RSLR_KEY -> D_RSLR.RSLR_KEY` relationship has no declared foreign key.

No semantic annotations are pre-approved. Use either the current coding agent or an optional
provider, then review the resulting drafts:

```bash
tarel annotation next retail-demo \
  --samples 5 \
  --profile-rows 10000 \
  --config .tarel/demos/retail-dwh.toml
tarel annotation apply retail-demo --input proposal.json
tarel annotation validate retail-demo main.F_SLS_01 \
  --include-fields \
  --reason "Reviewed against the demo schema and bounded samples."
```

Profiles include bounded min/max observations. Add `--include-small-domain-values` only when the
coding-agent task may additionally receive complete small-domain values. TAREL treats every
observed value as protected annotation input and rejects a provider response that repeats it.

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
