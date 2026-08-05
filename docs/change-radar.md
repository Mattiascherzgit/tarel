# Change Radar and stale claims

`tarel graph refresh NAME` compares a fresh connector observation with the current local graph. It
does not ask an LLM to interpret technical drift. The report is deterministic and, when the graph
revision changes, is stored under:

```text
.tarel/graphs/NAME/changes/BEFORE--AFTER.json
```

There are no timestamps, runtimes, credentials, samples, or volatile paths in the report.

## Classified changes

The first contract reports added and removed objects and fields, field type/nullability/key/position
changes, primary-key and object-kind changes, declared relationship changes, source-description
changes, and graph dialect/source-type changes. A possible field rename is emitted only when one
removed and one added field have the same parent, position, type, nullability, and key status. It is
always a review suggestion, never an automatic rename.

## Review behavior

A validated annotation directly affected by a semantic-risk change becomes `review_required`; its
description, evidence, provenance, and earlier human review remain present. A validated relationship
candidate whose endpoint changed is also removed from usable joins until a human validates it again.
Normal annotation and relationship validation clears the automatic change marker.

Claims on removed nodes and discarded relationship candidates cannot remain in the active technical
graph. They are therefore retained as `stale_claims` in the immutable transition report rather than
silently deleted or represented as current source observations.

Draft, deferred, and rejected annotations keep their existing state. A source change must not make an
unreviewed proposal appear human-approved.

## Workspace and context impact

Refresh projects changed object IDs and namespaces onto existing workspaces. It reports affected
systems, areas, and every overlapping zone, but does not repair or rewrite workspace membership.

`tarel context impact PACKET --graph NAME` compares the packet's selected object, field, and join IDs
with the exact persisted `BEFORE--AFTER` report. Its status is:

- `current`: packet and graph revisions are identical;
- `affected`: the exact transition touched a selected entity;
- `unaffected`: the graph changed, but the selected entities did not;
- `unknown`: no single stored transition connects the packet to the current graph.

Multi-transition impact composition and human-assisted stale workspace repair are intentionally
deferred until real shared-workspace requirements justify them.
