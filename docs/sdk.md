# Embedded Python SDK

The experimental SDK is a thin Python surface over the same application use cases as the CLI. It
does not invoke the CLI through subprocesses and it does not maintain a second implementation of
search, context, lineage, focus, or annotation behavior.

## Open a local TAREL state directory

An embedded application must select the state directory explicitly:

```python
from tarel.sdk import Tarel

tarel = Tarel(root="/srv/my-application/.tarel")
```

The path is the `.tarel` state directory itself. Constructing the client performs no discovery,
network request, model download, database connection, or write. Separate clients can safely point
at separate roots without changing the process working directory.

## Import a caller-observed catalog

An embedding application that already owns a connector-compatible `CatalogResult` can persist its
technical graph without opening the source or running discovery again:

```python
from tarel.connectors.contracts import CatalogResult
from tarel.sdk import Tarel

tarel = Tarel("/srv/bi-agent/.tarel")
catalog: CatalogResult = observe_catalog_in_the_host_application()
result = tarel.graph.import_catalog("observed-warehouse", catalog)
```

The SDK and CLI call the same application use case and atomic graph store. This first import
boundary is intentionally create-only: if the graph name already exists, TAREL raises
`graph_exists` and leaves the stored graph unchanged. Connector-backed refresh remains available
through `tarel.graph.refresh(...)`; revision-aware refresh of caller-observed catalogs is not yet
part of this API.

The CLI accepts the strict JSON shape emitted by `CatalogResult.to_dict()` and by connector
discovery:

```bash
tarel connector discover sqlite --config .tarel/connectors/retail.toml \
  --format json > observed-catalog.json
tarel graph import-catalog observed-warehouse \
  --source observed-catalog.json
```

Malformed objects, unknown fields, duplicate objects or fields, invalid primary keys, and dangling
foreign-key references fail before graph persistence. Catalog input contains technical metadata;
it must not contain credentials, connection URLs, samples, or query results.

## Configure a logical source

The optional local source registry binds a stable name to a reviewed connector and a private config
reference:

```python
source = tarel.source.configure(
    "warehouse-prod",
    connector="sqlserver",
    config_reference="env:TAREL_WAREHOUSE_CONFIG",
    database="EnterpriseDW",
    namespace="mart",
    enrichment_permissions=("aggregates", "small_domains", "raw_samples"),
)

status = tarel.source.check("warehouse-prod")
probe = tarel.source.probe("warehouse-prod")
catalog = tarel.source.discover("warehouse-prod")
graph = tarel.source.build_graph("warehouse-prod", "warehouse")
enrichment = tarel.source.enrich("warehouse-prod", "warehouse")
```

An `env:` reference resolves to a private TOML file path provided by the embedding process. A
`state:` reference resolves to a safe relative path below this client's state directory. TAREL
rejects connection URLs, absolute state paths, and traversal segments as config references. A
profile with no config reference relies on the connector's documented environment variable.

The persisted `SourceProfile` contains no resolved URL or credential and is always read-only.
`build_graph` records the graph association; `refresh_graph` reuses the same source boundary.
Installed private adapters are discovered through the `tarel.connectors` Python entry-point group,
while the core retains no dependency on their drivers.

Enrichment permissions are deny-by-default and become part of the source revision. `aggregates`
allows bounded profiles, `small_domains` adds complete values for small domains, and `raw_samples`
allows at most ten rows per graph object in the returned `SourceEnrichmentResult`. The result is an
ephemeral workfile; TAREL does not persist its profiles or rows. Passing
`persist_join_candidates=True` may persist only aggregate evidence for transformed draft joins and
requires `raw_samples` permission. Candidate generation is deliberately precision-first: temporal
and ordinary free-text patterns are excluded, each digit segment needs a literal cue that matches
the target object or field, and only the strongest target per source segment survives. A successful
enrichment may consequently return patterns but zero candidates. Drafts remain unusable by context
expansion until a human validates them.

## Ground a BI-agent turn

`tarel.grounding` is the high-level surface for an agent that uses TAREL as its semantic context
engine. It composes the existing context and lineage use cases; it does not introduce a second
retrieval implementation:

```python
from tarel.sdk import Tarel, WorkspaceScope

tarel = Tarel("/srv/bi-agent/.tarel")
scope = WorkspaceScope(systems=("commercial",), zones=("revenue",))

bundle = tarel.grounding.context(
    "Explain annual net sales and prepare the source objects for SQL generation",
    workspace="enterprise",
    selection=scope,
    sources=("warehouse-prod",),
    lineages=("reporting", "dbt", "warehouse-etl"),
    trace="powerbi.Sales.Report.NetSales",
    mode="hybrid",
)

system_prefix = bundle.stable_prompt()
turn_context = bundle.dynamic_prompt()
```

