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


def browser_graph(
    graph: GraphDocument,
    *,
    workspaces: Iterable[WorkspaceDocument] = (),
    lineage_names: tuple[str, ...] = (),
    editable: bool = False,
    lineage_documents: Iterable[LineageDocument] = (),
) -> dict[str, object]:
    nodes = graph.node_by_id()
    objects = [node for node in graph.nodes if node.type in {"table", "view"}]
    fields_by_object: dict[str, list[GraphNode]] = {node.id: [] for node in objects}
    for node in graph.nodes:
        if node.type == "field":
            parent = str(node.metadata.get("object_id") or "")
            if parent in fields_by_object:
                fields_by_object[parent].append(node)

    object_payloads = [
        _object_payload(node, fields_by_object[node.id])
        for node in sorted(objects, key=lambda item: (item.label.casefold(), item.id))
    ]
    documents = tuple(lineage_documents)
    selected_lineages = tuple(item.name for item in documents) or lineage_names
    return {
        "catalog": graph.catalog,
        "connector": graph.connector,
        "dialect": graph.dialect,
        "editable": editable,
        "edges": [
            payload
            for edge in sorted(graph.edges, key=lambda item: item.id)
            if (payload := _edge_payload(edge, nodes)) is not None
        ],
        "graph": graph.name,
        "lineage_documents": browser_lineages(documents),
        "lineages": list(selected_lineages),
        "objects": object_payloads,
        "review": _review_queue(object_payloads),
        "revision": graph_revision(graph),
        "source_type": graph.source_type,
        "workspaces": [
            payload
            for workspace in sorted(workspaces, key=lambda item: item.name)
            if (payload := _workspace_payload(workspace, graph.name)) is not None
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


def workspace_revision(workspace: WorkspaceDocument) -> str:
    payload = json.dumps(
        workspace.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _object_payload(node: GraphNode, fields: list[GraphNode]) -> dict[str, object]:
    annotation = node.annotation.to_dict() if node.annotation else None
    return {
        "annotation": annotation,
        "fields": [
            _field_payload(field)
            for field in sorted(
                fields,
                key=lambda item: (int(item.metadata.get("position") or 999999), item.id),
            )
        ],
        "grain": node.metadata.get("grain"),
        "id": node.id,
        "label": node.label,
        "name": node.metadata.get("name") or node.label,
        "namespace": node.metadata.get("namespace"),
        "primary_key": list(node.metadata.get("primary_key") or ()),
        "review": node.metadata.get("annotation_review"),
        "technical_description": node.metadata.get("technical_description"),
        "type": node.type,
    }


def _field_payload(node: GraphNode) -> dict[str, object]:
    return {
        "annotation": node.annotation.to_dict() if node.annotation else None,
        "data_type": node.metadata.get("data_type"),
        "id": node.id,
        "is_nullable": node.metadata.get("is_nullable"),
        "label": node.label,
        "position": node.metadata.get("position"),
        "review": node.metadata.get("annotation_review"),
        "semantic_type": node.metadata.get("semantic_type"),
    }


def _edge_payload(edge: GraphEdge, nodes: dict[str, GraphNode]) -> dict[str, object] | None:
    if edge.type not in {"foreign_key", "relationship_candidate"}:
        return None
    source = nodes.get(edge.source_id)
    target = nodes.get(edge.target_id)
    if edge.type == "relationship_candidate":
        source = nodes.get(str(source.metadata.get("object_id") or "")) if source else None
        target = nodes.get(str(target.metadata.get("object_id") or "")) if target else None
    if source is None or target is None or source.type not in {"table", "view"}:
        return None
    if target.type not in {"table", "view"}:
        return None
    return {
        "id": edge.id,
        "metadata": edge.metadata,
        "source": source.id,
        "target": target.id,
        "type": edge.type,
    }


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
