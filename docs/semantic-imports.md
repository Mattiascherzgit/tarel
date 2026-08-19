# Semantic-model imports

TAREL can preserve and project an external semantic model without making that format the internal
graph contract. This boundary is experimental. Three deliberately small readers currently
exercise it: Apache Ossie `semantic_model`, Semantic Modeling Language (SML), and Cube YAML. This
is tested import coverage, not a general compatibility or round-trip claim for any format.

## Why the import stays beside the graph

The technical graph, imported source semantics, and TAREL-authored claims have different owners
and lifecycles:

```text
database observation ─► tarel.graph.v0.1
                              ▲ stable node and edge bindings
external semantic file ─► tarel.semantic_import.v0.1
                              │ exact source snapshot + normalized projection
TAREL/provider/human ───► graph annotations and review history
```

An imported description is not silently promoted to a reviewed TAREL annotation. The browser shows
both layers separately. A correction to an imported value is stored as an overlay event; the exact
source text and its SHA-256 identity remain unchanged.

This avoids two destructive shortcuts: reshaping the graph around one external standard and
overwriting TAREL annotations when the external model is re-imported.

## Import a semantic model

YAML parsing is an optional capability; JSON Ossie documents work with the standard-library base
installation.

```bash
python -m pip install 'tarel[semantic]'

tarel semantic import retail-ossie \
  --graph retail-demo \
  --format apache-ossie \
  --source semantic-model.yaml \
  --output json

tarel semantic import retail-sml \
  --graph retail-demo \
  --format sml \
  --source path/to/sml-project

tarel semantic import retail-cube \
  --graph retail-demo \
  --format cube \
  --source path/to/cube-model

tarel semantic list --graph retail-demo
tarel semantic show retail-ossie --output json
```

Each source is limited to 8 MiB, 256 files, and UTF-8. A single file is preserved byte-for-byte as
text. A project directory is preserved as a deterministic bundle containing every selected file's
relative path and exact text. Symlinks and path traversal are rejected. TAREL stores
that snapshot below `.tarel/semantic-imports/<name>/semantic-import.json`; `semantic show` omits
the content unless `--include-source` is explicit.

Dataset bindings use, in order, `catalog.schema.object`, `schema.object`, or a unique object name.
Fields bind only when an Ossie dialect expression is a simple identifier that uniquely matches a
field on the bound object. Relationships bind only to a declared TAREL `foreign_key` edge with the
same object direction and exact field lists. There is no fuzzy or LLM-created binding inside this
kernel step.

The readers normalize only constructs proven by their test fixtures:

- **Apache Ossie:** semantic models, datasets, fields, metrics, and relationships. Ontology
  documents are preserved with an explicit `unsupported_ossie_ontology` error.
- **SML:** catalog/model/dataset/metric objects, dataset columns, and direct metric definitions.
  Connections, dimensions, logical relationships, metric calculations, and unsupported keys stay
  preserved with diagnostics rather than being guessed into physical graph edges.
- **Cube YAML:** cubes, dimensions, measures, and simple joins. Views, links, cardinality metadata,
  inline SQL datasets, and unsupported keys stay preserved with diagnostics.

Unknown keys, custom extensions, unbound objects, unbound fields, and unbound relationships become
diagnostics. They remain available in the exact source snapshot; nothing is silently discarded.

## Re-import and edit rules

Importing the same file again is idempotent and refreshes deterministic bindings against the
current graph. Different source content requires `--replace`. Replacement fails when source
overlays exist, because silently dropping them would lose reviewed work; migration and three-way
merge are deliberately deferred.

Descriptions and synonyms can be corrected without changing the source snapshot:

```json
{
  "description": "Reviewed business description.",
  "synonyms": ["sales ledger", "revenue facts"]
}
```

```bash
tarel semantic edit retail-ossie \
  'model:retail/dataset:sales' \
  --input patch.json \
  --reason 'Confirmed with the data owner.'
```

The local UI exposes the same operation in edit mode. TAREL annotations stay in the existing review
surface; imported dataset and field values appear in a separate source-colored section with their
original values and overlay count.

## Embedded SDK

```python
from tarel.sdk import Tarel

tarel = Tarel("/srv/agent/.tarel")
result = tarel.semantic.import_file(
    "retail-ossie",
    graph="retail-demo",
    source="semantic-model.yaml",
)

imports = tarel.semantic.list(graph="retail-demo")
payload = tarel.view.graph("retail-demo", editable=True)
```

The browser projection contains normalized values, bindings, diagnostics, and import revisions. It
never contains the raw source snapshot.

## Deliberate limits of the experimental boundary

- The three readers cover representative fixtures, not their complete evolving specifications.
- Import and projection only; semantic-model values are not yet compiled into retrieval or context.
- Reader dispatch is internal. The core contract must survive more formats and review before a
  public adapter/plugin discovery API is stabilized.
- No source export or round-trip compatibility claim. Official examples must pass schema
  validation and tested round trips before that claim is made.

The reproducible three-format evidence and its precise scope are documented in
[Experimental three-format contract test](semantic-contract-test.md).
