"""Resolve deterministic object scopes across workspace graphs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from tarel.graph.contracts import GraphDocument
from tarel.workspaces.contracts import WorkspaceDocument, WorkspaceFailure, WorkspaceSystem
from tarel.workspaces.core import validate_system_references


@dataclass(frozen=True, slots=True)
class ScopeSelection:
    systems: tuple[str, ...] = ()
    graphs: tuple[str, ...] = ()
    areas: tuple[str, ...] = ()
    schemas: tuple[str, ...] = ()
    zones: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "areas": sorted(set(self.areas)),
            "graphs": sorted(set(self.graphs)),
            "schemas": sorted(set(self.schemas)),
            "systems": sorted(set(self.systems)),
            "zones": sorted(set(self.zones)),
        }


@dataclass(frozen=True, slots=True)
class ResolvedScopeObject:
    graph: str
    system: str
    area: str | None
    namespace: str
    object_id: str
    label: str
    type: str
    zones: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "area": self.area,
            "graph": self.graph,
            "label": self.label,
            "namespace": self.namespace,
            "object_id": self.object_id,
            "system": self.system,
            "type": self.type,
            "zones": list(self.zones),
        }


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    workspace: str
    selection: ScopeSelection
    graph_names: tuple[str, ...]
    objects: tuple[ResolvedScopeObject, ...]
    scope_hash: str

    def to_dict(self) -> dict[str, object]:
        return {
            "graphs": list(self.graph_names),
            "objects": [item.to_dict() for item in self.objects],
            "scope_hash": self.scope_hash,
            "selection": self.selection.to_dict(),
            "workspace": self.workspace,
        }


def resolve_scope(
    workspace: WorkspaceDocument,
    graphs: Mapping[str, GraphDocument],
    selection: ScopeSelection | None = None,
) -> ResolvedScope:
    """Resolve one workspace selection without querying or mutating its graphs."""
    selection = selection or ScopeSelection()
    systems = _selected_systems(workspace, selection.systems)
    for system in systems:
        validate_system_references(system, graphs)

    graph_names = {name for system in systems for name in system.graphs}
    selected_graphs = set(selection.graphs)
    unknown_graphs = selected_graphs - graph_names
    if unknown_graphs:
        raise WorkspaceFailure(
            "graph_outside_scope",
            f"Graph is outside the selected systems: {sorted(unknown_graphs)[0]}",
        )
    if selected_graphs:
        graph_names &= selected_graphs

    area_keys = _selected_named_members(systems, selection.areas, member_type="area")
    zone_keys = _selected_named_members(systems, selection.zones, member_type="zone")
    schema_keys = _selected_schemas(graphs, graph_names, selection.schemas)

    system_by_graph = {
        graph_name: system.name for system in systems for graph_name in system.graphs
    }
    area_by_schema = {
        (schema.graph, schema.namespace): area.name
        for system in systems
        for area in system.areas
        for schema in area.schemas
    }
    area_schemas = {
        (schema.graph, schema.namespace)
        for system in systems
        for area in system.areas
        if not area_keys or (system.name, area.name) in area_keys
        for schema in area.schemas
    }
    zones_by_member: dict[tuple[str, str], list[str]] = {}
    selected_zone_members: set[tuple[str, str]] = set()
    for system in systems:
        for zone in system.zones:
            qualified_name = f"{system.name}:{zone.name}"
            for member in zone.members:
                zones_by_member.setdefault((member.graph, member.object_id), []).append(
                    qualified_name
                )
                if zone_keys and (system.name, zone.name) in zone_keys:
                    selected_zone_members.add((member.graph, member.object_id))

    objects: list[ResolvedScopeObject] = []
    for graph_name in sorted(graph_names):
        graph = graphs[graph_name]
        for node in graph.nodes:
            if node.type not in {"table", "view"}:
                continue
            namespace = str(node.metadata.get("namespace") or "")
            schema_key = (graph_name, namespace)
            member_key = (graph_name, node.id)
            if area_keys and schema_key not in area_schemas:
                continue
            if schema_keys and schema_key not in schema_keys:
                continue
            if zone_keys and member_key not in selected_zone_members:
                continue
            objects.append(
                ResolvedScopeObject(
                    graph=graph_name,
                    system=system_by_graph[graph_name],
                    area=area_by_schema.get(schema_key),
                    namespace=namespace,
                    object_id=node.id,
                    label=node.label,
                    type=node.type,
                    zones=tuple(sorted(zones_by_member.get(member_key, ()))),
                )
            )

    resolved_graphs = tuple(
        sorted({item.graph for item in objects} or graph_names)
    )
    ordered_objects = tuple(
        sorted(objects, key=lambda item: (item.system, item.area or "", item.graph, item.label))
    )
    payload = {
        "graphs": list(resolved_graphs),
        "objects": [item.to_dict() for item in ordered_objects],
        "selection": selection.to_dict(),
        "workspace": workspace.name,
    }
    scope_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return ResolvedScope(
        workspace=workspace.name,
        selection=selection,
        graph_names=resolved_graphs,
        objects=ordered_objects,
        scope_hash=scope_hash,
    )


def _selected_systems(
    workspace: WorkspaceDocument,
    selected_names: tuple[str, ...],
) -> tuple[WorkspaceSystem, ...]:
    by_name = {item.name: item for item in workspace.systems}
    requested = set(selected_names)
    unknown = requested - set(by_name)
    if unknown:
        raise WorkspaceFailure("system_not_found", f"System not found: {sorted(unknown)[0]}")
    return tuple(
        by_name[name] for name in sorted(requested or set(by_name))
    )


def _selected_named_members(
    systems: tuple[WorkspaceSystem, ...],
    selectors: tuple[str, ...],
    *,
    member_type: str,
) -> set[tuple[str, str]]:
    if not selectors:
        return set()
    matches: set[tuple[str, str]] = set()
    for selector in selectors:
        system_name, separator, name = selector.partition(":")
        candidates = [
            (system.name, item.name)
            for system in systems
            for item in getattr(system, f"{member_type}s")
            if item.name == (name if separator else selector)
            and (not separator or system.name == system_name)
        ]
        if not candidates:
            raise WorkspaceFailure(
                f"{member_type}_not_found",
                f"{member_type.title()} not found in selected systems: {selector}",
            )
        if len(candidates) > 1:
            raise WorkspaceFailure(
                "ambiguous_scope_selector",
                f"Qualify ambiguous {member_type} as SYSTEM:{member_type.upper()}: {selector}",
            )
        matches.add(candidates[0])
    return matches


def _selected_schemas(
    graphs: Mapping[str, GraphDocument],
    graph_names: set[str],
    selectors: tuple[str, ...],
) -> set[tuple[str, str]]:
    selected: set[tuple[str, str]] = set()
    for selector in selectors:
        graph_name, separator, namespace = selector.partition(":")
        if not separator or not graph_name or not namespace:
            raise WorkspaceFailure(
                "invalid_schema_reference",
                f"Schema reference must use GRAPH:NAMESPACE: {selector}",
            )
        if graph_name not in graph_names:
            raise WorkspaceFailure(
                "graph_outside_scope",
                f"Schema graph is outside the selected systems: {graph_name}",
            )
        known = {
            str(node.metadata.get("namespace") or "")
            for node in graphs[graph_name].nodes
            if node.type in {"table", "view"}
        }
        if namespace not in known:
            raise WorkspaceFailure(
                "schema_not_found",
                f"Schema not found in graph {graph_name}: {namespace}",
            )
        selected.add((graph_name, namespace))
    return selected
