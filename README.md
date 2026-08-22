# TAREL

**Map the data estate. Review its meaning. Give agents only the context they need.**

[![PyPI](https://img.shields.io/pypi/v/tarel)](https://pypi.org/project/tarel/)
[![Python](https://img.shields.io/pypi/pyversions/tarel)](https://pypi.org/project/tarel/)
[![CI](https://github.com/mpsgitai/tarel/actions/workflows/ci.yml/badge.svg)](https://github.com/mpsgitai/tarel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

TAREL maps the **semantic and lineage layer of complex information systems**. It is a local-first
Python **CLI and SDK** for discovering data estates, proposing and reviewing meaning, tracing data
from reports back to its origins, and compiling trustworthy context for agents and people.

The core has **no mandatory third-party runtime dependency**. It can sit beside an existing DWH,
ERP, lake, orchestration stack, coding harness, or BI application without becoming another hosted
catalog platform.

> **TAREL** stands for **Topology, Annotation, Retrieval, Evidence & Lineage**.

```text
databases · marts · workflow exports · reports
                      │
                      ▼
       technical topology + lineage graph
                      │
            semantic proposals + review
                      │
             BM25 + local embeddings
                      │
                      ▼
       precise context for humans and agents
```

![TAREL semantic information space with reviewed metadata](https://raw.githubusercontent.com/mpsgitai/tarel/master/docs/assets/semantic-space.png)

*An annotated TPC-DS information space in the dependency-free local browser UI. Objects, fields,
roles, grain, confidence, and review state remain inspectable.*

## Why TAREL

The strongest model is still ineffective when it does not know which of hundreds or thousands of
tables matter. Enterprise analytical systems add another problem: the frontier coding agent may be
allowed to generate SQL but not to inspect raw ERP or DWH data.

TAREL separates those responsibilities:

- a read-only connector observes bounded technical evidence;
- an optional private or local model proposes semantics and physical lineage;
- a human reviews important claims;
- local retrieval selects the relevant part of the estate;
- a frontier harness receives compact metadata context, not credentials or raw rows.

The same map also helps engineers and analysts explore unfamiliar systems, preserve institutional
knowledge, review schema drift, and move from a report or measure back through ETL to its sources.

## What it does

- **Discovers topology** — catalogs, schemas, tables, views, fields, types, keys, and declared
  relationships become a deterministic graph.
- **Builds reviewed semantics** — models or coding agents propose descriptions, roles, grain,
  synonyms, field semantics, and possible joins; people validate, edit, defer, or reject them.
- **Offers entity hypotheses** — graph-bound normalization candidates remain separate from joins,
  expose aggregate coverage, collisions, confidence, and review state, and can be probed by agents
  before human approval without being presented as facts.
- **Imports external semantics** — experimental Apache Ossie, SML, and Cube YAML readers preserve,
  diagnose, and bind supported constructs to stable graph IDs while keeping them separate from
  reviewed TAREL annotations.
- **Traces lineage** — reports, visuals, measures, fields, marts, models, procedures, and jobs can
  be traversed across multiple lineage documents and physical graphs.
- **Retrieves context** — dependency-free BM25 and optional CPU-local Qwen embeddings locate
  relevant objects before bounded graph expansion.
- **Grounds agents** — stable and dynamic context blocks include source identity, SQL dialect,
  lineage revisions, evidence, warnings, and visible omissions without connection details.
- **Tracks change** — schema drift preserves existing knowledge, marks affected claims for review,
  and reports impacted workspaces, zones, and saved context packets.
- **Extends locally** — a coding agent can implement a missing connector or provider behind a small
  reviewed contract; generated adapters remain inactive until a human installs them.

## Report-to-source lineage

TAREL can begin with the object a user actually knows — a report, visual, measure, cube field, or
mart column — and walk upstream across semantic and physical boundaries.

![TAREL report-to-source lineage trace](https://raw.githubusercontent.com/mpsgitai/tarel/master/docs/assets/report-lineage.png)

*A Power BI report measure traced through a semantic column, physical mart, dbt models, an extract,
and five DWH origin tables. Draft state and the field-to-object granularity change remain visible.*

Lineage keeps different evidence separate:

- workflow order describes **when** jobs run;
- calls describe **which** definitions invoke others;
- read/write claims describe **where data flows**;
- review state describes **how much the claim is trusted**.

Sanitized runtime SQL, MongoDB, federated DuckDB, and caller-executed Python analysis observations
use a separate create-only contract. They retain ordered call identity, exact graph bindings,
explicit result dependencies, executor identity, bounded-frame hashes, reconciliation evidence,
status, row count, schema, and hashes without persisting query/code text, parameters, raw rows,
connection details, or free-form database errors. TAREL stores and traces these observations but
executes no analysis code. See
[Runtime lineage](docs/runtime-lineage.md).

The text, JSON, SDK, and browser trace also expose the evidence attached to each hop. Importers can
therefore retain compact DAX expressions, compiled SQL, or exact manifest bindings instead of
reducing a report-to-source path to unexplained arrows.

This prevents a scheduler dependency from silently becoming invented table lineage.

## Entity-resolution hypotheses

TAREL can store and retrieve bounded normalization candidates without turning them into technical
joins or confirmed facts. An agent may use an unreviewed candidate when no approved rule exists,
but every CLI and SDK result exposes its evidence level, evaluated count, coverage, collision rate,
confidence, review state, and `requires_runtime_validation` flag.

```bash
tarel entity import --source sanitized-candidate.json --format json

tarel entity find music \
  --source-field mb.ArtistCredit.Name \
  --target-field mb.Artist.Name \
  --mode confirmed_then_candidates \
  --format json
```

```python
from tarel.sdk import EntityResolutionCandidate, Tarel

tarel = Tarel(".tarel")
candidate = EntityResolutionCandidate.from_dict(sanitized_candidate_payload)
tarel.entity_resolution.import_candidate(candidate)

matches = tarel.entity_resolution.find(
    "music",
    source="mb.ArtistCredit.Name",
    target="mb.Artist.Name",
    mode="confirmed_then_candidates",
)
```

Unreviewed matches use `exploratory_only`; TAREL never executes their rules. Current candidates can
also be shown as optional violet edges in the local information-space graph. See
[Entity-resolution candidates](docs/entity-resolution-candidates.md) for the complete contract,
review commands, retrieval modes, and data boundary.

## Human in the loop

Generated semantics are proposals, not truth. The local browser guides reviewers through table
meaning first while keeping field-level suggestions, evidence, provider provenance, warnings, and
confidence available.

![TAREL field annotation inspector](https://raw.githubusercontent.com/mpsgitai/tarel/master/docs/assets/tarel-field-annotation-inspector.png)

*Expand every field to inspect its description, semantic role and type, confidence reason,
synonyms, evidence, review state, and provider provenance. Review, edit, approve, defer, or reject
semantic proposals without sending the graph to a hosted UI. The browser makes no external
requests.*

Existing Markdown or text documentation can be attached as optional annotation context at global,
system, graph, schema, or object scope. TAREL resolves a deterministic, bounded set for each object;
the default remains `--knowledge none`, so documents are never sent to a provider implicitly.
System scopes are bound to the workspace supplied when the document is registered. When the
budget is tight, the most specific scopes receive space first; retained documents are still
serialized in stable broad-to-narrow order. Explicit `--knowledge-document` selections take
priority over automatic scope matches.

```bash
tarel knowledge add commercial-terms docs/commercial-terms.md \
  --scope system:commercial --workspace enterprise --state validated
tarel knowledge add sales-grain docs/sales-grain.md \
  --scope object:warehouse:mart.FactSales --state validated

tarel annotation next warehouse --object mart.FactSales \
  --knowledge scoped --knowledge-workspace enterprise
```

The task and resulting annotation retain document IDs, scopes, states, and revisions—not source
paths. The review UI shows which documents were supplied while keeping them distinct from accepted
evidence. This is intentionally a small reference layer, not a document-management system.

## Install

TAREL supports Python 3.11 and 3.12.

```bash
python -m pip install tarel
tarel --version
```

The base install uses only the Python standard library. Add optional capabilities only when needed:

```bash
# SQL Server metadata and bounded sampling
python -m pip install 'tarel[sqlserver]'

# CPU-local semantic retrieval through llama.cpp
python -m pip install 'tarel[local-rag]'

# YAML semantic-model imports (Apache Ossie, SML, and Cube)
python -m pip install 'tarel[semantic]'
```

SQLite support is built in. Other database and lake connectors, plus workflow and report lineage
importers, can be added as independently reviewed extensions.

## Quickstart

The bundled Retail DWH is synthetic, local, and requires no credentials.

```bash
# Create a small source below the ignored .tarel directory.
tarel demo create retail-dwh

# Give the connection a stable, non-secret logical name.
tarel source configure retail-local \
  --connector sqlite \
  --config-ref state:demos/retail-dwh.toml \
  --namespace main \
  --allow-aggregates \
  --allow-small-domains \
  --allow-raw-samples

tarel source check retail-local
tarel source probe retail-local
tarel source build retail-local retail-demo
tarel source enrich retail-local retail-demo --format json

# Dependency-free semantic retrieval and bounded agent context.
tarel search retail-demo \
  "internet and reseller sales by year" \
  --mode bm25

tarel grounding retail-demo \
  "internet and reseller sales by year" \
  --source retail-local \
  --mode bm25
```

Source enrichment is deny-by-default. Profiles, complete small-domain values, and bounded raw
samples require the corresponding source grants. Raw rows appear only in the current command
result and are never copied into the graph, retrieval index, context packet, or browser payload.
Transformed join drafts are deliberately rare: a repeated key pattern must also carry a segment
prefix that matches a target token or acronym, and TAREL keeps at most one candidate per segment.

Inspect the graph locally:

```bash
tarel ui retail-demo
```

The demo intentionally contains abbreviated fact names and one missing relationship. Continue with
the [complete Retail DWH walkthrough](https://github.com/mpsgitai/tarel/blob/master/docs/retail-demo.md)
to sample evidence, propose annotations, review joins, enable hybrid retrieval, and reproduce schema
drift.

## Use it from Python

The SDK calls the same application use cases as the CLI. It never changes the working directory or
starts CLI subprocesses.

```python
from tarel.sdk import Tarel

tarel = Tarel(root="/path/to/project/.tarel")

bundle = tarel.grounding.context(
    "internet and reseller sales by year",
    graph="retail-demo",
    sources=("retail-local",),
    mode="bm25",
)

# Suitable for a reusable system-prefix and the current user turn.
stable_context = bundle.stable_prompt()
dynamic_context = bundle.dynamic_prompt()
```

The bundle maps every selected object to a logical read-only source and dialect. It never contains
the source profile's config reference, resolved URL, password, sample rows, or provider key.

See the [SDK guide](https://github.com/mpsgitai/tarel/blob/master/docs/sdk.md) for workspaces,
scope selection, lineage search, upstream tracing, reviews, vector indexes, and Space/Lineage UI
projections.

## Retrieval and context

Exact search and BM25 work without an index. For poorly named enterprise schemas, build the
optional local semantic index after annotation or review changes:

```bash
tarel model download
tarel index build retail-demo --resume

tarel context retail-demo \
  "returns by product and sales channel" \
  --mode hybrid \
  --max-objects 10 \
  --max-characters 24000
```

The pinned Qwen3-Embedding-0.6B GGUF runs in-process on the CPU. TAREL does not add Torch,
SentenceTransformers, a vector database, an API server, or a local generation model. Indexes contain
only allowlisted graph metadata and are explicitly rebuildable. With `--resume`, completed
embedding batches survive an interruption and are reused only when the graph, retrieval-document
projection, and model still match exactly. The previous complete index stays readable until its
replacement is ready.
Index construction reports document batches and the final persistence phase, so a larger local
schema never looks stalled while the CPU model is working. llama.cpp decodes each document
independently; `--batch-size` controls scheduling and progress rather than token capacity.

Context packets are deterministic and budgeted. They report selected objects, fields, joins,
expansion paths, retrieval reasons, warnings, review state, hashes, and every omission. Stable facts
come before question-specific retrieval state so Codex, Claude Code, Pi, or another harness can use
provider prefix caching without TAREL depending on that provider.

## Sources, systems, and focused exploration

A **source** gives a reviewed connector a stable logical name without persisting its URL. A
**graph** represents one discovered technical estate. A **workspace** organizes several graphs as:

```text
workspace
└── system
    └── area
        └── schema
```

Overlapping **zones** can select business-relevant objects across schemas. A revision-bound
**focus** starts at a report, cube, measure, or field and keeps only its upstream slice. This allows
teams to begin with one useful report inside a thousand-table estate and expand reviewed knowledge
demand by demand.

## Self-extending connectors

TAREL ships SQLite and SQL Server as reference implementations, not as a closed connector list.
Create an isolated candidate when an agent encounters a new system:

```bash
tarel connector scaffold postgres \
  --output .tarel/connectors/postgres-candidate
```

The candidate contains a versioned read-only contract, manifest, implementation task, dialect
notes, and a Python entry point. The coding agent may research official documentation and implement
the adapter locally. TAREL does not install or execute it automatically; a human reviews, tests, and
activates the package explicitly.

## CLI map

| Command | Purpose |
|---|---|
| `tarel source` | Manage logical sources; probe, discover, build, refresh, and enrich by policy |
| `tarel connector` | Inspect, profile, sample, and scaffold read-only adapters |
| `tarel graph` | Build, import, refresh, inspect, and batch-annotate graphs |
| `tarel annotation` | Plan, apply, edit, and review semantic proposals |
| `tarel knowledge` | Attach bounded Markdown/TXT context to annotation scopes |
| `tarel relationship` | Add, discover, probe, and review joins |
| `tarel entity` | Import, retrieve, inspect, and review entity-resolution hypotheses |
| `tarel lineage` | Trace static lineage; import sanitized SQL/MongoDB/DuckDB/Python observations |
| `tarel focus` | Save report- or cube-centred upstream slices |
| `tarel workspace` | Organize systems, areas, schemas, zones, and cross-graph joins |
| `tarel search` | Run lexical, BM25, vector, or hybrid retrieval |
| `tarel context` | Compile, compare, and impact-check context packets |
| `tarel grounding` | Add source, dialect, lineage, and trace identity for agents |
| `tarel ui` | Explore Space/Lineage views and perform local human review |

Use `tarel COMMAND --help` for the exact installed interface. Machine-oriented commands support
deterministic JSON output where useful.

## Architecture and data boundary

```text
CLI · Python SDK · local browser UI
                 │
                 ▼
       application use cases
                 │
                 ▼
 graph · annotation · lineage · workspace · retrieval
                 │
                 ▼
 connectors · providers · file stores · optional indexes
```

Contracts define truth, application use cases coordinate work, and adapters remain replaceable.
`import tarel` is cheap and side-effect free; optional drivers load only inside their adapters.

Read the [architecture guide](https://github.com/mpsgitai/tarel/blob/master/docs/architecture.md)
for dependency direction, extension seams, persistence, grounding, and the shared CLI/SDK/UI path.

Local state lives below the selected `.tarel/` root. Graphs and lineage contain metadata,
reviewable claims, and evidence references — not a copy of the warehouse. Secrets, resolved
connection URLs, raw samples, downloaded models, indexes, and local test targets are excluded from
Git and agent-facing context.

## Documentation

- [Architecture](https://github.com/mpsgitai/tarel/blob/master/docs/architecture.md)
- [Embedded Python SDK](https://github.com/mpsgitai/tarel/blob/master/docs/sdk.md)
- [Retail DWH demo](https://github.com/mpsgitai/tarel/blob/master/docs/retail-demo.md)
- [Local semantic retrieval](https://github.com/mpsgitai/tarel/blob/master/docs/local-retrieval.md)
- [Context packet contract](https://github.com/mpsgitai/tarel/blob/master/docs/context-contract.md)
- [Workspaces, systems, areas, and zones](https://github.com/mpsgitai/tarel/blob/master/docs/workspaces.md)
- [Change Radar](https://github.com/mpsgitai/tarel/blob/master/docs/change-radar.md)

## Current boundaries

TAREL is pre-alpha. Its local file-first core supports concurrent reads and one writer per document.
Shared database-backed stores, authorization, coordinated multi-writer operation, additional source
families, and standard semantic-model interchange belong to optional future adapters.

TAREL does not execute analytical answer queries, replace a warehouse, or silently promote model
output to truth. It compiles the map and evidence that humans and agents need to work safely.

## Development

```bash
python -m pip install -e '.[dev]'
ruff check src tests tools
python -m unittest discover -s tests -q
python -m build
python tools/check_distribution.py dist
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
