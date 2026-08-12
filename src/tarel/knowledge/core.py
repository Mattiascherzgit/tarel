"""Deterministic scope resolution and budgeting for annotation knowledge."""

from __future__ import annotations

from collections.abc import Iterable

from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.knowledge.contracts import (
    DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    MAX_KNOWLEDGE_CHARACTERS,
    KnowledgeContext,
    KnowledgeDocument,
    KnowledgeFailure,
    KnowledgeReference,
    ResolvedKnowledgeDocument,
)
from tarel.workspaces.contracts import WorkspaceDocument

_SCOPE_RANK = {"global": 0, "system": 1, "graph": 2, "schema": 3, "object": 4}


def resolve_knowledge(
    documents: Iterable[KnowledgeDocument],
    graph: GraphDocument,
    node: GraphNode,
    *,
    workspace: WorkspaceDocument | None = None,
    mode: str = "none",
    document_ids: tuple[str, ...] = (),
    max_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
) -> KnowledgeContext:
    if mode not in {"none", "scoped"}:
        raise KnowledgeFailure(
            "invalid_knowledge_mode",
            "Knowledge mode must be none or scoped.",
        )
    if not 1 <= max_characters <= MAX_KNOWLEDGE_CHARACTERS:
        raise KnowledgeFailure(
            "invalid_knowledge_budget",
            f"Knowledge budget must be between 1 and {MAX_KNOWLEDGE_CHARACTERS} characters.",
        )
    available = {item.id: item for item in documents}
    missing = sorted(set(document_ids) - set(available))
    if missing:
        raise KnowledgeFailure(
            "knowledge_not_found",
            f"Knowledge document not found: {missing[0]}",
        )
    selected = {item: available[item] for item in document_ids}
    if mode == "scoped":
        selected.update(
            {
                item.id: item
                for item in available.values()
                if knowledge_applies(item, graph, node, workspace=workspace)
            }
        )
    ordered = sorted(
        selected.values(),
        key=lambda item: (_SCOPE_RANK[item.scope.kind], item.id.casefold(), item.id),
    )
    remaining = max_characters
    allocations: dict[str, ResolvedKnowledgeDocument] = {}
    explicit = set(document_ids)
    allocation_order = sorted(
        ordered,
        key=lambda item: (
            0 if item.id in explicit else 1,
            -_SCOPE_RANK[item.scope.kind],
            item.id.casefold(),
            item.id,
        ),
    )
    for document in allocation_order:
        if remaining <= 0:
            continue
        content = document.content[:remaining]
        truncated = len(content) < len(document.content)
        allocations[document.id] = ResolvedKnowledgeDocument(
            reference=KnowledgeReference(
                id=document.id,
                title=document.title,
                scope=document.scope,
                state=document.state,
                revision=document.revision,
                characters=len(content),
                truncated=truncated,
            ),
            content=content,
        )
        remaining -= len(content)
    return KnowledgeContext(
        documents=tuple(allocations[item.id] for item in ordered if item.id in allocations),
        omitted=tuple(item.id for item in ordered if item.id not in allocations),
        max_characters=max_characters,
    )


def knowledge_applies(
    document: KnowledgeDocument,
    graph: GraphDocument,
    node: GraphNode,
    *,
    workspace: WorkspaceDocument | None,
) -> bool:
    scope = document.scope
    if scope.kind == "global":
        return True
    if scope.kind == "system":
        return (
            workspace is not None
            and scope.workspace is not None
            and workspace.name.casefold() == scope.workspace.casefold()
            and any(
                system.name.casefold() == scope.reference.casefold()
                and graph.name in system.graphs
                for system in workspace.systems
            )
        )
    if scope.kind == "graph":
        return graph.name.casefold() == scope.reference.casefold()
    if scope.graph is None or scope.graph.casefold() != graph.name.casefold():
        return False
    if scope.kind == "schema":
        namespace = str(node.metadata.get("namespace") or "")
        return namespace.casefold() == scope.reference.casefold()
    if scope.kind == "object":
        candidates = {
            node.id.casefold(),
            node.label.casefold(),
            str(node.metadata.get("name") or "").casefold(),
        }
        return scope.reference.casefold() in candidates
    return False
