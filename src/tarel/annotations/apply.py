"""Validate and apply annotation proposals as graph drafts."""

from __future__ import annotations

from dataclasses import replace

from tarel.annotations.contracts import AnnotationFailure, AnnotationProposalEnvelope
from tarel.annotations.review import has_human_review
from tarel.annotations.tasks import annotation_task_for_target
from tarel.graph.contracts import AnnotationProvenance, GraphAnnotation, GraphDocument, GraphNode


def apply_annotation_proposal(
    graph: GraphDocument,
    envelope: AnnotationProposalEnvelope,
    *,
    source: str,
    provider: str | None = None,
    model: str | None = None,
) -> GraphDocument:
    task = annotation_task_for_target(graph, envelope.target_id)
    if envelope.task_id != task.id:
        raise AnnotationFailure(
            "stale_proposal",
            "Annotation proposal does not match the current graph object.",
        )
    proposal = envelope.annotation
    field_proposals = {item.name: item for item in proposal.fields}
    object_fields = {
        node.label
        for node in graph.nodes
        if node.type == "field" and node.metadata.get("object_id") == envelope.target_id
    }
    if set(field_proposals) != object_fields:
        raise AnnotationFailure(
            "invalid_proposal",
            "Proposal must annotate every supplied field exactly once.",
        )

    reviewed_nodes = [
        node
        for node in graph.nodes
        if (
            node.id == envelope.target_id
            or (node.type == "field" and node.metadata.get("object_id") == envelope.target_id)
        )
        and (has_human_review(node) or (node.annotation and node.annotation.state == "validated"))
    ]
    if reviewed_nodes:
        raise AnnotationFailure(
            "reviewed_annotation",
            "A new proposal cannot overwrite human-reviewed annotations: "
            + ", ".join(node.label for node in reviewed_nodes),
        )

    provenance = AnnotationProvenance(source=source, provider=provider, model=model)
    updated_nodes: list[GraphNode] = []
    for node in graph.nodes:
        if node.id == envelope.target_id:
            metadata = dict(node.metadata)
            metadata["grain"] = proposal.grain
            updated_nodes.append(
                replace(
                    node,
                    metadata=metadata,
                    annotation=GraphAnnotation(
                        description=proposal.description,
                        role=proposal.role,
                        synonyms=proposal.synonyms,
                        warnings=proposal.warnings,
                        confidence=proposal.confidence,
                        confidence_reason=proposal.confidence_reason,
                        evidence=proposal.evidence,
                        provenance=provenance,
                    ),
                )
            )
            continue
        field_proposal = field_proposals.get(node.label)
        if node.type == "field" and node.metadata.get("object_id") == envelope.target_id:
            assert field_proposal is not None
            metadata = dict(node.metadata)
            metadata["semantic_type"] = field_proposal.semantic_type
            updated_nodes.append(
                replace(
                    node,
                    metadata=metadata,
                    annotation=GraphAnnotation(
                        description=field_proposal.description,
                        role=field_proposal.role,
                        synonyms=field_proposal.synonyms,
                        warnings=field_proposal.warnings,
                        confidence=field_proposal.confidence,
                        confidence_reason=field_proposal.confidence_reason,
                        evidence=field_proposal.evidence,
                        provenance=provenance,
                    ),
                )
            )
            continue
        updated_nodes.append(node)
    return replace(graph, nodes=tuple(updated_nodes))
