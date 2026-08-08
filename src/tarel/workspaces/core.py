"""Pure workspace transformations and graph-reference validation."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Protocol, TypeVar

from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.relationships.core import RelationshipFailure, resolve_field
from tarel.workspaces.contracts import (
    Area,
    SchemaReference,
    WorkspaceDocument,
    WorkspaceFailure,
    WorkspaceRelationship,
    WorkspaceRelationshipEndpoint,
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


def add_workspace_relationship(
    workspace: WorkspaceDocument,
    *,
    source_reference: str,
    target_reference: str,
    graphs: Mapping[str, GraphDocument],
    reason: str,
    validated: bool = False,
) -> tuple[WorkspaceDocument, WorkspaceRelationship]:
    """Persist one explicit join whose endpoints may belong to different graphs."""
    if not reason.strip():
        raise WorkspaceFailure(
            "missing_relationship_reason",
            "A workspace relationship requires a non-empty reason.",
        )
    source = _resolve_workspace_field(source_reference, graphs)
    target = _resolve_workspace_field(target_reference, graphs)
    if source == target:
        raise WorkspaceFailure(
            "invalid_relationship_pair",
            "Workspace relationship endpoints must be different fields.",
        )
    pair_key = frozenset((_endpoint_key(source), _endpoint_key(target)))
    if any(
        frozenset((_endpoint_key(item.source), _endpoint_key(item.target))) == pair_key
        for item in workspace.relationships
    ):
        raise WorkspaceFailure(
            "relationship_exists",
            "A workspace relationship already exists for these fields.",
        )
    digest = hashlib.sha256(
        "\n".join(sorted(pair_key)).encode("utf-8")
    ).hexdigest()[:20]
    relationship = WorkspaceRelationship(
        id=f"rel-{digest}",
        source=source,
        target=target,
        state="validated" if validated else "draft",
        origin="human",
        reason=reason.strip(),
    )
    updated = replace(
        workspace,
        relationships=tuple(
            sorted((*workspace.relationships, relationship), key=lambda item: item.id)
        ),
    )
    validate_workspace(updated)
    validate_workspace_references(updated, graphs)
    return updated, relationship


def decide_workspace_relationship(
    workspace: WorkspaceDocument,
    relationship_id: str,
    *,
    state: str,
    reason: str,
) -> tuple[WorkspaceDocument, WorkspaceRelationship]:
    if state not in {"validated", "rejected"}:
        raise WorkspaceFailure(
            "invalid_relationship_state",
            f"Unsupported workspace relationship state: {state}",
        )
    if not reason.strip():
        raise WorkspaceFailure(
            "missing_relationship_reason",
            "A workspace relationship decision requires a non-empty reason.",
        )
    selected = next(
        (item for item in workspace.relationships if item.id == relationship_id),
        None,
    )
    if selected is None:
        raise WorkspaceFailure(
            "relationship_not_found",
            f"Workspace relationship not found: {relationship_id}",
        )
    updated_relationship = replace(selected, state=state, reason=reason.strip())
    updated = replace(
        workspace,
        relationships=tuple(
            updated_relationship if item.id == relationship_id else item
            for item in workspace.relationships
        ),
    )
    validate_workspace(updated)
    return updated, updated_relationship


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


def validate_workspace_references(
    workspace: WorkspaceDocument,
    graphs: Mapping[str, GraphDocument],
) -> None:
    for system in workspace.systems:
        validate_system_references(system, graphs)
    for relationship in workspace.relationships:
        for endpoint in (relationship.source, relationship.target):
            graph = graphs.get(endpoint.graph)
            if graph is None:
                raise WorkspaceFailure(
                    "graph_not_found",
                    f"Graph not found: {endpoint.graph}",
                )
            node = graph.node_by_id().get(endpoint.object_id)
            if node is None or node.type not in {"table", "view"}:
                raise WorkspaceFailure(
                    "relationship_object_not_found",
                    f"Workspace relationship {relationship.id} references an unknown object "
                    f"in {endpoint.graph}: {endpoint.object_id}",
                )
            available = {
                field.label.casefold()
                for field in graph.nodes
                if field.type == "field" and field.metadata.get("object_id") == node.id
            }
            for field_name in endpoint.fields:
                if field_name.casefold() not in available:
                    raise WorkspaceFailure(
                        "relationship_field_not_found",
                        f"Workspace relationship {relationship.id} references an unknown field "
                        f"on {endpoint.graph}:{node.label}: {field_name}",
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


def _resolve_workspace_field(
    reference: str,
    graphs: Mapping[str, GraphDocument],
) -> WorkspaceRelationshipEndpoint:
    graph_name, separator, field_reference = reference.partition(":")
    if not separator or not graph_name or not field_reference:
        raise WorkspaceFailure(
            "invalid_field_reference",
            f"Workspace field reference must use GRAPH:NAMESPACE.OBJECT.FIELD: {reference}",
        )
    graph = graphs.get(graph_name)
    if graph is None:
        raise WorkspaceFailure("graph_not_found", f"Graph not found: {graph_name}")
    try:
        resolved = resolve_field(graph, field_reference)
    except RelationshipFailure as exc:
        raise WorkspaceFailure(exc.code, str(exc)) from exc
    return WorkspaceRelationshipEndpoint(
        graph=graph_name,
        object_id=resolved.object_node.id,
        fields=(resolved.field_node.label,),
    )


def _endpoint_key(endpoint: WorkspaceRelationshipEndpoint) -> str:
    return "\x1f".join((endpoint.graph, endpoint.object_id, *endpoint.fields))


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
