"""Pure browser projections over TAREL graph and workspace contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable

from tarel.graph.contracts import GraphDocument, GraphEdge, GraphNode
from tarel.graph.revision import graph_revision
from tarel.lineage.contracts import LineageDocument
from tarel.lineage.revision import lineage_revision
from tarel.workspaces.contracts import WorkspaceDocument
from tarel.workspaces.scope import ResolvedScope


def browser_graph(
    graph: GraphDocument,
    *,
    workspaces: Iterable[WorkspaceDocument] = (),
    lineage_names: tuple[str, ...] = (),
    editable: bool = False,
    lineage_documents: Iterable[LineageDocument] = (),
) -> dict[str, object]:
    return _browser_payload(
        (graph,),
        workspaces=tuple(workspaces),
        lineage_names=lineage_names,
        editable=editable,
        lineage_documents=tuple(lineage_documents),
    )


def browser_workspace(
    graphs: Iterable[GraphDocument],
    scope: ResolvedScope,
    *,
    workspace: WorkspaceDocument,
    editable: bool = False,
    lineage_documents: Iterable[LineageDocument] = (),
) -> dict[str, object]:
    payload = _browser_payload(
        tuple(graphs),
        workspaces=(workspace,),
        editable=editable,
        lineage_documents=tuple(lineage_documents),
        scope=scope,
    )
    objects = payload["objects"]
    edges = payload["edges"]
    if isinstance(objects, list) and isinstance(edges, list):
        edges.extend(_workspace_relationship_payloads(workspace, objects))
        edges.sort(key=lambda item: str(item["id"]))
    return payload


def _browser_payload(
    graphs: tuple[GraphDocument, ...],
    *,
    workspaces: tuple[WorkspaceDocument, ...],
    editable: bool,
    lineage_documents: tuple[LineageDocument, ...],
    lineage_names: tuple[str, ...] = (),
    scope: ResolvedScope | None = None,
) -> dict[str, object]:
    if not graphs:
        raise ValueError("Browser projection requires at least one graph.")
    selected = (
        {(item.graph, item.object_id): item for item in scope.objects}
        if scope is not None
        else None
    )
    object_payloads: list[dict[str, object]] = []
    edge_payloads: list[dict[str, object]] = []
    for graph in sorted(graphs, key=lambda item: item.name):
        nodes = graph.node_by_id()
        objects = [
            node
            for node in graph.nodes
            if node.type in {"table", "view"}
            and (selected is None or (graph.name, node.id) in selected)
        ]
        object_ids = {node.id for node in objects}
        fields_by_object: dict[str, list[GraphNode]] = {node.id: [] for node in objects}
        for node in graph.nodes:
            if node.type == "field":
                parent = str(node.metadata.get("object_id") or "")
                if parent in fields_by_object:
                    fields_by_object[parent].append(node)
        object_payloads.extend(
            _object_payload(
                node,
                fields_by_object[node.id],
                graph=graph,
                scope_object=selected.get((graph.name, node.id)) if selected else None,
            )
            for node in sorted(objects, key=lambda item: (item.label.casefold(), item.id))
        )
        edge_payloads.extend(
            payload
            for edge in sorted(graph.edges, key=lambda item: item.id)
            if edge.source_id in object_ids and edge.target_id in object_ids
            if (payload := _edge_payload(edge, nodes, graph.name)) is not None
        )

    documents = tuple(lineage_documents)
    selected_lineages = tuple(item.name for item in documents) or lineage_names
    revisions = {graph.name: graph_revision(graph) for graph in graphs}
    revision = (
        next(iter(revisions.values()))
        if len(revisions) == 1
        else _combined_revision(revisions)
    )
    graph_payloads = [
        {
            "catalog": graph.catalog,
            "connector": graph.connector,
            "dialect": graph.dialect,
            "name": graph.name,
            "revision": revisions[graph.name],
            "source_type": graph.source_type,
        }
        for graph in sorted(graphs, key=lambda item: item.name)
    ]
    first = sorted(graphs, key=lambda item: item.name)[0]
    return {
        "catalog": first.catalog if len(graphs) == 1 else None,
        "connector": first.connector if len(graphs) == 1 else None,
        "dialect": first.dialect if len(graphs) == 1 else None,
        "editable": editable,
        "edges": sorted(edge_payloads, key=lambda item: str(item["id"])),
        "graph": first.name,
        "graphs": graph_payloads,
        "lineage_documents": browser_lineages(documents),
        "lineage_flows": browser_lineage_flows(documents, object_payloads),
        "lineages": list(selected_lineages),
        "objects": object_payloads,
        "review": _review_queue(object_payloads),
        "revision": revision,
        "revisions": revisions,
        "scope": scope.to_dict() if scope else None,
        "source_type": first.source_type if len(graphs) == 1 else "workspace",
        "title": scope.workspace if scope else first.name,
        "workspaces": [
            _workspace_payload_for_graphs(workspace, {graph.name for graph in graphs})
            for workspace in sorted(workspaces, key=lambda item: item.name)
            if any(
                graph.name in system.graphs
                for graph in graphs
                for system in workspace.systems
            )
        ],
    }


def browser_lineages(documents: Iterable[LineageDocument]) -> list[dict[str, object]]:
    payload = []
    for document in sorted(documents, key=lambda item: item.name):
        definitions = document.definition_by_id()
        descriptions = {item.definition_id: item.summary for item in document.analyses}
        jobs = [
            {
                "description": descriptions.get(item.id),
                "id": item.id,
                "kind": item.kind,
                "language": item.language,
                "name": item.name,
                "qualified_name": item.qualified_name,
                "source_reference": item.source_reference,
            }
            for item in sorted(
                document.definitions,
                key=lambda item: item.qualified_name.casefold(),
            )
        ]
        hops = []
        for unit in sorted(document.write_units, key=lambda item: item.id):
            definition = definitions[unit.definition_id]
            for source in unit.sources:
                hops.append(
                    {
                        "evidence": source.evidence.to_dict(),
                        "item_id": unit.id,
                        "job": definition.qualified_name,
                        "operation": unit.operation,
                        "reviews": [item.to_dict() for item in unit.reviews],
                        "role": source.role,
                        "source": source.target,
                        "state": unit.state,
                        "target": unit.target,
                    }
                )
        payload.append(
            {
                "hops": hops,
                "jobs": jobs,
                "manual": document.source_kind == "manual",
                "name": document.name,
                "revision": lineage_revision(document),
                "source_kind": document.source_kind,
            }
        )
    return payload


def browser_lineage_flows(
    documents: Iterable[LineageDocument],
    objects: list[dict[str, object]],
) -> dict[str, object]:
    aliases: dict[str, list[str]] = {}
    for item in objects:
        for alias in (
            str(item["id"]),
            str(item["object_id"]),
            str(item["label"]),
            str(item["reference"]),
        ):
            aliases.setdefault(_normalize_reference(alias), []).append(str(item["id"]))

    nodes: dict[str, dict[str, object]] = {}
    edges: dict[str, dict[str, object]] = {}

    def reference_node(reference: str) -> str:
        matches = sorted(set(aliases.get(_normalize_reference(reference), ())))
        if len(matches) == 1:
            return matches[0]
        identifier = "lineage-ref::" + hashlib.sha256(
            _normalize_reference(reference).encode()
        ).hexdigest()[:20]
        nodes.setdefault(
            identifier,
            {
                "graph": None,
                "id": identifier,
                "kind": "asset",
                "label": _display_reference(reference),
                "reference": reference,
            },
        )
        return identifier

    for document in sorted(documents, key=lambda item: item.name):
        definitions = document.definition_by_id()
        job_ids: dict[str, str] = {}
        for definition in document.definitions:
            identifier = f"lineage-job::{document.name}::{definition.id}"
            job_ids[definition.id] = identifier
            nodes[identifier] = {
                "description": next(
                    (
                        analysis.summary
                        for analysis in document.analyses
                        if analysis.definition_id == definition.id
                    ),
                    None,
                ),
                "graph": None,
                "id": identifier,
                "kind": definition.kind,
                "label": definition.name,
                "lineage": document.name,
                "reference": definition.qualified_name,
            }
            for alias in (definition.id, definition.name, definition.qualified_name):
                aliases.setdefault(_normalize_reference(alias), []).append(identifier)

        for claim in sorted(document.claims, key=lambda item: item.id):
            if claim.operation != "read":
                continue
            source = reference_node(claim.target)
            target = job_ids[claim.definition_id]
            identifier = f"lineage-edge::{document.name}::{claim.id}"
            edges[identifier] = {
                "id": identifier,
                "job": definitions[claim.definition_id].qualified_name,
                "lineage": document.name,
                "relation": "read",
                "source": source,
                "state": claim.state,
                "target": target,
            }

        for unit in sorted(document.write_units, key=lambda item: item.id):
            job = job_ids[unit.definition_id]
            for position, source in enumerate(unit.sources, start=1):
                identifier = f"lineage-edge::{document.name}::{unit.id}::source::{position}"
                edges[identifier] = {
                    "id": identifier,
                    "job": definitions[unit.definition_id].qualified_name,
                    "lineage": document.name,
                    "relation": source.role,
                    "source": reference_node(source.target),
                    "state": unit.state,
                    "target": job,
                }
            identifier = f"lineage-edge::{document.name}::{unit.id}::target"
            edges[identifier] = {
                "id": identifier,
                "job": definitions[unit.definition_id].qualified_name,
                "lineage": document.name,
                "relation": unit.operation,
                "source": job,
                "state": unit.state,
                "target": reference_node(unit.target),
            }

        steps = {item.id: item for item in document.steps}
        for step in document.steps:
            target = job_ids[step.definition_id]
            for dependency in step.depends_on:
                source_step = steps.get(dependency)
                if source_step is None:
                    continue
                identifier = f"process-edge::{document.name}::{dependency}::{step.id}"
                edges[identifier] = {
                    "id": identifier,
                    "job": None,
                    "lineage": document.name,
                    "relation": "precedes",
                    "source": job_ids[source_step.definition_id],
                    "state": "observed",
                    "target": target,
                    "type": "process",
                }

    return {
        "edges": [edges[key] for key in sorted(edges)],
        "nodes": [nodes[key] for key in sorted(nodes)],
    }


def workspace_revision(workspace: WorkspaceDocument) -> str:
    payload = json.dumps(
        workspace.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _object_payload(
    node: GraphNode,
    fields: list[GraphNode],
    *,
    graph: GraphDocument,
    scope_object: object | None = None,
) -> dict[str, object]:
    annotation = node.annotation.to_dict() if node.annotation else None
    area = getattr(scope_object, "area", None)
    system = getattr(scope_object, "system", None)
    zones = tuple(getattr(scope_object, "zones", ()))
    return {
        "annotation": annotation,
        "area": area,
        "area_ref": f"{system}:{area}" if system and area else None,
        "catalog": graph.catalog,
        "fields": [
            _field_payload(field, graph.name)
            for field in sorted(
                fields,
                key=lambda item: (int(item.metadata.get("position") or 999999), item.id),
            )
        ],
        "grain": node.metadata.get("grain"),
        "graph": graph.name,
        "id": _ui_id(graph.name, node.id),
        "label": node.label,
        "name": node.metadata.get("name") or node.label,
        "namespace": node.metadata.get("namespace"),
        "object_id": node.id,
        "primary_key": list(node.metadata.get("primary_key") or ()),
        "reference": f"{graph.catalog}.{node.label}",
        "review": node.metadata.get("annotation_review"),
        "system": system,
        "schema_ref": f"{graph.name}:{node.metadata.get('namespace')}",
        "technical_description": node.metadata.get("technical_description"),
        "type": node.type,
        "zones": list(zones),
    }


def _field_payload(node: GraphNode, graph_name: str) -> dict[str, object]:
    return {
        "annotation": node.annotation.to_dict() if node.annotation else None,
        "data_type": node.metadata.get("data_type"),
        "id": _ui_id(graph_name, node.id),
        "is_nullable": node.metadata.get("is_nullable"),
        "label": node.label,
        "position": node.metadata.get("position"),
        "review": node.metadata.get("annotation_review"),
        "semantic_type": node.metadata.get("semantic_type"),
    }


def _edge_payload(
    edge: GraphEdge,
    nodes: dict[str, GraphNode],
    graph_name: str,
) -> dict[str, object] | None:
    if edge.type not in {"foreign_key", "relationship_candidate"}:
        return None
    source = nodes.get(edge.source_id)
    target = nodes.get(edge.target_id)
    if edge.type == "relationship_candidate":
        if source is not None and source.type == "field":
            source = nodes.get(str(source.metadata.get("object_id") or ""))
        if target is not None and target.type == "field":
            target = nodes.get(str(target.metadata.get("object_id") or ""))
    if source is None or target is None or source.type not in {"table", "view"}:
        return None
    if target.type not in {"table", "view"}:
        return None
    return {
        "graph": graph_name,
        "id": _ui_id(graph_name, edge.id),
        "metadata": edge.metadata,
        "source": _ui_id(graph_name, source.id),
        "target": _ui_id(graph_name, target.id),
        "type": edge.type,
    }


def _workspace_relationship_payloads(
    workspace: WorkspaceDocument,
    objects: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_reference = {
        (str(item["graph"]), str(item["object_id"])): str(item["id"])
        for item in objects
    }
    payloads = []
    for relationship in workspace.relationships:
        source = by_reference.get(
            (relationship.source.graph, relationship.source.object_id)
        )
        target = by_reference.get(
            (relationship.target.graph, relationship.target.object_id)
        )
        if source is None or target is None:
            continue
        payloads.append(
            {
                "graph": None,
                "id": f"workspace::{workspace.name}::{relationship.id}",
                "metadata": {
                    "from_field": relationship.source.fields[0],
                    "origin": relationship.origin,
                    "reason": relationship.reason,
                    "state": relationship.state,
                    "to_field": relationship.target.fields[0],
                    "workspace": workspace.name,
                },
                "source": source,
                "target": target,
                "type": "relationship_candidate",
            }
        )
    return payloads


def _review_queue(objects: list[dict[str, object]]) -> list[dict[str, object]]:
    rank = {"draft": 0, "review_required": 1, "deferred": 2, "missing": 3,
            "validated": 4, "rejected": 5}
    records = []
    for item in objects:
        annotation = item["annotation"]
        state = str(annotation["state"]) if isinstance(annotation, dict) else "missing"
        records.append({
            "annotation": annotation,
            "field_count": len(item["fields"]),
            "graph": item["graph"],
            "grain": item["grain"],
            "id": item["id"],
            "label": item["label"],
            "review": item["review"],
            "state": state,
            "type": item["type"],
        })
    return sorted(records, key=lambda item: (rank.get(str(item["state"]), 99), str(item["label"])))


def _workspace_payload(workspace: WorkspaceDocument, graph_name: str) -> dict[str, object] | None:
    systems = []
    for system in workspace.systems:
        if graph_name not in system.graphs:
            continue
        systems.append({
            "areas": [item.to_dict() for item in system.areas],
            "description": system.description,
            "name": system.name,
            "zones": [item.to_dict() for item in system.zones],
        })
    if not systems:
        return None
    return {
        "description": workspace.description,
        "name": workspace.name,
        "revision": workspace_revision(workspace),
        "systems": systems,
    }


def _workspace_payload_for_graphs(
    workspace: WorkspaceDocument,
    graph_names: set[str],
) -> dict[str, object]:
    systems = [
        system.to_dict()
        for system in workspace.systems
        if graph_names.intersection(system.graphs)
    ]
    return {
        "description": workspace.description,
        "name": workspace.name,
        "revision": workspace_revision(workspace),
        "systems": systems,
    }


def _ui_id(graph_name: str, item_id: str) -> str:
    return f"{graph_name}::{item_id}"


def _combined_revision(revisions: dict[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(revisions, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _normalize_reference(value: str) -> str:
    return "".join(
        character for character in value.strip() if character not in "[]`\""
    ).casefold()


def _display_reference(value: str) -> str:
    clean = "".join(character for character in value if character not in "[]`\"")
    parts = [part for part in clean.split(".") if part]
    return ".".join(parts[-2:]) if len(parts) > 1 else clean
