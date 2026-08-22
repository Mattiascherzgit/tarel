# Entity-resolution candidates

TAREL has an experimental, graph-bound contract for entity-resolution hypotheses. It keeps
identity matching separate from technical joins: an `entity_resolution_candidate` says that two
fields may support a record-identity rule, not that they are a foreign key or an executable join.

Candidates are available to CLI and SDK callers before human review. Every unreviewed match is
labelled `exploratory_only`, requires runtime validation, and carries its measured evidence. TAREL
does not execute the rule, query a source, or promote a confidence score into an approval.

## Bounded contract

The contract is `tarel.entity-resolution-candidate.v0.1`. One candidate contains exact field node
IDs from one graph revision, a small declarative rule, aggregate evidence, producing-run
provenance, and optional human review:

```json
{
  "contract_version": "tarel.entity-resolution-candidate.v0.1",
  "id": "artist-credit-normalized-name-v1",
  "graph": {"name": "music", "revision": "<sha256>"},
  "source_field_id": "<field-node-id>",
  "target_field_id": "<field-node-id>",
  "rule": {
    "kind": "normalized_exact",
    "operations": ["unicode_nfkc", "trim", "casefold"]
  },
  "evidence": {
    "level": "sample_tested",
    "evaluated_count": 1000,
    "matched_count": 720,
    "collision_count": 18,
    "counterexample_count": 14,
    "coverage": 0.72,
    "collision_rate": 0.025,
    "confidence": 0.61
  },
  "provenance": {"run_id": "agent-run-42", "producer": "v2-agent"},
  "state": "candidate",
  "review": null
}
```

`coverage` must equal `matched_count / evaluated_count`; `collision_rate` must equal
`collision_count / matched_count`. The contract rejects inconsistent values rather than accepting
a persuasive score without its denominator. Evidence levels are `proposed`, `sample_tested`, and
`population_tested`. A proposed rule must report zero evaluated rows and zero measured rates.

The initial rule vocabulary is intentionally small: `normalized_exact` with an ordered, unique
combination of `unicode_nfkc`, `trim`, `casefold`, `collapse_whitespace`, and
`strip_punctuation`. Arbitrary code, regular expressions, SQL, and model-generated functions are
not accepted. The operations are applied to both endpoints in their declared order; asymmetric
parsing needs a future reviewed contract rather than an implicit convention.

Raw samples, record values, original counterexamples, query text, secrets, and local paths are
outside the artifact. Stored files use mode `0600` below `.tarel/entity-resolution/`.

## CLI

```bash
tarel entity import --source sanitized-candidate.json --format json

tarel entity find music \
  --source-field mb.ArtistCredit.Name \
  --target-field mb.Artist.Name \
  --mode confirmed_then_candidates \
  --format json

tarel entity list --graph music --format json
tarel entity show artist-credit-normalized-name-v1 --format json

tarel entity review artist-credit-normalized-name-v1 \
  --decision approve \
  --reason "Population and collision evidence reviewed." \
  --revision <candidate-revision> \
  --format json
```

Imports are create-only. Repeating an identical import is idempotent; different content under an
existing ID fails. A review uses optimistic revision checking and changes an unreviewed candidate
once to `reviewed` or `rejected`. Rejected candidates remain available through `list` and `show`
for audit but never appear in normal retrieval.

## SDK

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

for match in matches:
    if match.requires_runtime_validation:
        # V2 may probe the declared rule with a controlled source tool.
        # TAREL itself never executes it.
        pass
```

CLI and SDK call the same application use cases. The public SDK also exports the typed candidate,
rule, evidence, provenance, and match values.

## Retrieval policy

- `confirmed_only` returns only human-approved rules.
- `include_candidates` returns approved and unreviewed candidates.
- `confirmed_then_candidates` returns approved rules for a field pair when present; otherwise it
  offers that pair's unreviewed candidates as explicit hypotheses.

The last mode is the default for `find`. It lets an agent try the best available hypothesis when no
confirmed rule exists, while `usage`, `requires_runtime_validation`, `warning`, evidence level,
counts, rates, confidence, and review state remain visible in every match.

Only candidates bound to the current graph revision are returned by `find` or projected into the
browser. `list` and `show` retain older candidates for audit. This prevents a rule from silently
surviving changed field topology.

## Graph and browser projection

The canonical candidate stays in its separate artifact. TAREL projects current retrieval matches
onto the information-space graph as `entity_resolution_candidate` edges without modifying the
stored `GraphDocument` or its revision. Normal relationship expansion and context joins therefore
cannot consume them.

The browser lists candidate evidence in each connected table inspector. A disabled-by-default
**Entity candidates** toggle renders unreviewed candidates as dashed violet edges and reviewed
rules as solid violet edges. The projection includes aggregate evidence and provenance, never raw
records.

This first slice does not cluster records, execute matching, append multiple evaluation snapshots,
or inject entity hypotheses into ordinary context packets. V2 or another controlled caller owns
runtime probing and may import a later candidate version with a new ID when evidence changes.
