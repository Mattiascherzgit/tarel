"""Validate and apply annotation proposals as graph drafts."""

from __future__ import annotations

from dataclasses import replace

from tarel.annotations.contracts import (
    AnnotationFailure,
    AnnotationProposalEnvelope,
    ObjectAnnotationProposal,
)
from tarel.annotations.review import has_human_review
from tarel.annotations.tasks import annotation_task_for_target
from tarel.graph.contracts import AnnotationProvenance, GraphAnnotation, GraphDocument, GraphNode
from tarel.knowledge.contracts import KnowledgeReference


def apply_annotation_proposal(
    graph: GraphDocument,
    envelope: AnnotationProposalEnvelope,
    *,
    source: str,
    provider: str | None = None,
    model: str | None = None,
    context_documents: tuple[KnowledgeReference, ...] | None = None,
) -> GraphDocument:
    task = annotation_task_for_target(graph, envelope.target_id)
    if envelope.task_id != task.id:
        raise AnnotationFailure(
            "stale_proposal",
            "Annotation proposal does not match the current graph object.",
        )
    proposal = envelope.annotation
    supplied_context = (
        envelope.context_documents if context_documents is None else context_documents
    )
    _validate_knowledge_evidence(proposal, supplied_context)
    context_payload = [item.to_dict() for item in supplied_context]
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
            metadata["annotation_context_documents"] = context_payload
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
            metadata["annotation_context_documents"] = context_payload
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


def _validate_knowledge_evidence(
    proposal: ObjectAnnotationProposal,
    references: tuple[KnowledgeReference, ...],
) -> None:
    expected = {f"{item.id}@{item.revision}" for item in references}
    evidence = [*proposal.evidence]
    for field in proposal.fields:
        evidence.extend(field.evidence)
    for item in evidence:
        source_is_document = item.source == "knowledge_document"
        exact_reference = item.reference in expected
        mentions_document = (
            "knowledge_document" in item.reference.casefold()
            or any(reference in item.reference for reference in expected)
        )
        if source_is_document and not exact_reference:
            raise AnnotationFailure(
                "invalid_knowledge_evidence",
                "Knowledge evidence reference must be exactly ID@REVISION from the task.",
            )
        if mentions_document and not (source_is_document and exact_reference):
            raise AnnotationFailure(
                "invalid_knowledge_evidence",
                "Knowledge evidence must use source knowledge_document and exact ID@REVISION.",
            )