The stable prefix contains the selected semantic objects, reviewed joins, explicit lineage
document revisions, and one `SourceTarget` per participating graph. A source target maps context
object IDs to its logical source, connector, catalog, source type, SQL dialect, graph revision, and
source-profile revision. It never contains an endpoint, config reference, credential, sample value,
or provider secret. The dynamic part contains the question, retrieval choices, visible omissions,
lineage matches, and optional exact upstream trace. Both parts and the complete bundle have
independent SHA-256 identities.

This separation lets a host keep a large reviewed zone beneath a stable system prompt for provider
prefix caching, or attach only the dynamic retrieval slice to a short-lived turn. TAREL reports the
identities; the host remains responsible for provider-specific cache headers, session affinity,
and token accounting.

The convenience operations stay explicit:

```python
found = tarel.grounding.find(
    "customer revenue",
    workspace="enterprise",
    selection=scope,
    sources=("warehouse-prod",),
    lineages=("reporting", "warehouse-etl"),
)
asset = tarel.grounding.describe(
    "warehouse",
    "mart.FactSales",
    source="warehouse-prod",
)
trace = tarel.grounding.upstream(
    "powerbi.Sales.Report.NetSales",
    workspace="enterprise",
    selection=scope,
    lineages=("reporting", "warehouse-etl"),
)
```

`find` returns a small `GroundingBundle` through tolerant retrieval without relationship expansion.
`describe` is exact and fails on a missing or ambiguous object. `upstream` also requires exact,
explicitly selected lineage documents.
The lower-level `search`, `context`, and `lineage` namespaces remain available when an embedding
application wants to orchestrate each step itself.

## Import an external semantic model

Imported semantic values remain distinct from TAREL-authored annotations. Experimental readers
accept Apache Ossie files plus SML and Cube YAML project directories and preserve exact source
snapshots:

```python
result = tarel.semantic.import_file(
    "retail-ossie",
    graph="warehouse",
    source="semantic-model.yaml",
    format_name="apache-ossie",
)

imports = tarel.semantic.list(graph="warehouse")
document = tarel.semantic.load("retail-ossie")
view = tarel.view.graph("warehouse", editable=True)
```

JSON inputs require no extra dependency; YAML needs `tarel[semantic]`. Project inputs are stored as
deterministic multi-file bundles. Supported datasets, simple fields, metrics, and declared
relationships normalize into one contract; only exact graph bindings receive stable graph IDs.
Unknown or unbound constructs are returned as diagnostics and remain present in the source
snapshot. `tarel.semantic.edit(...)` records a description or synonym overlay with a reason and
optional optimistic revision check; it never rewrites the original source. See
[Semantic-model imports](semantic-imports.md) for the contract and current limits.

When a graph has exactly one registered source, grounding selects it automatically. Multiple
profiles mapped to the same graph fail closed; pass `sources=("warehouse-prod",)` to choose the
execution environment explicitly. Source-profile changes invalidate the stable grounding hash even
when the semantic graph itself is unchanged.

## Build a workspace

Applications can create the same system, area, schema, and overlapping-zone structure available
through the CLI:

```python
tarel.workspace.create("enterprise", description="Shared analytical estate")
tarel.workspace.define_system(
    "enterprise",
    "commercial",
    graphs=("erp", "warehouse"),
)
tarel.workspace.define_area(
    "enterprise",
    "commercial",
    "analytics",
    schemas=("warehouse:mart",),
)
tarel.workspace.define_zone(
    "enterprise",
    "commercial",
    "revenue",
    objects=("warehouse:mart.FactSales", "erp:sales.Orders"),
)
```

Graph-local joins use `tarel.relationship`; relationships whose endpoints belong to different
graphs use `tarel.workspace.add_relationship`. Both begin as reviewable evidence unless the caller
explicitly records a human-validated decision.

The hierarchy and the orthogonal zone concept are explicit:

```text
workspace
└── system
    ├── graphs
    ├── areas
    │   └── graph:schema
    └── zones
        └── graph:schema.object
```

An area groups complete sibling schemas. A zone selects individual objects and may overlap other
zones or cross several schemas and areas. Repeated selectors within one level form a union; filters
across levels form an intersection.

An embedded agent can define one typed selection and reuse it unchanged for inspection, retrieval,
context compilation, and prompt caching:

