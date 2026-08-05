"""Build a deterministic technical graph from connector catalog observations."""

from __future__ import annotations

from urllib.parse import quote

from tarel.connectors.contracts import CatalogResult
from tarel.graph.contracts import GraphDocument, GraphEdge, GraphNode


def build_graph_from_catalog(name: str, catalog: CatalogResult) -> GraphDocument:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    object_ids: dict[tuple[str, str], str] = {}
    field_ids: dict[tuple[str, str, str], str] = {}
    catalog_id = _id("catalog", catalog.catalog)
    nodes.append(
        GraphNode(
            id=catalog_id,
            type="catalog",
            label=catalog.catalog,
            metadata={"dialect": catalog.dialect, "source_type": catalog.source_type},
        )
    )

    namespace_ids: dict[str, str] = {}
    for item in catalog.objects:
        namespace_id = namespace_ids.get(item.namespace)
        if namespace_id is None:
            namespace_id = _id("namespace", catalog.catalog, item.namespace)
            namespace_ids[item.namespace] = namespace_id
            nodes.append(
                GraphNode(
                    id=namespace_id,
                    type="namespace",
                    label=item.namespace,
                    metadata={"catalog": catalog.catalog},
                )
            )
            edges.append(_contains(catalog_id, namespace_id))

        object_id = _id("object", catalog.catalog, item.namespace, item.name)
        object_ids[(item.namespace, item.name)] = object_id
        nodes.append(
            GraphNode(
                id=object_id,
                type=item.kind,
                label=f"{item.namespace}.{item.name}",
                metadata={
                    "catalog": catalog.catalog,
                    "name": item.name,
                    "namespace": item.namespace,
                    "primary_key": list(item.primary_key),
                    "technical_description": item.description,
                },
            )
        )
        edges.append(_contains(namespace_id, object_id))
        for catalog_field in item.fields:
            field_id = _id(
                "field",
                catalog.catalog,
                item.namespace,
                item.name,
                catalog_field.name,
            )
            field_ids[(item.namespace, item.name, catalog_field.name)] = field_id
            nodes.append(
                GraphNode(
                    id=field_id,
                    type="field",
                    label=catalog_field.name,
                    metadata={
                        "data_type": catalog_field.data_type,
                        "is_primary_key": catalog_field.is_primary_key,
                        "nullable": catalog_field.nullable,
                        "object_id": object_id,
                        "position": catalog_field.position,
                        "technical_description": catalog_field.description,
                    },
                )
            )
            edges.append(_contains(object_id, field_id))

    for relationship in catalog.relationships:
        source_id = object_ids.get((relationship.from_namespace, relationship.from_object))
        target_id = object_ids.get((relationship.to_namespace, relationship.to_object))
        if source_id is None or target_id is None:
            continue
        relationship_id = _id(
            "foreign_key",
            catalog.catalog,
            relationship.from_namespace,
            relationship.from_object,
            relationship.name,
        )
        edges.append(
            GraphEdge(
                id=relationship_id,
                source_id=source_id,
                target_id=target_id,
                type="foreign_key",
                metadata={
                    "from_fields": list(relationship.from_fields),
                    "name": relationship.name,
                    "to_fields": list(relationship.to_fields),
                },
            )
        )
        for position, (from_field, to_field) in enumerate(
            zip(relationship.from_fields, relationship.to_fields, strict=True),
            start=1,
        ):
            source_field_id = field_ids[(
                relationship.from_namespace,
                relationship.from_object,
                from_field,
            )]
            target_field_id = field_ids[(
                relationship.to_namespace,
                relationship.to_object,
                to_field,
            )]
            edges.append(
                GraphEdge(
                    id=f"{relationship_id}/field/{position}",
                    source_id=source_field_id,
                    target_id=target_field_id,
                    type="foreign_key_field",
                    metadata={"relationship_id": relationship_id},
                )
            )

    return GraphDocument(
        name=name,
        connector=catalog.connector,
        source_type=catalog.source_type,
        catalog=catalog.catalog,
        dialect=catalog.dialect,
        nodes=tuple(nodes),
        edges=tuple(edges),
    )


def _id(kind: str, *parts: str) -> str:
    return f"{kind}:" + "/".join(quote(part, safe="") for part in parts)


def _contains(source_id: str, target_id: str) -> GraphEdge:
    return GraphEdge(
        id=f"contains:{source_id}->{target_id}",
        source_id=source_id,
        target_id=target_id,
        type="contains",
    )
