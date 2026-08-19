"""Conservative bindings from normalized semantics to stable TAREL graph IDs."""

from __future__ import annotations

import re
from dataclasses import replace

from tarel.graph.contracts import GraphDocument, GraphEdge, GraphNode
from tarel.semantics.contracts import (
    SemanticDataset,
    SemanticDiagnostic,
    SemanticField,
    SemanticModel,
    SemanticRelationship,
)

_SIMPLE_IDENTIFIER = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_$]*|\"[^\"]+\"|\[[^\]]+\]|`[^`]+`)"
    r"(?:\.(?:[A-Za-z_][A-Za-z0-9_$]*|\"[^\"]+\"|\[[^\]]+\]|`[^`]+`))*$"
)


def bind_semantic_models(
    models: tuple[SemanticModel, ...],
    graph: GraphDocument,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticModel, ...]:
    graph_nodes = graph.node_by_id()
    object_nodes = tuple(node for node in graph.nodes if node.type in {"table", "view"})
    fields_by_object: dict[str, tuple[GraphNode, ...]] = {
        node.id: tuple(
            field
            for field in graph.nodes
            if field.type == "field" and field.metadata.get("object_id") == node.id
        )
        for node in object_nodes
    }
    bound_models: list[SemanticModel] = []
    for model in models:
        bound_datasets: list[SemanticDataset] = []
        for dataset in model.datasets:
            object_node = _match_dataset(dataset, graph, object_nodes)
            if object_node is None:
                diagnostics.append(
                    SemanticDiagnostic(
                        level="warning",
                        code="dataset_not_bound",
                        message=f"No unique graph object matches source: {dataset.source}",
                        source_reference=dataset.source_reference,
                    )
                )
                bound_datasets.append(dataset)
                continue
            bound_fields: list[SemanticField] = []
            for field in dataset.fields:
                graph_field = _match_field(field, fields_by_object[object_node.id])
                if graph_field is None:
                    diagnostics.append(
                        SemanticDiagnostic(
                            level="warning",
                            code="field_not_bound",
                            message=(
                                "No graph field matches a simple source expression for: "
                                f"{dataset.name}.{field.name}"
                            ),
                            source_reference=field.source_reference,
                        )
                    )
                    bound_fields.append(field)
                else:
                    bound_fields.append(replace(field, graph_node_id=graph_field.id))
            bound_datasets.append(
                replace(
                    dataset,
                    fields=tuple(bound_fields),
                    graph_node_id=object_node.id,
                )
            )

        datasets_by_name = {item.name: item for item in bound_datasets}
        bound_relationships = tuple(
            _bind_relationship(
                relationship,
                datasets_by_name,
                graph,
                graph_nodes,
                diagnostics,
            )
            for relationship in model.relationships
        )
        bound_models.append(
            replace(
                model,
                datasets=tuple(bound_datasets),
                relationships=bound_relationships,
            )
        )
    return tuple(bound_models)


def _match_dataset(
    dataset: SemanticDataset,
    graph: GraphDocument,
    objects: tuple[GraphNode, ...],
) -> GraphNode | None:
    source_parts = _identifier_parts(dataset.source)
    if not source_parts:
        return None
    tiers: tuple[str, ...] = (
        ".".join(source_parts),
        ".".join(source_parts[-2:]) if len(source_parts) >= 2 else "",
        source_parts[-1],
    )
    for tier_index, expected in enumerate(tiers):
        if not expected:
            continue
        matches = []
        for node in objects:
            namespace = str(node.metadata.get("namespace") or "")
            name = str(node.metadata.get("name") or node.label.rsplit(".", 1)[-1])
            candidates = (
                f"{graph.catalog}.{namespace}.{name}",
                f"{namespace}.{name}",
                name,
            )
            if candidates[tier_index].casefold() == expected.casefold():
                matches.append(node)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    return None


def _match_field(field: SemanticField, candidates: tuple[GraphNode, ...]) -> GraphNode | None:
    source_names = {
        identifier
        for expression in field.expressions
        if (identifier := _simple_identifier(expression.expression)) is not None
    }
    if not source_names:
        return None
    matches = [node for node in candidates if node.label.casefold() in source_names]
    return matches[0] if len(matches) == 1 else None


def _bind_relationship(
    relationship: SemanticRelationship,
    datasets: dict[str, SemanticDataset],
    graph: GraphDocument,
    nodes: dict[str, GraphNode],
    diagnostics: list[SemanticDiagnostic],
) -> SemanticRelationship:
    source_dataset = datasets.get(relationship.from_dataset)
    target_dataset = datasets.get(relationship.to_dataset)
    if (
        source_dataset is None
        or target_dataset is None
        or source_dataset.graph_node_id is None
        or target_dataset.graph_node_id is None
    ):
        diagnostics.append(
            SemanticDiagnostic(
                level="warning",
                code="relationship_not_bound",
                message="Semantic relationship endpoints are not both bound to graph objects.",
                source_reference=relationship.source_reference,
            )
        )
        return relationship
    matching = [
        edge
        for edge in graph.edges
        if _relationship_matches(
            edge,
            relationship,
            source_dataset.graph_node_id,
            target_dataset.graph_node_id,
            nodes,
        )
    ]
    if len(matching) == 1:
        return replace(relationship, graph_edge_id=matching[0].id)
    diagnostics.append(
        SemanticDiagnostic(
            level="warning",
            code="relationship_not_bound" if not matching else "relationship_binding_ambiguous",
            message=(
                "No declared graph relationship matches the semantic relationship."
                if not matching
                else "Multiple graph relationships match the semantic relationship."
            ),
            source_reference=relationship.source_reference,
        )
    )
    return relationship


def _relationship_matches(
    edge: GraphEdge,
    relationship: SemanticRelationship,
    source_id: str,
    target_id: str,
    nodes: dict[str, GraphNode],
) -> bool:
    return (
        edge.type == "foreign_key"
        and edge.source_id == source_id
        and edge.target_id == target_id
        and edge.metadata.get("from_fields") == list(relationship.from_fields)
        and edge.metadata.get("to_fields") == list(relationship.to_fields)
        and edge.source_id in nodes
        and edge.target_id in nodes
    )


def _simple_identifier(value: str) -> str | None:
    candidate = value.strip()
    if not _SIMPLE_IDENTIFIER.fullmatch(candidate):
        return None
    parts = _identifier_parts(candidate)
    return parts[-1].casefold() if parts else None


def _identifier_parts(value: str) -> tuple[str, ...]:
    candidate = value.strip()
    if not candidate or any(character in candidate for character in "();\n\r"):
        return ()
    parts = []
    for raw in candidate.split("."):
        part = raw.strip()
        if len(part) >= 2 and (
            (part[0] == part[-1] and part[0] in {'"', '`'})
            or (part[0] == "[" and part[-1] == "]")
        ):
            part = part[1:-1]
        if not part:
            return ()
        parts.append(part)
    return tuple(parts)
