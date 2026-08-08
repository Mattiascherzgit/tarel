"""Deterministic in-memory graph projection for one resolved workspace scope."""

from __future__ import annotations

from collections.abc import Mapping

from tarel.graph.contracts import GraphDocument, GraphEdge, GraphNode
from tarel.workspaces.contracts import (
    WorkspaceDocument,
    WorkspaceFailure,
    WorkspaceRelationshipEndpoint,
)
from tarel.workspaces.scope import ResolvedScope


def workspace_graph_name(workspace_name: str) -> str:
    return f"workspace.{workspace_name}"


def scoped_node_id(graph_name: str, node_id: str) -> str:
    return f"scope::{graph_name}::{node_id}"


def project_workspace_scope(
    workspace: WorkspaceDocument,
    graphs: Mapping[str, GraphDocument],
    scope: ResolvedScope,
) -> GraphDocument:
    """Combine selected graph fragments without changing their persisted documents."""
    selected_objects = {(item.graph, item.object_id) for item in scope.objects}
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    selected_nodes: set[tuple[str, str]] = set()

    for graph_name in scope.graph_names:
        graph = graphs[graph_name]
        object_ids = {
            object_id
            for selected_graph, object_id in selected_objects
            if selected_graph == graph_name
        }
        for node in graph.nodes:
            parent = node.metadata.get("object_id")
            include = (
                node.type in {"table", "view"} and node.id in object_ids
            ) or (
                node.type == "field" and isinstance(parent, str) and parent in object_ids
            )
            if not include:
                continue
            selected_nodes.add((graph_name, node.id))
            metadata = dict(node.metadata)
            metadata["source_graph"] = graph_name
            metadata["source_node_id"] = node.id
            if node.type == "field" and isinstance(parent, str):
                metadata["object_id"] = scoped_node_id(graph_name, parent)
            nodes.append(
                GraphNode(
                    id=scoped_node_id(graph_name, node.id),
                    type=node.type,
                    label=(
                        f"{graph_name}:{node.label}"
                        if node.type in {"table", "view"}
                        else node.label
                    ),
                    metadata=metadata,
                    annotation=node.annotation,
                )
            )
        for edge in graph.edges:
            if (
                (graph_name, edge.source_id) in selected_nodes
                and (graph_name, edge.target_id) in selected_nodes
            ):
                edges.append(
                    GraphEdge(
                        id=f"scope::{graph_name}::edge::{edge.id}",
                        source_id=scoped_node_id(graph_name, edge.source_id),
                        target_id=scoped_node_id(graph_name, edge.target_id),
                        type=edge.type,
                        metadata={**edge.metadata, "source_graph": graph_name},
                    )
                )

    for relationship in workspace.relationships:
        if (
            (relationship.source.graph, relationship.source.object_id) not in selected_objects
            or (relationship.target.graph, relationship.target.object_id) not in selected_objects
        ):
            continue
        _field_id(graphs, relationship.source)
        _field_id(graphs, relationship.target)
        edges.append(
            GraphEdge(
                id=f"workspace::{relationship.id}",
                source_id=scoped_node_id(
                    relationship.source.graph,
                    relationship.source.object_id,
                ),
                target_id=scoped_node_id(
                    relationship.target.graph,
                    relationship.target.object_id,
                ),
                type="relationship_candidate",
                metadata={
                    "candidate_kind": "cross_graph_join",
                    "from_field": relationship.source.fields[0],
                    "origin": relationship.origin,
                    "reason": relationship.reason,
                    "state": relationship.state,
                    "to_field": relationship.target.fields[0],
                    "workspace": workspace.name,
                },
            )
        )

    return GraphDocument(
        name=workspace_graph_name(workspace.name),
        connector="workspace",
        source_type="workspace",
        catalog=workspace.name,
        dialect=None,
        nodes=tuple(sorted(nodes, key=lambda item: item.id)),
        edges=tuple(sorted(edges, key=lambda item: item.id)),
    )


def _field_id(
    graphs: Mapping[str, GraphDocument],
    endpoint: WorkspaceRelationshipEndpoint,
) -> str:
    graph_name = endpoint.graph
    object_id = endpoint.object_id
    fields = endpoint.fields
    graph = graphs[graph_name]
    matches = [
        node.id
        for node in graph.nodes
        if node.type == "field"
        and node.metadata.get("object_id") == object_id
        and node.label.casefold() == fields[0].casefold()
    ]
    if len(matches) != 1:
        raise WorkspaceFailure(
            "relationship_field_not_found",
            f"Could not resolve workspace relationship field in {graph_name}: {fields[0]}",
        )
    return matches[0]
