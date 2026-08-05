# TAREL

[![CI](https://github.com/Mattiascherzgit/tarel/actions/workflows/ci.yml/badge.svg)](https://github.com/Mattiascherzgit/tarel/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-2ea44f.svg)](LICENSE)

**Give coding agents the right analytics context -- and let them extend the tool when the source is
new.**

TAREL is a local-first context compiler for data warehouses, BI systems, and ERP schemas. It turns
technical metadata, bounded evidence, semantic annotations, and relationships into compact context
that an agent can actually use.

```text
known source -> technical graph -> reviewed meaning -> retrieval -> agent context
new source   -> agent-built connector candidate -> human gate -> same pipeline
```

TAREL stands for **Topology, Annotation, Retrieval, Evidence & Lineage**. It is a dependency-light
Python CLI with no mandatory runtime dependency. Use it directly from Codex, Claude Code, Pi, or
another coding agent; later, embed the same application core into your own software.

TAREL follows a self-modifying-software idea with a deliberate safety boundary. When it encounters
an unsupported source, the coding agent operating TAREL can create an isolated connector candidate,
research the official metadata APIs and dialect, implement the adapter, and test it locally. A
human reviews the code and observed results before activation. The kernel stays small while the
installation can grow toward the systems it actually encounters.

TAREL is deliberately not another catalog UI, chat application, SQL execution engine, or mandatory
graph server. It is the small layer between a complex analytical estate and an agent's limited
context window.

> TAREL is pre-alpha software. The CLI and serialized contracts may still change before `0.0.1`.
> A stable public SDK, operational lineage, and semantic-standard interoperability are not part of
> the current release.

## The problem it solves

An agent can inspect a small, well-named schema directly. Real analytical estates are different:
hundreds of tables, abbreviated names, missing foreign keys, undocumented measures, several source
systems, and knowledge that exists only in people's heads.

Dumping an entire schema into the prompt wastes context and still leaves the agent guessing. TAREL
instead builds a reusable map of the estate and compiles only the relevant part for each question:

1. A connector observes schemas, tables, views, fields, keys, descriptions, and bounded samples.
2. TAREL builds a deterministic technical graph with stable identities.
3. A coding agent or optional LLM provider proposes semantic annotations.
4. A human validates, edits, rejects, or defers those proposals.
5. Search selects useful graph objects and expands through trusted relationships.
6. The context compiler emits only the relevant objects, fields, joins, warnings, and provenance.

The result is small enough for an agent, explicit enough for a human to review, and deterministic
enough for prompt caching. The source database remains authoritative: TAREL stores metadata and
claims, not a copy of the warehouse.

## Design principles

- **Self-extending, human-gated.** A coding agent can build missing source adapters locally from
  explicit contracts; generated code remains isolated and inactive until reviewed.
- **Local first.** Graphs, indexes, reviews, and context packets work without a hosted service.
- **Evidence before confidence.** Technical observations, generated proposals, and human-validated
  knowledge remain distinguishable.
- **Human-reviewed semantics.** Generated descriptions and inferred joins begin as proposals, never
  as silent truth.
- **Small, stable context.** Deterministic ordering, explicit budgets, hashes, and visible omissions
  make output inspectable and cache-friendly.
- **Optional complexity.** The core uses only the Python standard library. Database drivers, local
  embeddings, and remote LLM calls are opt-in.
- **Agent-native, provider-neutral.** The agent already operating the CLI can research and extend
  TAREL. An optional provider handles repeatable parallel annotation jobs.

## Start here

If you are evaluating TAREL, follow the [five-minute local demo](#five-minute-local-demo). It needs
no credentials and exercises discovery, graph construction, annotation exchange, relationship
detection, retrieval, context compilation, and schema-drift reporting.

If you already have a source:

- read [Teach TAREL a new source](#teach-tarel-a-new-source) when no connector exists;
- read [Connector runtime](#connector-runtime) for the current built-in adapters;
- read [Semantic annotation and review](#semantic-annotation-and-review) for meaning and approval;
- read [Retrieval modes](#retrieval-modes) and
  [Deterministic context packets](#deterministic-context-packets) for agent integration.

## Implemented features

| Area | Current capability |
|---|---|
| Self-extension | Agent-readable connector tasks, isolated candidates, versioned manifests, dialect references, and a human activation gate |
| Connector runtime | Stable read-only contracts for probing, metadata discovery, sampling, and bounded relationship evidence |
| Discovery | Catalog, namespace, table, view, field, type, nullability, primary key, foreign key, and technical description observations |
| Sampling | Explicit, deterministic samples of 1–10 rows with field, value-size, and total-size limits |
| Graph | Stable technical node and edge identities; atomic local JSON persistence; deterministic revisions |
| Annotation | Provider-free coding-agent tasks and optional OpenRouter-backed parallel batches |
| Human review | Draft, validated, rejected, deferred, and `review_required` states with preserved originals and review reasons |
| Relationships | Declared foreign keys, human-defined joins, and bounded aggregate discovery of missing relationship candidates |
| Retrieval | Deterministic lexical search, dependency-free BM25, optional local vector search, and hybrid reciprocal-rank fusion |
| Context | Bounded object, field, join, and hop selection with visible paths, reasons, warnings, and omissions |
| Cache-friendly output | Stable and dynamic packet sections, graph revision, canonical hashes, packet diffing, and refresh impact checks |
| Workspaces | `system → area → schema` hierarchy plus explicit overlapping zones across schemas |
| Change Radar | Field, key, object, and relationship drift; possible renames; stale claims; affected areas, zones, and context packets |
| Demo | Deterministic local Retail DWH with a deliberate missing relationship and reproducible V1→V2 schema drift |

The runtime core has no mandatory third-party dependency. SQLite uses Python's standard library.
SQL Server and local embeddings are optional extras.

## Installation

TAREL has not been released to PyPI yet. To use it directly from source:

```bash
git clone https://github.com/Mattiascherzgit/tarel.git
cd tarel
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
tarel --version
```

This installs the dependency-free core. The current supported target is Python 3.11 or 3.12.
Contributors can use `python -m pip install -e '.[dev]'` instead.

Optional capabilities:

```bash
# SQL Server through pymssql
python -m pip install '.[sqlserver]'

# Local CPU embeddings through llama.cpp
python -m pip install '.[local-rag]'
```

## Five-minute local demo

The bundled Retail DWH is the safest way to try TAREL. It contains synthetic analytical data,
requires no credentials, and is created only when requested.

### 1. Create and probe the source

```bash
tarel demo create retail-dwh
tarel connector check sqlite
tarel connector probe sqlite --config .tarel/demos/retail-dwh.toml
```

The generated SQLite database and configuration live below the ignored `.tarel/` directory.

Inspect the catalog or a bounded sample:

```bash
tarel connector discover sqlite \
  --config .tarel/demos/retail-dwh.toml \
  --schema main

tarel connector sample sqlite \
  --config .tarel/demos/retail-dwh.toml \
  --schema main \
  --object F_SLS_01 \
  --limit 3
```

### 2. Build the technical graph

```bash
tarel graph build retail-demo \
  --connector sqlite \
  --config .tarel/demos/retail-dwh.toml \
  --schema main

tarel graph show retail-demo
```

The demo graph contains date, product, customer, geography, reseller, currency, and channel
dimensions; two sales facts; a return fact; a mapping table; and a union view. Its fact names are
deliberately abbreviated so that semantic annotation provides measurable value.

### 3. Annotate with the current coding agent

TAREL can work without an API provider. Ask for one complete, structured task:

```bash
tarel annotation next retail-demo \
  --samples 5 \
  --config .tarel/demos/retail-dwh.toml > annotation-task.json
```

Give `annotation-task.json` to the coding agent already running TAREL. Save its structured response
as `proposal.json`, then apply it as a draft:

```bash
tarel annotation apply retail-demo --input proposal.json
tarel annotation show retail-demo main.D_CHNL
tarel annotation validate retail-demo main.D_CHNL \
  --include-fields \
  --reason "Reviewed against the demo schema and bounded samples."
```

Every proposal must cover the selected object and all supplied fields. Samples are input-only:
they are not persisted in the graph and must not be repeated in generated descriptions.

### 4. Discover the deliberately missing relationship

`F_SLS_02.RSLR_KEY` plausibly joins `D_RSLR.RSLR_KEY`, but the demo intentionally declares no
foreign key. TAREL can test a small set of type-compatible candidates with bounded SQL aggregates:

```bash
tarel relationship discover retail-demo \
  --object main.F_SLS_02 \
  --field RSLR_KEY \
  --config .tarel/demos/retail-dwh.toml

tarel relationship list retail-demo
```

Discovery stores counts and ratios, not the probed values. The result remains a draft and is not
used for graph expansion until a human validates its edge ID:

```bash
tarel relationship validate retail-demo RELATIONSHIP_ID \
  --reason "The domain overlap and target uniqueness were reviewed."
```

### 5. Search and compile agent context

Dependency-free BM25 is useful immediately after annotation:

```bash
tarel search retail-demo \
  "internet and reseller sales by year" \
  --mode bm25 \
  --limit 10

tarel context retail-demo \
  "internet and reseller sales by year" \
  --mode bm25 \
  --max-objects 10
```

The context result contains graph metadata and proposed joins needed to answer the question. It
does not execute the analytical aggregation itself.

Conceptually, the agent receives a bounded packet like this:

```text
STABLE CONTEXT
graph: retail-demo
objects:
  main.F_SLS_01  "Internet sales fact"                 [draft]
  main.F_SLS_02  "Reseller sales fact"                 [draft]
  main.D_DATE    "Calendar dimension"                  [validated]
joins:
  F_SLS_01.DOC_DT_KEY -> D_DATE.DATE_KEY                [foreign_key]
  F_SLS_02.RSLR_KEY -> D_RSLR.RSLR_KEY                 [candidate, draft]

DYNAMIC REQUEST
query: internet and reseller sales by year
selection: ...
omissions: ...
```

This is illustrative rather than copied output; the real text and JSON renderers include stable
identities, review states, paths, retrieval reasons, budgets, warnings, hashes, and visible
omissions. Unreviewed proposals may be included, but their state is never hidden.

### 6. Reproduce schema drift

Save the first packet, replace the local demo source with V2, refresh the graph, and check impact:

```bash
tarel context retail-demo \
  "internet and reseller sales by year" \
  --mode bm25 \
  --format json > retail-context-v1.json

tarel demo create retail-dwh --version 2 --force
tarel graph refresh retail-demo \
  --config .tarel/demos/retail-dwh.toml

tarel context impact retail-context-v1.json --graph retail-demo
```

V2 changes selected types and keys, adds and removes fields, renames a reseller field, and removes
one declared relationship. Change Radar preserves affected claims, moves validated annotations to
`review_required`, retains removed knowledge as stale claims, and identifies affected workspace
areas, zones, and context packets.

See the complete [Retail DWH walkthrough](docs/retail-demo.md).

## Teach TAREL a new source

The long-term value of TAREL is not a large built-in connector inventory. It is a small connector
contract that a capable coding agent can implement when it meets a new database or warehouse. The
same authoring pattern is intended for lakes, document systems, and orchestration platforms as
their contracts are added.

Start by creating an isolated candidate:

```bash
tarel connector scaffold postgres \
  --output .tarel/connectors/postgres-candidate
```

Then give the coding agent already operating TAREL a concrete instruction:

> Implement the connector described in
> `.tarel/connectors/postgres-candidate/CONNECTOR_TASK.md`. Use official vendor documentation,
> keep the driver optional, implement the read-only probe first, record only the required dialect
> and metadata notes under `references/`, and stop after local tests for human review.

The generated workspace gives the agent:

- an explicit versioned connector contract and capability boundary;
- an inactive Python adapter with the required entry point;
- a read-only manifest for dependencies, permissions, dialect, and references;
- focused authoring and SQL-dialect reference files;
- rules for secret handling, bounded probes, stable ordering, and visible failures;
- a completion gate that requires testing against a private source and human review.

The agent may consult current official documentation when a proprietary metadata API or SQL dialect
is unknown. It can then edit the candidate, install only the source-specific driver, and iterate on
`probe` and `discover_catalog` using real error output. This is the self-modifying loop: the tool
provides the contract and evidence boundary; the coding agent supplies the source-specific code.

Today, `scaffold` creates the complete authoring workspace but does not invoke an LLM by itself,
register the candidate, or execute generated code. Integration and activation are explicit human
decisions. That gate is intentional: self-extension should remove repetitive integration work,
not turn unreviewed generated code into trusted infrastructure.

## Connector runtime

Once approved and integrated, every connector uses the same small CLI surface:

```bash
tarel connector check NAME
tarel connector probe NAME --config private.toml
tarel connector discover NAME --config private.toml --schema SCHEMA
tarel connector sample NAME --config private.toml --schema SCHEMA --object OBJECT
```

Connectors are read-only adapters. They produce normalized observations and never mutate a TAREL
graph directly. The current distribution includes SQLite and SQL Server as working references for
the contract, not as the intended limit of the system.

Private connector files use TOML sections matching the connector name. They must remain outside
Git. Environment variables such as `TAREL_SQLSERVER_URL` can override local URLs.

## Semantic annotation and review

Two execution modes use the same annotation contract:

- **Coding-agent mode:** `annotation plan`, `next`, and `apply` exchange JSON with the agent already
  operating the CLI.
- **Provider mode:** `graph annotate` calls an optional provider once per object and can run several
  independent calls in parallel.

Configure OpenRouter without writing a key into the repository:

```bash
export OPENROUTER_API_KEY='...'
tarel provider configure openrouter --from-env --model MODEL_NAME
tarel provider test openrouter
```

Run a bounded batch:

```bash
tarel graph annotate retail-demo \
  --provider openrouter \
  --workers 4 \
  --retry 1 \
  --samples 5 \
  --config .tarel/demos/retail-dwh.toml
```

Using `--samples` with a remote provider is an explicit data-boundary decision and is announced on
stderr. Without it, only technical metadata and graph relationships are sent.

Review commands never silently turn generated meaning into trusted meaning:

```bash
tarel annotation list retail-demo --state draft
tarel annotation show retail-demo main.F_SLS_01.NET_AMT
tarel annotation edit retail-demo main.F_SLS_01.NET_AMT \
  --input annotation-patch.json \
  --reason "Definition supplied by the data owner"
tarel annotation validate retail-demo main.F_SLS_01.NET_AMT \
  --reason "Definition and currency behavior confirmed"
tarel annotation defer retail-demo main.F_SLS_01 \
  --reason "Waiting for the data owner"
tarel annotation reject retail-demo main.F_SLS_01 \
  --reason "Proposed meaning is incorrect"
```

Human edits preserve the original proposal. Rejected claims are hidden from retrieval while the
technical schema stays available.

## Retrieval modes

`search` and `context` support four retrieval modes:

- `lexical`: deterministic name and annotation matching;
- `bm25`: dependency-free ranked retrieval over safe graph documents;
- `vector`: local embeddings from a current persisted index;
- `hybrid`: BM25 and vector results combined with reciprocal-rank fusion.

For local vector and hybrid retrieval:

```bash
python -m pip install -e '.[local-rag]'
tarel model download
tarel model status
tarel index build retail-demo
tarel search retail-demo "annual online revenue" --mode hybrid
```

The embedding model is downloaded only by the explicit command, checksum-verified, and stored in
the user cache. Models and vector indexes are not included in the repository or package. TAREL
embeds an allowlist of graph metadata; connection information, sample values, and arbitrary
provenance are excluded. See [Local retrieval](docs/local-retrieval.md).

## Deterministic context packets

`tarel.context.v0.2` separates a reusable stable prefix from question-specific dynamic content.
The packet includes:

- graph name and deterministic revision;
- selected annotation states and scope;
- ordered objects, fields, joins, and validated relationship candidates;
- selection paths, retrieval reasons, warnings, and visible omissions;
- stable, dynamic, and complete packet hashes;
- an explicit character budget without timestamps, runtimes, or local paths.

Compare two packets or test whether a source refresh invalidated an older packet:

```bash
tarel context diff packet-a.json packet-b.json
tarel context impact packet-a.json --graph retail-demo
```

TAREL intentionally emits neutral packet structure rather than provider-specific cache headers,
TTLs, or session controls. A consuming agent or application can map the stable and dynamic sections
to its own caching mechanism. See the [context packet contract](docs/context-contract.md).

## Workspaces, areas, and zones

Graphs remain independent source projections. A workspace organizes them without copying or
rewriting their nodes:

```text
workspace
└── system
    └── area
        └── schema
```

Zones are explicit overlapping sets of tables and views. They may cross schemas and areas within
one system.

```bash
tarel workspace create enterprise
tarel workspace system define enterprise commercial --graph retail-demo
tarel workspace area define enterprise commercial sales \
  --schema retail-demo:main
tarel workspace zone define enterprise commercial revenue \
  --object retail-demo:main.F_SLS_01 \
  --object retail-demo:main.F_SLS_02 \
  --object retail-demo:main.D_DATE
tarel workspace zone show enterprise commercial revenue
```

Change Radar reports which areas and zones are affected by a graph refresh without rewriting their
definitions automatically. See [Workspaces, systems, areas, and zones](docs/workspaces.md).

## Local persistence and data boundaries

TAREL is file-first. Runtime state is stored below the working directory:

```text
.tarel/
├── demos/
├── graphs/
│   └── GRAPH/
│       ├── graph.json
│       └── changes/
├── indexes/
└── workspaces/
```

`.tarel/` is ignored by Git. Graph and workspace stores have explicit whole-document boundaries so
that a later shared database implementation can use the same application use cases.

Security defaults:

- connector manifests currently accept read permission only;
- normal CLI errors do not print connection URLs or passwords;
- provider keys are stored outside the repository with user-only file permissions;
- samples require an explicit command or positive sample limit;
- sample blocks are never persisted in graph annotations;
- inferred relationships are not traversed until human validation;
- model downloads and remote provider calls never happen during import.

## Command overview

```text
tarel demo          create deterministic local demo sources
tarel connector     check, probe, discover, sample, and scaffold connectors
tarel graph         build, refresh, list, show, and batch-annotate graphs
tarel annotation    plan, exchange, apply, inspect, edit, and review proposals
tarel relationship  add, probe, discover, list, validate, and reject possible joins
tarel search        retrieve relevant graph objects and fields
tarel context       build, diff, and impact-check deterministic context packets
tarel model         explicitly download and verify the optional embedding model
tarel index         build and inspect local vector indexes
tarel workspace     organize graphs into systems, areas, schemas, and zones
tarel provider      configure and test optional annotation providers
```

Every substantive command supports deterministic JSON output where it is useful. Run
`tarel COMMAND --help` for the exact current interface.

## What TAREL does not do yet

- no stable public SDK;
- the distribution does not yet ship PostgreSQL, cloud warehouse, lake, document, or orchestration
  connectors; agents can already author database and warehouse candidates;
- no operational lineage or ETL run history yet;
- no tested Apache Ossie import/export compatibility yet;
- no multi-user shared graph store yet;
- no automatic execution of analytical SQL;
- no autonomous activation of generated connector code -- activation remains a human trust gate;
- no web UI, catalog server, or mandatory cloud service.

These are scope boundaries, not hidden fallbacks. Unsupported capabilities fail visibly.

## Development

```bash
python -m pip install -e '.[dev]'
ruff check src tests tools
python -m unittest discover -s tests -q
python -m compileall -q src tests tools
python -m build
python tools/check_distribution.py dist
```

Live database targets and credentials belong in ignored local configuration. The deterministic
SQLite demo and the unit suite require no external service.

## Documentation

- [Retail DWH demo](docs/retail-demo.md)
- [Change Radar and stale claims](docs/change-radar.md)
- [Local retrieval](docs/local-retrieval.md)
- [Context packet contract](docs/context-contract.md)
- [Workspaces, systems, areas, and zones](docs/workspaces.md)

## License

TAREL is available under the [MIT License](LICENSE). Optional components and the separately
downloaded embedding model are listed in the [third-party notices](THIRD_PARTY_NOTICES.md).
