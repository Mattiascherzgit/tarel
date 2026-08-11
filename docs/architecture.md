# Architecture

TAREL is a local-first context compiler, not an agent framework. Its reusable core maps technical
metadata, semantic claims, relationships, and lineage into deterministic contracts. The CLI, SDK,
and browser UI are three adapters over the same application behavior.

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

Dependencies point inward. Domain code does not import the CLI, SDK, or UI. Entry adapters may
compose application use cases, but they do not own a second implementation of the business rules.

## Layers

| Layer | Main paths | Responsibility |
|---|---|---|
| Entry adapters | `tarel.cli`, `tarel.sdk`, `tarel.ui` | Parse input, call use cases, render typed results |
| Application | `tarel.application`, `tarel.grounding_application`, domain `application.py` modules | Coordinate stores, domain transformations, and explicit side effects |
| Domain | `graph`, `annotations`, `lineage`, `focus`, `relationships`, `workspaces`, `context`, `grounding` | Contracts, validation, revisions, review state, traversal, and deterministic compilation |
| Infrastructure | `connectors`, `providers`, `retrieval`, domain stores | Observe external systems and persist local rebuildable state |
| Runtime | `tarel.runtime` | Bind one SDK client to an explicit local state root |

## One implementation, three entry points

The CLI and SDK call the same application functions. A graph built with the CLI can be loaded by
the SDK; a review performed in the browser changes the same revisioned document. The browser UI is
served from the standard library and consumes the same graph, workspace, focus, annotation, and
lineage projections.

```text
tarel source build ... ─┐
Tarel(...).source... ───┼─► application use case ─► domain contract ─► .tarel state
local review UI ────────┘
```

`import tarel` stays cheap and side-effect free. Source drivers and the local embedding runtime are
optional and imported only inside their adapters.

## Semantic graph path

```text
read-only source
  → connector observations
  → technical graph
  → model or coding-agent proposals
  → human review
  → BM25 / optional local embeddings
  → bounded context packet
```

Technical observations and semantic claims remain separate. An annotation begins as a draft and
keeps its evidence, provider identity, confidence, and review state. The source remains
authoritative; TAREL stores metadata and reviewable knowledge rather than a warehouse copy.

## Lineage path

```text
report / visual / measure
  → semantic field
  → physical mart object
  → query, model, procedure, or job
  → upstream tables
  → source-system origins
```

Workflow importers normalize external exports into one lineage contract. Optional providers may
analyze complete SQL definitions and propose evidence-backed reads and writes. Job order, procedure
calls, and physical data flow remain distinct; TAREL never treats execution order alone as data
lineage. Cross-document traversal is explicit and preserves unresolved references, review state,
evidence, cycles, and granularity changes.

## Grounding and cache boundaries

Search chooses graph anchors; the context compiler expands them through reviewed relationships and
reports every omission caused by a budget. `tarel.grounding.v0.1` then adds non-secret source
identity, SQL dialect, selected lineage revisions, matches, and an optional upstream trace.

Agent-facing output has two independently hashed parts:

- `stable`: selected semantic facts, joins, source identities, and lineage document revisions;
- `dynamic`: the question, retrieval decisions, paths, warnings, omissions, and optional trace.

This lets a harness place reusable context in a provider cache-friendly prefix without making the
core provider-specific.

## Extension boundaries

TAREL deliberately has a few narrow extension seams:

- **Connectors** normalize read-only probes, catalog discovery, bounded sampling, and relationship
  evidence. Reviewed external packages register through the `tarel.connectors` entry-point group.
- **Providers** return schema-validated annotation or lineage workfiles. Provider profiles and
  credentials stay outside persisted graphs and context packets.
- **Stores** are file-first today. Shared database-backed stores and authorization can be added as
  optional adapters without changing domain contracts.

Generated connector or provider candidates are inactive until a human reviews and installs them.
Self-extension removes repetitive adapter work; it does not grant generated code automatic trust.

## Persistence boundary

The selected state root contains revisioned JSON documents and rebuildable indexes:

```text
.tarel/
├── sources/
├── graphs/
├── lineage/
├── focus/
├── workspaces/
├── indexes/
└── lineage-analysis-cache/
```

Writes are atomic. Documents use canonical ordering and SHA-256 identities and omit timestamps,
runtime durations, and volatile paths from agent-facing contracts. The first SDK supports
concurrent reads and one writer per document; coordinated multi-writer storage is intentionally an
optional future adapter.

## Public surface

The stable entry points are the `tarel` command and `from tarel.sdk import Tarel`. Domain modules
remain available for typed integration, but consumers should prefer the SDK unless they are
implementing or testing a TAREL extension.

The architectural rule is simple:

```text
contracts define truth; use cases coordinate work; adapters remain replaceable
```
