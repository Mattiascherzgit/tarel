# Entity-resolution candidates

TAREL's current public SDK cannot yet represent a fully auditable entity-resolution candidate.
The existing `relationship_candidate` model is deliberately narrower: it can bind graph fields,
store aggregate transformed-join evidence, carry confidence and review state, and expose only
human-validated relationships to normal context expansion. It must not be repurposed to imply that
two records or entities are identical.

## Gap assessment

| Requirement | Current public model |
| --- | --- |
| Participating objects and fields | Available on relationship candidates |
| Fixed-segment join transformation | Available with aggregate coverage and overlap evidence |
| Draft, validated, and rejected review states | Available for relationships |
| General normalization or matching rule | Missing |
| Producing agent and runtime-run provenance | Missing as a candidate invariant |
| Positive evidence and counterexamples | Missing as a bounded ER evidence model |
| Coverage, collision rate, and confidence together | Collision rate and general ER coverage are missing |
| Candidate/reviewed ER lifecycle | Missing as a distinct semantic contract |
| Confirmed-only versus explicit-candidate retrieval | Missing for entity resolution |

The safe behavior today is therefore to keep an uncertain ER rule outside TAREL's confirmed graph.
A caller may retain its own observation, but must not write it as a validated relationship. This is
why a rule inferred from a few MusicBrainz samples cannot silently enter normal agent context.

## Small contract proposal for human review

Before implementation, the Core contract should be reviewed. A minimal, separate experimental
input could contain:

```json
{
  "contract_version": "tarel.entity-resolution-candidate-input.v0.1",
  "candidate_id": "artist-credit-normalized-name-v1",
  "graph": {"name": "music", "revision": "<sha256>"},
  "provenance": {"run_id": "agent-run-42", "producer": "v2-agent"},
  "inputs": [
    {"object_id": "<node-id>", "field_ids": ["<field-node-id>"]},
    {"object_id": "<node-id>", "field_ids": ["<field-node-id>"]}
  ],
  "rule": {
    "kind": "normalized_exact",
    "operations": ["unicode_nfkc", "trim", "casefold"]
  },
  "evidence": {
    "evaluated_count": 1000,
    "matched_count": 720,
    "coverage": 0.72,
    "collision_rate": 0.03,
    "confidence": 0.61,
    "counterexample_count": 14,
    "counterexample_hashes": ["<sha256>"]
  },
  "status": "candidate",
  "reviews": []
}
```

Raw samples, original counterexample values, query text, secrets, and local paths must remain
outside the artifact. Hashes are optional correlation evidence, not proof of correctness. Metrics
must state their evaluated population; they must never be extrapolated silently from a small
sample.

The proposed lifecycle is `draft` → `candidate` → `reviewed`, with `rejected` possible from any
reviewable state. A human review entry records the decision and a bounded reason. Default retrieval
would be `confirmed_only`, returning only reviewed candidates with an explicit approval decision.
An `include_candidates` mode would be opt-in and label every unconfirmed rule as a hypothesis; it
must never turn it into a graph relationship or an executable normalization rule.

Open architecture decisions are the exact rule vocabulary, whether reviewed ER rules become a
separate reusable object, how metric populations are identified without leaking data, and which
human identity reference is safe to persist. Until those decisions are accepted, this document is
a proposal, not a public SDK promise.