```python
from tarel.sdk import Tarel, WorkspaceScope

tarel = Tarel("/srv/little-nice-bi/.tarel")
sales_scope = WorkspaceScope(
    systems=("commercial",),
    graphs=("erp", "warehouse"),
    areas=("analytics",),
    schemas=("warehouse:mart",),
    zones=("revenue",),
)

resolved = tarel.workspace.scope("enterprise", selection=sales_scope)
results = tarel.search.workspace(
    "enterprise",
    "annual customer revenue",
    selection=sales_scope,
    mode="hybrid",
)
packet = tarel.context.workspace(
    "enterprise",
    "annual customer revenue",
    selection=sales_scope,
    mode="hybrid",
)
cached_scope = tarel.context.prefix_workspace(
    "enterprise",
    selection=sales_scope,
)
```

Passing both `selection=...` and individual scope filters fails visibly instead of silently
combining two potentially different privacy or caching boundaries.

## Attach annotation knowledge

Reference documents stay outside graph contracts. They are copied into local TAREL state with a
content hash and assigned to a global, system, graph, schema, or object scope. System scopes are
bound to their validated workspace:

```python
tarel.knowledge.add(
    "commercial-terms",
    "docs/commercial-terms.md",
    scope="system:commercial",
    workspace="enterprise",
    state="validated",
)
tarel.knowledge.add(
    "sales-grain",
    "docs/sales-grain.md",
    scope="object:warehouse:mart.FactSales",
    state="validated",
)

preview = tarel.knowledge.resolve(
    "warehouse",
    "mart.FactSales",
    workspace="enterprise",
)
task = tarel.annotation.plan_graph(
    "warehouse",
    objects={"mart.FactSales"},
    knowledge="scoped",
    knowledge_workspace="enterprise",
)[0]
```

Annotation planning defaults to `knowledge="none"`. `scoped` adds matching documents; explicit
document IDs can be passed with `knowledge_documents=(...)`. Explicit documents receive budget
first; automatic matches prioritize object, schema, and graph knowledge over broader scopes. The
character budget bounds the exact provider input. The
resulting annotation metadata records only the selected document ID, scope,
state, revision, character count, and truncation flag. Knowledge content is untrusted reference
data and is explicitly separated from provider instructions.

## Retrieve context

```python
results = tarel.search.workspace(
    "enterprise",
    "customer revenue",
    systems=("commercial",),
    zones=("revenue",),
    mode="bm25",
)

packet = tarel.context.workspace(
    "enterprise",
    "customer revenue",
    systems=("commercial",),
    zones=("revenue",),
    mode="bm25",
    max_objects=12,
    max_characters=24_000,
)

prompt_json = packet.canonical_json()
```

Returned objects are the same typed contracts used internally by the CLI. Their `to_dict()` and
`canonical_json()` projections are deterministic and contain no timestamps or implicit runtime
paths.

## Search and trace lineage

```python
matches = tarel.lineage.find_workspace(
    "enterprise",
    "total net sales",
    lineages=("reporting", "dbt", "warehouse-etl"),
    selection=sales_scope,
    mode="bm25",
)

trace = tarel.lineage.upstream_workspace(
    "enterprise",
    matches[0].reference,
    lineages=("reporting", "dbt", "warehouse-etl"),
    selection=sales_scope,
    max_hops=20,
)

process_steps = tarel.lineage.process("warehouse-etl")
table_flows = tarel.lineage.tables("warehouse-etl")
status = tarel.lineage.status("warehouse-etl")

focus = tarel.focus.load("commercial-sales")
origins = tuple(item.reference for item in focus.members if item.origin)
```

`lineage.find` and `lineage.find_workspace` support `lexical`, `bm25`, `vector`, and `hybrid`
retrieval. The vector modes use the same optional local embedding model as graph search. The
workspace variants derive graph catalogs from the shared `WorkspaceScope`; lineage documents stay
explicit because TAREL must not guess which workflow snapshots or manual overlays belong to a
trace. Selected lineage documents may deliberately lead beyond a zone to show the complete path to
an origin; a zone is an exploration filter, not an authorization boundary.

`lineage.upstream` and `lineage.upstream_workspace` remain fail-closed: pass explicit lineage
documents and use an exact reference returned by the corresponding `find` method when the starting
identifier is not already known.

### Import observed runtime query lineage

Runtime query evidence uses a separate experimental contract rather than pretending an execution
attempt is a reusable workflow definition:

