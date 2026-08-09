"""Build and validate demand-driven focus snapshots from upstream traces."""

from __future__ import annotations

import hashlib
import json

from tarel.focus.contracts import (
    FocusDocument,
    FocusFailure,
    FocusHop,
    FocusMember,
    FocusSource,
    focus_hop_key,
    focus_member_key,
    validate_focus,
)
from tarel.graph.contracts import GraphDocument
from tarel.lineage.contracts import LineageDocument
from tarel.lineage.revision import lineage_revision
from tarel.lineage.traversal import LineageReference, UpstreamTrace


def build_focus(
    name: str,
    trace: UpstreamTrace,
    *,
    lineages: tuple[LineageDocument, ...],
    graphs: tuple[GraphDocument, ...],
    states: frozenset[str],
    max_hops: int,
) -> FocusDocument:
    origins = {item.id for item in trace.origins}
    members: dict[str, FocusMember] = {}

    def add_member(item: LineageReference, depth: int, reason: str) -> None:
        existing = members.get(item.id)
        reasons = tuple(sorted({*(existing.reasons if existing else ()), reason}))
        candidate = FocusMember(
            id=item.id,
            reference=item.reference,
            name=item.name,
            kind=item.kind,
            source=item.source,
            depth=min(depth, existing.depth) if existing else depth,
            reasons=reasons,
            origin=item.id in origins,
            annotation_state=item.annotation_state,
        )
        members[candidate.id] = candidate

    add_member(trace.start, 0, "seed")
    for hop in trace.hops:
        add_member(hop.source, hop.depth, f"upstream:{hop.relation}")
        add_member(hop.target, max(hop.depth - 1, 0), f"downstream:{hop.relation}")

    document = FocusDocument(
        name=name,
        seed=trace.query,
        seed_id=trace.start.id,
        max_hops=max_hops,
        states=tuple(sorted(states)),
        sources=tuple(
            sorted(
                (
                    *(
                        FocusSource("graph", item.name, focus_graph_revision(item))
                        for item in graphs
                    ),
                    *(
                        FocusSource("lineage", item.name, lineage_revision(item))
                        for item in lineages
                    ),
                ),
                key=lambda item: (item.kind, item.name),
            )
        ),
        members=tuple(sorted(members.values(), key=focus_member_key)),
        hops=tuple(
            sorted(
                (
                    FocusHop(
                        id=item.id,
                        depth=item.depth,
                        source_id=item.source.id,
                        target_id=item.target.id,
                        relation=item.relation,
                        state=item.state,
                        lineage=item.lineage,
                    )
                    for item in trace.hops
                ),
                key=focus_hop_key,
            )
        ),
        warnings=trace.warnings,
        truncated=trace.truncated,
    )
    validate_focus(document)
    return document


def require_current_focus(
    focus: FocusDocument,
    *,
    lineages: dict[str, LineageDocument],
    graphs: dict[str, GraphDocument],
) -> None:
    current = {
        **{("graph", name): focus_graph_revision(item) for name, item in graphs.items()},
        **{("lineage", name): lineage_revision(item) for name, item in lineages.items()},
    }
    expected = {(item.kind, item.name): item.revision for item in focus.sources}
    missing = sorted(set(expected) - set(current))
    changed = sorted(key for key in set(expected) & set(current) if expected[key] != current[key])
    if missing or changed:
        details = ", ".join(f"{kind}:{name}" for kind, name in (*missing, *changed))
        raise FocusFailure(
            "focus_stale",
            f"Focus {focus.name} is stale; rebuild it after source changes: {details}",
        )


def graph_object_ids(focus: FocusDocument, graph_name: str) -> frozenset[str]:
    prefix = f"graph:{graph_name}:"
    return frozenset(
        item.id.removeprefix(prefix)
        for item in focus.members
        if item.source == f"graph:{graph_name}"
        and item.kind in {"table", "view"}
        and item.id.startswith(prefix)
    )


def focus_graph_revision(graph: GraphDocument) -> str:
    """Hash only graph structure that can affect focus resolution or expansion."""
    payload = {
        "catalog": graph.catalog,
        "edges": [
            {
                "id": item.id,
                "source_id": item.source_id,
                "target_id": item.target_id,
                "type": item.type,
            }
            for item in graph.edges
            if item.type == "foreign_key"
        ],
        "name": graph.name,
        "nodes": [
            {
                "id": item.id,
                "label": item.label,
                "object_id": item.metadata.get("object_id"),
                "type": item.type,
            }
            for item in graph.nodes
            if item.type in {"field", "table", "view"}
        ],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def expand_graph_objects_one_hop(
    graph: GraphDocument,
    object_ids: frozenset[str],
) -> frozenset[str]:
    objects = {item.id for item in graph.nodes if item.type in {"table", "view"}}
    nodes = graph.node_by_id()

    def owning_object(node_id: str) -> str | None:
        node = nodes.get(node_id)
        if node is None:
            return None
        if node.id in objects:
            return node.id
        value = node.metadata.get("object_id")
        return value if isinstance(value, str) and value in objects else None

    expanded = set(object_ids)
    for edge in graph.edges:
        if edge.type != "foreign_key":
            continue
        source = owning_object(edge.source_id)
        target = owning_object(edge.target_id)
        if source in object_ids and target is not None:
            expanded.add(target)
        if target in object_ids and source is not None:
            expanded.add(source)
    return frozenset(expanded)
