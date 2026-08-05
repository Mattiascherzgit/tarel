# Context packet contract

Experimental `tarel.context.v0.2` separates graph-derived knowledge from request-specific
retrieval state and gives each part an independently verifiable identity. The application use case
and CLI return the same packet:

```json
{
  "contract_version": "tarel.context.v0.2",
  "stable": {
    "annotation_states": ["deferred", "draft", "validated"],
    "graph": {"name": "example", "revision": "<sha256>"},
    "joins": [],
    "objects": [],
    "scope": {"mode": "retrieval", "namespace": null}
  },
  "dynamic": {
    "budgets": {},
    "omissions": {},
    "paths": [],
    "query": "...",
    "retrieval": {},
    "selection": []
  },
  "identity": {
    "stable_hash": "<sha256>",
    "dynamic_hash": "<sha256>",
    "packet_hash": "<sha256>"
  }
}
```

The stable section contains the selected graph facts, semantic annotations, joins, graph revision,
and selection scope. Search scores, selection reasons, paths, budgets, and the question belong to
the dynamic section. Both JSON and text renderers emit stable facts before dynamic request data so
a harness can reuse the largest possible prefix; the combined identity follows both JSON sections.

The query still determines which facts are selected. Within that selected set, objects, fields, and
joins are ordered by stable IDs rather than search rank; rank exists only in `dynamic.selection`.
Future schema, zone, area, and system packets can reuse the representation while changing the
explicit scope.

## Identity and comparison

- `stable_hash` is SHA-256 over canonical compact JSON of `stable`.
- `dynamic_hash` is SHA-256 over canonical compact JSON of `dynamic`.
- `packet_hash` binds the contract version and both section hashes.
- The graph revision remains SHA-256 over the complete canonical graph document.
- Identical graph, query, retrieval result, scope, and budgets produce byte-identical canonical
  JSON.
- The packet contains no timestamps, elapsed times, local paths, connections, or process metadata.

Consumers must validate hashes before trusting a serialized v0.2 packet. A query-only change may
reuse the stable prefix when `stable_hash` remains equal. A graph, semantic review, or stable scope
change produces a new stable identity.

`tarel context diff LEFT RIGHT` validates both packets and reports stable, dynamic, graph revision,
scope, query, object, and join differences. The former invocation remains compatible:

```bash
tarel context GRAPH "sales by year"
tarel context build GRAPH "sales by year"
tarel context diff first.json second.json --format json
```

## Character budget and omissions

`--max-characters` limits the complete packet measured as canonical compact JSON characters. The
default is 24,000. Both the complete count and stable-section count are reported. This metric is
tokenizer-independent and therefore reproducible across Codex, Claude Code, Pi, and SDK consumers.

When necessary, TAREL removes the lowest-ranked fields first, then expansion paths, joins, and
lower-ranked objects. It never truncates the question or a semantic string midway. If the smallest
valid packet cannot fit, the command fails visibly. `dynamic.omissions` reports omitted objects,
fields, joins, and paths plus the responsible budget categories.

Token budgets, provider cache headers, session affinity, breakpoints, and TTLs remain consumer
concerns. Consumers may use the packet identities but must not silently change this contract.
Version 0.2 is pre-alpha and may change before TAREL 0.0.1.