```python
from tarel.lineage.runtime import RuntimeLineageInput

observed = RuntimeLineageInput.from_dict(sanitized_runtime_payload)
result = tarel.lineage.import_runtime("agent-run-001", observed)
loaded = tarel.lineage.load_runtime("agent-run-001")
trace = tarel.lineage.trace_runtime("agent-run-001", "accepted-duckdb-call")
```

The payload is bound to an exact graph revision and declares read-only SQL `select` or MongoDB
`find`/`aggregate` operations. A direct DuckDB source is represented by `sql_query` with
`dialect: "duckdb"`; a temporary DuckDB computation over earlier calls remains a separate
`federated_query` with `engine: "duckdb"`. The v0.2 contract also accepts caller-observed
`python_analysis` events with `tool_type: "python"`; TAREL does not execute the Python code.

Federated and Python analysis events identify their executor plugin and version, consume ordered
prior call IDs, bind each call to a source alias and hashed bounded input frame, and retain output
grain, join coverage, unmatched counts, reconciliation state, runtime, applied limits, result hash,
and status. V2 must import all events in one runtime document in dependency order: v0.2 does not
resolve `consumes` across separately persisted documents.

Events cannot contain SQL or Python code, MongoDB filters or pipelines, parameters, documents,
input frames, raw rows, connection URLs, credentials, or free-form errors. Imports are create-only.
`lineage.list_runtime()` lists these immutable run documents. See
[Runtime lineage](runtime-lineage.md) for the exact v0.1/v0.2 shapes and
[Entity-resolution candidates](entity-resolution-candidates.md) for the separate matching
hypothesis contract.

### Retrieve entity-resolution hypotheses

Entity-resolution candidates are graph-bound information rather than executable joins. The
default retrieval mode prefers reviewed rules for a field pair and otherwise offers explicitly
labelled candidates:

```python
from tarel.sdk import EntityResolutionCandidate

candidate = EntityResolutionCandidate.from_dict(sanitized_candidate_payload)
tarel.entity_resolution.import_candidate(candidate)

matches = tarel.entity_resolution.find(
    "music",
    source="mb.ArtistCredit.Name",
    target="mb.Artist.Name",
    mode="confirmed_then_candidates",
)

for match in matches:
    print(match.usage, match.requires_runtime_validation)
    print(match.candidate.evidence.to_dict())
```

Unreviewed matches use `exploratory_only` and require a runtime probe by the caller. TAREL stores
only declared rule operations, counts, rates, confidence, graph identity, and run provenance. It
does not store samples or execute matching. `confirmed_only` excludes every unreviewed rule;
`include_candidates` returns reviewed and unreviewed active candidates. Rejected candidates remain
in the audit store but are never retrieved. See
[Entity-resolution candidates](entity-resolution-candidates.md) for CLI commands, metric
invariants, and the violet browser projection.

### Feed a Space/Lineage GUI

One projection contains both canvas modes, so changing the mode is local UI state and never causes
the application to rediscover a graph or reinterpret lineage:

```python
view = tarel.view.workspace(
    "enterprise",
    selection=sales_scope,
    lineages=("reporting", "dbt", "warehouse-etl"),
)

space_objects = view["objects"]
space_relationships = view["edges"]
lineage_nodes = view["lineage_flows"]["nodes"]
lineage_edges = view["lineage_flows"]["edges"]
available_modes = view["view_modes"]  # ["space", "lineage"]
```

The built-in browser consumes this same combined projection: **Space** groups the scoped objects
by system, area, graph, and schema; **Lineage** reuses those objects and adds the explicitly
selected jobs, reads, writes, and process edges. Field rows in the object inspector expand to show
their complete TAREL annotation, confidence reasoning, evidence, provenance, review, and bounded
knowledge references. `tarel.view.graph(...)` provides the same contract for one graph. A custom
GUI owns only the selected mode, layout, and interaction state—not graph or lineage resolution.

Human knowledge that is unavailable from an exporter can be kept in a separate manual overlay:

```python
job = tarel.lineage.add_job(
    "manual-sales",
    kind="procedure",
    job_name="LoadSales",
    qualified_name="etl.LoadSales",
    language="tsql",
    source_reference="runbook:load-sales",
    description="Loads the reviewed sales mart.",
)
tarel.lineage.add_hop(
    "manual-sales",
    job="etl.LoadSales",
    source="stage.Sales",
    target="mart.Sales",
    operation="merge",
    evidence_reference="runbook:load-sales",
    reason="Confirmed by the data owner.",
)
```

## Annotation review

