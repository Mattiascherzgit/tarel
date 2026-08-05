# Workspaces, systems, areas, and zones

TAREL keeps source discovery separate from organizational scope. A `GraphDocument` remains a
technical and semantic snapshot of one discovered source. A `WorkspaceDocument` references one or
more of those graphs and adds the human-defined estate structure.

The structural hierarchy is:

```text
workspace
└── system
    └── area
        └── schema = graph + namespace
```

- A **workspace** is the local estate or project being organized.
- A **system** is a logical information system and owns one or more complete TAREL graphs.
- An **area** groups sibling schemas inside one system. A schema belongs to at most one area.
- A **schema reference** is always explicit as `GRAPH:NAMESPACE`.

A **zone is not another hierarchy level**. It is an explicit set of tables and views inside one
system. A zone may cross schemas and areas, and the same object may belong to several zones. Zone
membership is stored through the graph name and stable object ID; the CLI resolves human-readable
`GRAPH:NAMESPACE.OBJECT` references before persistence.

## CLI workflow

Create a workspace and assign existing graphs to a system:

```bash
tarel workspace create enterprise
tarel workspace system define enterprise commercial \
  --graph adventureworks_dw \
  --graph erp
```

Group schemas into areas:

```bash
tarel workspace area define enterprise commercial analytics \
  --schema adventureworks_dw:dbo
tarel workspace area define enterprise commercial operations \
  --schema erp:public
```

Define a zone that crosses both areas:

```bash
tarel workspace zone define enterprise commercial revenue \
  --object adventureworks_dw:dbo.FactInternetSales \
  --object erp:public.Orders
tarel workspace zone show enterprise commercial revenue --format json
```

`define` is desired-state based: repeating it for the same system, area, or zone replaces that
definition atomically. TAREL rejects duplicate schema ownership, unknown graphs or schemas,
unknown zone objects, and zone members whose schema has not yet been assigned to an area.
Unassigned schemas may exist while a workspace is being built incrementally.

The file-first store writes the versioned `tarel.workspace.v0.1` document to
`.tarel/workspaces/<workspace>/workspace.json`. The whole-document `WorkspaceStore` boundary allows
a future shared database adapter without changing the contract used by the CLI and SDK.

## Graph and workspace separation

TAREL keeps source graphs independent and makes the workspace a separate referencing document. A
zone therefore never owns or duplicates a graph. This supports overlapping analytical slices and
later compilation of stable system-, area-, schema-, or zone-level agent context.

## Deliberately deferred

The first contract does not infer areas or zones, use regular expressions, nest zones, grant
permissions, or alter context compilation. Multi-graph context projection, revisions for workspace
content, stale-reference repair, and LLM context-caching modes are separate follow-up slices.
