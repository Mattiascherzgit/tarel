"""Pure workspace transformations and graph-reference validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol, TypeVar

from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.workspaces.contracts import (
    Area,
    SchemaReference,
    WorkspaceDocument,
    WorkspaceFailure,
    WorkspaceSystem,
    Zone,
    ZoneMember,
    validate_workspace,
)


class _Named(Protocol):
    name: str


_NamedType = TypeVar("_NamedType", bound=_Named)


@dataclass(frozen=True, slots=True)
class ResolvedZoneObject:
    graph: str
    area: str
    namespace: str
    name: str
    label: str
    type: str
    object_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "area": self.area,
            "graph": self.graph,
            "label": self.label,
            "name": self.name,
            "namespace": self.namespace,
            "object_id": self.object_id,
            "type": self.type,
        }


@dataclass(frozen=True, slots=True)
class ResolvedZone:
    workspace: str
    system: str
    zone: str
    description: str | None
    objects: tuple[ResolvedZoneObject, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "description": self.description,
            "objects": [item.to_dict() for item in self.objects],
            "system": self.system,
            "workspace": self.workspace,
            "zone": self.zone,
        }


def create_workspace(name: str, *, description: str | None = None) -> WorkspaceDocument:
    workspace = WorkspaceDocument(name=name, description=description)
    validate_workspace(workspace)
    return workspace


def define_system(
    workspace: WorkspaceDocument,
    name: str,
    *,
    graph_names: tuple[str, ...],
    graphs: Mapping[str, GraphDocument],
    description: str | None = None,
) -> WorkspaceDocument:
    existing = _optional_system(workspace, name)
    system = WorkspaceSystem(
        name=name,
        graphs=tuple(sorted(graph_names)),
        areas=existing.areas if existing else (),
        zones=existing.zones if existing else (),
        description=description,
    )
    updated = _replace_system(workspace, system)
    validate_workspace(updated)
    validate_system_references(system, graphs)
    return updated


def define_area(
    workspace: WorkspaceDocument,
    system_name: str,
    name: str,
    *,
    schemas: tuple[SchemaReference, ...],
    graphs: Mapping[str, GraphDocument],
    description: str | None = None,
) -> WorkspaceDocument:
    system = require_system(workspace, system_name)
    area = Area(
        name=name,
        schemas=tuple(sorted(schemas, key=lambda item: (item.graph, item.namespace))),
        description=description,
    )
    updated_system = replace(
        system,
        areas=_replace_named(system.areas, area),
    )
    updated = _replace_system(workspace, updated_system)
    validate_workspace(updated)
    validate_system_references(updated_system, graphs)
    return updated


def define_zone(
    workspace: WorkspaceDocument,
    system_name: str,
    name: str,
    *,
    object_references: tuple[str, ...],
    graphs: Mapping[str, GraphDocument],
    description: str | None = None,
) -> WorkspaceDocument:
    system = require_system(workspace, system_name)
    members = tuple(
        sorted(
            (_resolve_object_reference(reference, graphs) for reference in object_references),
            key=lambda item: (item.graph, item.object_id),
        )
    )
    zone = Zone(name=name, members=members, description=description)
    updated_system = replace(system, zones=_replace_named(system.zones, zone))
    updated = _replace_system(workspace, updated_system)
    validate_workspace(updated)
    validate_system_references(updated_system, graphs)
    return updated


def parse_schema_reference(reference: str) -> SchemaReference:
    graph, separator, namespace = reference.partition(":")
    if not separator or not graph or not namespace:
        raise WorkspaceFailure(
            "invalid_schema_reference",
            f"Schema reference must use GRAPH:NAMESPACE: {reference}",
        )
    return SchemaReference(graph=graph, namespace=namespace)


def require_system(workspace: WorkspaceDocument, name: str) -> WorkspaceSystem:
    system = _optional_system(workspace, name)
    if system is None:
        raise WorkspaceFailure(
            "system_not_found",
            f"System not found in workspace {workspace.name}: {name}",
        )
    return system


def resolve_zone(
    workspace: WorkspaceDocument,
    system_name: str,
    zone_name: str,
    *,
    graphs: Mapping[str, GraphDocument],
) -> ResolvedZone:
    system = require_system(workspace, system_name)
    validate_system_references(system, graphs)
    zone = next((item for item in system.zones if item.name == zone_name), None)
    if zone is None:
        raise WorkspaceFailure(
            "zone_not_found",
            f"Zone not found in system {system_name}: {zone_name}",
        )

    schema_areas = _schema_areas(system)
    objects: list[ResolvedZoneObject] = []
    for member in sorted(zone.members, key=lambda item: (item.graph, item.object_id)):
        node = graphs[member.graph].node_by_id()[member.object_id]
        namespace = str(node.metadata["namespace"])
        objects.append(
            ResolvedZoneObject(
                graph=member.graph,
                area=schema_areas[(member.graph, namespace)],
                namespace=namespace,
                name=str(node.metadata["name"]),
                label=node.label,
                type=node.type,
                object_id=node.id,
            )
        )
    return ResolvedZone(
        workspace=workspace.name,
        system=system.name,
        zone=zone.name,
        description=zone.description,
        objects=tuple(objects),
    )


def validate_system_references(
    system: WorkspaceSystem,
    graphs: Mapping[str, GraphDocument],
) -> None:
    for graph_name in system.graphs:
        graph = graphs.get(graph_name)
        if graph is None:
            raise WorkspaceFailure("graph_not_found", f"Graph not found: {graph_name}")
        if graph.name != graph_name:
            raise WorkspaceFailure(
                "graph_name_mismatch",
                f"Loaded graph {graph.name} does not match reference {graph_name}.",
            )

    schema_areas = _schema_areas(system)
    for graph_name, namespace in schema_areas:
        graph = graphs[graph_name]
        if namespace not in _graph_namespaces(graph):
            raise WorkspaceFailure(
                "schema_not_found",
                f"Schema not found in graph {graph_name}: {namespace}",
            )

    for zone in system.zones:
        for member in zone.members:
            graph = graphs[member.graph]
            node = graph.node_by_id().get(member.object_id)
            if node is None or node.type not in {"table", "view"}:
                raise WorkspaceFailure(
                    "zone_member_not_found",
                    f"Zone {zone.name} references an unknown table or view in graph "
                    f"{member.graph}: {member.object_id}",
                )
            namespace = _node_namespace(node)
            if (member.graph, namespace) not in schema_areas:
                raise WorkspaceFailure(
                    "zone_schema_unassigned",
                    f"Assign {member.graph}:{namespace} to an area before adding "
                    f"{node.label} to zone {zone.name}.",
                )


def _resolve_object_reference(
    reference: str,
    graphs: Mapping[str, GraphDocument],
) -> ZoneMember:
    graph_name, separator, label = reference.partition(":")
    if not separator or not graph_name or not label:
        raise WorkspaceFailure(
            "invalid_object_reference",
            f"Object reference must use GRAPH:NAMESPACE.OBJECT: {reference}",
        )
    graph = graphs.get(graph_name)
    if graph is None:
        raise WorkspaceFailure("graph_not_found", f"Graph not found: {graph_name}")
    matches = [
        node for node in graph.nodes if node.type in {"table", "view"} and node.label == label
    ]
    if len(matches) != 1:
        raise WorkspaceFailure(
            "object_not_found",
            f"Table or view not found in graph {graph_name}: {label}",
        )
    return ZoneMember(graph=graph_name, object_id=matches[0].id)


def _graph_namespaces(graph: GraphDocument) -> set[str]:
    return {
        str(node.metadata["namespace"])
        for node in graph.nodes
        if node.type in {"table", "view"} and node.metadata.get("namespace") is not None
    }


def _node_namespace(node: GraphNode) -> str:
    namespace = node.metadata.get("namespace")
    if not isinstance(namespace, str) or not namespace:
        raise WorkspaceFailure(
            "invalid_graph_object",
            f"Graph object has no namespace: {node.id}",
        )
    return namespace


def _schema_areas(system: WorkspaceSystem) -> dict[tuple[str, str], str]:
    return {
        (schema.graph, schema.namespace): area.name
        for area in system.areas
        for schema in area.schemas
    }


def _optional_system(workspace: WorkspaceDocument, name: str) -> WorkspaceSystem | None:
    return next((item for item in workspace.systems if item.name == name), None)


def _replace_system(
    workspace: WorkspaceDocument,
    system: WorkspaceSystem,
) -> WorkspaceDocument:
    return replace(
        workspace,
        systems=_replace_named(workspace.systems, system),
    )


def _replace_named(
    items: tuple[_NamedType, ...],
    replacement: _NamedType,
) -> tuple[_NamedType, ...]:
    retained = [item for item in items if item.name != replacement.name]
    return tuple(sorted((*retained, replacement), key=lambda item: item.name))