```python
queue = tarel.annotation.reviews("warehouse", states=frozenset({"draft"}))

tarel.annotation.edit(
    "warehouse",
    queue[0].reference,
    {"description": "Reviewed business description."},
    reason="Confirmed by the data owner.",
)

tarel.annotation.decide(
    "warehouse",
    queue[0].reference,
    state="validated",
    reason="Definition and grain approved.",
    include_fields=True,
)
```

Provider-backed batch annotation uses the same configured provider profiles as the CLI through
`tarel.annotation.run(...)`. Provider credentials remain in the existing private profile or
environment boundary; they are not stored in the SDK client.

An embedding application may pass samples that it has already authorized and bounded, keyed by the
stable table or view node ID:

```python
from tarel.connectors.contracts import SampleResult

samples: dict[str, SampleResult] = observe_bounded_samples()
result = tarel.annotation.run(
    "warehouse",
    provider="openrouter",
    samples_by_target=samples,
)
```

Each sample must match its target namespace, object name, and complete selected/omitted field set,
and may contain at most ten rows. The sample's connector and catalog remain provenance supplied by
the caller and may differ for objects in a composite graph. `samples_by_target` and connector-backed
`sample_limit` are mutually exclusive. Samples are input-only: TAREL checks that the provider never
echoes an observed value and persists neither the rows nor their values in the graph, retrieval
index, context packet, or annotation evidence.

## Context lifecycle and local embeddings

`tarel.context.diff(left, right)` validates and compares two serialized packets.
`tarel.context.impact(packet, graph=...)` reports whether a graph refresh invalidated the packet.
These operations let an embedding application reuse stable context safely instead of guessing from
file timestamps.

### Choose a prompt-caching strategy

For a short context window or a one-off request, compile only a retrieval snippet and place the
complete packet next to the question:

```python
packet = tarel.context.workspace(
    "enterprise",
    question,
    zones=("revenue",),
    mode="hybrid",
    max_objects=10,
)
request_context = packet.canonical_json()
```

When the same selected objects recur across questions, split that packet into a stable prefix and a
request-specific suffix. TAREL supplies identities but no provider-specific cache headers:

```python
parts = tarel.context.split(packet)

system_content = "TAREL stable context:\n" + parts.stable_json
user_content = "TAREL request context:\n" + parts.dynamic_json
cache_key = parts.cache_key
```

For a small graph or a repeatedly used system, area, schema, or zone, compile the complete selected
scope without a question. The whole returned packet is stable and can remain in the system prefix:

```python
cached_zone = tarel.context.prefix_workspace(
    "enterprise",
    systems=("commercial",),
    zones=("revenue",),
    max_objects=250,
    max_characters=500_000,
)

system_content = "TAREL cached scope:\n" + cached_zone.canonical_json()
user_content = question
cache_key = cached_zone.packet_hash
```

`prefix_graph(...)` provides the equivalent graph or schema scope. Prefix packets report every
object, field, and join omission. Their bytes change only when the selected scope, budgets, visible
annotation states, graph revisions, semantics, or relationships change—not when the user asks a
new question.

The optional local model remains an explicit operation:

```python
status = tarel.model.status()
if not status["exists"]:
    tarel.model.download()

result = tarel.index.build("warehouse", resume=True)
print(result.resumed_documents)
```

`resume=True` uses the same checkpoint contract as CLI `index build --resume`. The checkpoint is
accepted only for the exact graph content, allowlisted retrieval documents, model ID, and model
SHA-256. `tarel.index.status("warehouse")` exposes partial checkpoint coverage. Constructing `Tarel`
never downloads the model.

## Current surface

- `tarel.graph`: list, load, build, refresh
- `tarel.workspace`: create, organize, list, load, resolve scope and manage cross-graph joins
- `tarel.search`: graph and workspace retrieval
- `tarel.context`: retrieval snippets, graph/workspace cache prefixes, stable/dynamic splitting,
  packet diff and refresh impact
- `tarel.lineage`: static lineage plus create-only observed SQL/MongoDB/DuckDB/Python runtime imports
- `tarel.view`: one combined graph/workspace projection for Space and Lineage canvases
- `tarel.focus`: list, load, build
- `tarel.annotation`: plan, apply, inspect, edit, decide and provider batch
- `tarel.relationship`: add, probe, discover, list and review graph-local joins
- `tarel.model`: inspect and explicitly download the optional local embedding model
- `tarel.index`: build, resume and inspect the optional local vector index

This first SDK surface is local and file-first. Reads can be concurrent. Mutating the same graph,
lineage, focus, or workspace from multiple processes is not yet a coordinated multi-writer mode;
use one writer per document. Shared database-backed stores and authorization belong to optional
future adapters rather than this dependency-free SDK.
