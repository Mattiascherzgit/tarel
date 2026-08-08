"""Deterministic upstream traversal across stored lineage and technical graphs."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from hashlib import sha256

from tarel.annotations.states import DEFAULT_CONTEXT_ANNOTATION_STATES
from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.lineage.contracts import (
    LineageDocument,
    LineageEvidence,
    LineageFailure,
    LineageReview,
    LineageWriteSource,
)
from tarel.retrieval.bm25 import rank_bm25, tokenize
from tarel.retrieval.contracts import EmbeddingBackend, RankedDocument, RetrievalDocument

DEFAULT_LINEAGE_STATES = frozenset({"draft", "review_required", "validated"})
_LINEAGE_STATES = frozenset({"draft", "rejected", "review_required", "validated"})
_MAX_VECTOR_DOCUMENTS = 32


@dataclass(frozen=True, slots=True)
class LineageReference:
    id: str
    reference: str
    name: str
    kind: str
    source: str
    description: str | None = None
    description_kind: str | None = None
    annotation_state: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "annotation_state": self.annotation_state,
            "description": self.description,
            "description_kind": self.description_kind,
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "reference": self.reference,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class LineageHop:
    id: str
    depth: int
    source: LineageReference
    target: LineageReference
    relation: str
    state: str
    lineage: str | None = None
    role: str | None = None
    via_definition: str | None = None
    process_steps: tuple[str, ...] = ()
    via: tuple[str, ...] = ()
    evidence: LineageEvidence | None = None
    write_evidence: LineageEvidence | None = None
    reviews: tuple[LineageReview, ...] = ()
    granularity_change: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "depth": self.depth,
            "evidence": self.evidence.to_dict() if self.evidence else None,
            "id": self.id,
            "granularity_change": self.granularity_change,
            "lineage": self.lineage,
            "process_steps": list(self.process_steps),
            "relation": self.relation,
            "role": self.role,
            "reviews": [item.to_dict() for item in self.reviews],
            "source": self.source.to_dict(),
            "state": self.state,
            "target": self.target.to_dict(),
            "via": list(self.via),
            "via_definition": self.via_definition,
            "write_evidence": self.write_evidence.to_dict() if self.write_evidence else None,
        }


@dataclass(frozen=True, slots=True)
class UpstreamTrace:
    query: str
    start: LineageReference
    hops: tuple[LineageHop, ...]
    origins: tuple[LineageReference, ...]
    warnings: tuple[str, ...]
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "hops": [item.to_dict() for item in self.hops],
            "origins": [item.to_dict() for item in self.origins],
            "query": self.query,
            "start": self.start.to_dict(),
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True, slots=True)
class _Node:
    value: LineageReference
    aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Edge:
    id: str
    source_id: str
    target_id: str
    relation: str
    state: str
    lineage: str | None = None
    role: str | None = None
    via_definition: str | None = None
    process_steps: tuple[str, ...] = ()
    via: tuple[str, ...] = ()
    evidence: LineageEvidence | None = None
    write_evidence: LineageEvidence | None = None
    reviews: tuple[LineageReview, ...] = ()
    granularity_change: str | None = None


@dataclass(frozen=True, slots=True)
class _Network:
    nodes: dict[str, _Node]
    edges: tuple[_Edge, ...]

    def aliases(self) -> dict[str, tuple[str, ...]]:
        result: dict[str, list[str]] = {}
        for node in self.nodes.values():
            for alias in node.aliases:
                result.setdefault(_normalize(alias), []).append(node.value.id)
        return {key: tuple(sorted(set(values))) for key, values in result.items()}


def find_lineage_references(
    documents: tuple[LineageDocument, ...],
    graphs: tuple[GraphDocument, ...],
    query: str,
    *,
    limit: int = 20,
    mode: str = "lexical",
    embedder: EmbeddingBackend | None = None,
) -> tuple[LineageReference, ...]:
    if not query.strip() or limit < 1:
        raise LineageFailure("invalid_lineage_search", "Lineage search requires a query and limit.")
    if mode not in {"lexical", "bm25", "vector", "hybrid"}:
        raise LineageFailure(
            "invalid_lineage_search",
            f"Unsupported lineage search mode: {mode}",
        )
    if mode in {"vector", "hybrid"} and embedder is None:
        raise LineageFailure(
            "missing_embedding_backend",
            "Vector lineage search requires a local embedding backend.",
        )
    network = _build_network(documents, graphs, DEFAULT_LINEAGE_STATES)
    if mode == "lexical":
        return _lexical_lineage_references(network, query, limit=limit)

    retrieval_documents = _lineage_retrieval_documents(network)
    bm25 = (
        rank_bm25(retrieval_documents, query, limit=len(retrieval_documents))
        if mode in {"bm25", "vector", "hybrid"}
        else ()
    )
    vector = (
        _rank_lineage_vectors(
            _lineage_vector_candidates(network, retrieval_documents, bm25, query),
            query,
            embedder,
        )
        if mode in {"vector", "hybrid"}
        else ()
    )
    if mode == "bm25":
        ordered_ids = tuple(item.document.id for item in bm25)
    elif mode == "vector":
        ordered_ids = tuple(item[0] for item in vector)
    else:
        ordered_ids = _fuse_lineage_ranks(
            tuple(item.document.id for item in bm25),
            tuple(item[0] for item in vector),
            tuple(item.id for item in _lexical_lineage_references(network, query, limit=limit)),
        )
    return tuple(network.nodes[node_id].value for node_id in ordered_ids[:limit])


def _lexical_lineage_references(
    network: _Network,
    query: str,
    *,
    limit: int,
) -> tuple[LineageReference, ...]:
    needle = _normalize(query)
    query_terms = set(tokenize(query))
    ranked: list[tuple[int, float, int, int, str, str, LineageReference]] = []
    for node in network.nodes.values():
        normalized = {_normalize(item) for item in node.aliases}
        name = _normalize(node.value.name)
        if needle == name or needle in normalized:
            rank = 0
        elif any(item.endswith(f".{needle}") for item in normalized):
            rank = 1
        elif needle in name:
            rank = 2
        elif any(needle in item for item in normalized):
            rank = 3
        else:
            rank = 4
        candidate_terms = set(
            tokenize(
                " ".join(
                    (
                        node.value.reference,
                        node.value.name,
                        node.value.kind,
                        node.value.description or "",
                        *node.aliases,
                    )
                )
            )
        )
        matched = len(query_terms & candidate_terms)
        if rank == 4 and matched == 0:
            continue
        coverage = matched / max(len(query_terms), 1)
        ranked.append(
            (
                rank,
                -coverage,
                -matched,
                0 if node.value.source.startswith("lineage:") else 1,
                len(name),
                node.value.reference.casefold(),
                node.value.id,
                node.value,
            )
        )
    return tuple(item[7] for item in sorted(ranked)[:limit])


def _lineage_retrieval_documents(network: _Network) -> tuple[RetrievalDocument, ...]:
    return tuple(
        RetrievalDocument(
            id=node.value.id,
            object_id=node.value.id,
            field_id=None,
            namespace=node.value.source,
            label=node.value.reference,
            text="\n".join(
                (
                    f"Name: {node.value.name}",
                    f"Kind: {node.value.kind}",
                    f"Reference: {node.value.reference}",
                    f"Aliases: {', '.join(node.aliases)}",
                    f"Description: {node.value.description or ''}",
                )
            ),
        )
        for node in sorted(network.nodes.values(), key=lambda item: item.value.id)
    )


def _rank_lineage_vectors(
    documents: tuple[RetrievalDocument, ...],
    query: str,
    embedder: EmbeddingBackend | None,
) -> tuple[tuple[str, float], ...]:
    assert embedder is not None
    vectors = embedder.embed_documents(
        tuple(f"{item.label}\n{item.text}" for item in documents),
        batch_size=16,
    )
    query_vector = embedder.embed_query(query)
    ranked = []
    for document, vector in zip(documents, vectors, strict=True):
        if len(vector) != len(query_vector):
            raise LineageFailure(
                "model_index_mismatch",
                "Lineage query and document embedding dimensions differ.",
            )
        ranked.append(
            (
                document.id,
                sum(left * right for left, right in zip(vector, query_vector, strict=True)),
            )
        )
    return tuple(sorted(ranked, key=lambda item: (-item[1], item[0])))


def _lineage_vector_candidates(
    network: _Network,
    documents: tuple[RetrievalDocument, ...],
    bm25: tuple[RankedDocument, ...],
    query: str,
) -> tuple[RetrievalDocument, ...]:
    by_id = {item.id: item for item in documents}
    ordered_ids: list[str] = []

    lineage_ids = sorted(
        node.value.id
        for node in network.nodes.values()
        if node.value.source.startswith("lineage:")
    )
    if len(lineage_ids) <= _MAX_VECTOR_DOCUMENTS:
        ordered_ids.extend(lineage_ids)

    lexical = _lexical_lineage_references(
        network,
        query,
        limit=_MAX_VECTOR_DOCUMENTS,
    )
    ordered_ids.extend(item.id for item in lexical)
    ordered_ids.extend(item.document.id for item in bm25)
    if not ordered_ids:
        ordered_ids.extend(lineage_ids)

    unique = []
    seen: set[str] = set()
    for node_id in ordered_ids:
        if node_id in seen or node_id not in by_id:
            continue
        seen.add(node_id)
        unique.append(by_id[node_id])
        if len(unique) >= _MAX_VECTOR_DOCUMENTS:
            break
    return tuple(unique)


def _fuse_lineage_ranks(*rankings: tuple[str, ...]) -> tuple[str, ...]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, node_id in enumerate(ranking, start=1):
            scores[node_id] = scores.get(node_id, 0.0) + 1.0 / (60 + rank)
    return tuple(sorted(scores, key=lambda node_id: (-scores[node_id], node_id)))


def trace_upstream(
    documents: tuple[LineageDocument, ...],
    graphs: tuple[GraphDocument, ...],
    reference: str,
    *,
    max_hops: int = 12,
    states: frozenset[str] = DEFAULT_LINEAGE_STATES,
) -> UpstreamTrace:
    if not reference.strip() or max_hops < 1 or max_hops > 100:
        raise LineageFailure(
            "invalid_lineage_trace",
            "Upstream tracing requires a reference and max_hops between 1 and 100.",
        )
    unknown_states = states - _LINEAGE_STATES
    if unknown_states or not states:
        raise LineageFailure("invalid_lineage_trace", "Invalid lineage state filter.")

    network = _build_network(documents, graphs, states)
    start_id = _resolve_start(network, reference)
    adjacency: dict[str, list[_Edge]] = {}
    for edge in network.edges:
        adjacency.setdefault(edge.target_id, []).append(edge)
    for values in adjacency.values():
        values.sort(key=_edge_key)

    queue = deque([(start_id, 0, (start_id,))])
    seen_depth = {start_id: 0}
    reached = {start_id}
    hops: list[LineageHop] = []
    warnings: set[str] = set()
    truncated = False
    while queue:
        target_id, depth, path = queue.popleft()
        incoming = adjacency.get(target_id, [])
        if depth >= max_hops:
            if incoming:
                truncated = True
            continue
        for edge in incoming:
            source_depth = depth + 1
            source = network.nodes[edge.source_id].value
            target = network.nodes[edge.target_id].value
            hops.append(
                LineageHop(
                    id=edge.id,
                    depth=source_depth,
                    source=source,
                    target=target,
                    relation=edge.relation,
                    state=edge.state,
                    lineage=edge.lineage,
                    role=edge.role,
                    via_definition=edge.via_definition,
                    process_steps=edge.process_steps,
                    via=edge.via,
                    evidence=edge.evidence,
                    write_evidence=edge.write_evidence,
                    reviews=edge.reviews,
                    granularity_change=edge.granularity_change,
                )
            )
            reached.add(edge.source_id)
            if edge.relation == "field_of":
                warnings.add(
                    "Physical lineage widens from a field to object-level procedure lineage."
                )
            if edge.state in {"draft", "review_required"}:
                warnings.add("The trace contains lineage that has not been human-validated.")
            if source.kind == "unresolved":
                warnings.add(f"Unresolved upstream reference: {source.reference}")
            if edge.source_id in path:
                warnings.add(f"Lineage cycle detected at: {source.reference}")
                continue
            previous = seen_depth.get(edge.source_id)
            if previous is None or source_depth < previous:
                seen_depth[edge.source_id] = source_depth
                queue.append((edge.source_id, source_depth, (*path, edge.source_id)))

    origins = tuple(
        sorted(
            (
                network.nodes[node_id].value
                for node_id in reached
                if not adjacency.get(node_id)
            ),
            key=lambda item: (item.reference.casefold(), item.id),
        )
    )
    if not hops:
        warnings.add("No upstream lineage was found for the selected reference.")
    if truncated:
        warnings.add(f"Trace stopped at the configured max_hops={max_hops} boundary.")
    return UpstreamTrace(
        query=reference,
        start=network.nodes[start_id].value,
        hops=tuple(sorted(hops, key=_hop_key)),
        origins=origins,
        warnings=tuple(sorted(warnings)),
        truncated=truncated,
    )


def _build_network(
    documents: tuple[LineageDocument, ...],
    graphs: tuple[GraphDocument, ...],
    states: frozenset[str],
) -> _Network:
    nodes: dict[str, _Node] = {}
    edges: list[_Edge] = []
    definitions: dict[tuple[str, str], str] = {}

    for document in sorted(documents, key=lambda item: item.name):
        summaries = {item.definition_id: item.summary for item in document.analyses}
        for definition in document.definitions:
            node_id = f"lineage:{document.name}:{definition.id}"
            definitions[(document.name, definition.id)] = node_id
            value = LineageReference(
                id=node_id,
                reference=definition.qualified_name,
                name=definition.name,
                kind=definition.kind,
                source=f"lineage:{document.name}",
                description=summaries.get(definition.id),
                description_kind=(
                    "analysis_summary" if summaries.get(definition.id) is not None else None
                ),
            )
            nodes[node_id] = _Node(
                value,
                (
                    definition.id,
                    definition.external_id,
                    definition.name,
                    definition.qualified_name,
                ),
            )

    for graph in sorted(graphs, key=lambda item: item.name):
        _add_graph_nodes(graph, nodes, edges)

    aliases = _alias_map(nodes)
    for document in sorted(documents, key=lambda item: item.name):
        steps = _steps_by_definition(document)
        definitions_by_id = document.definition_by_id()
        for claim in document.claims:
            if claim.state not in states or claim.operation != "read":
                continue
            source_id = _resolve_or_add(claim.target, nodes, aliases)
            target_id = definitions[(document.name, claim.definition_id)]
            edges.append(
                _Edge(
                    id=f"lineage:{document.name}:claim:{claim.id}",
                    source_id=source_id,
                    target_id=target_id,
                    relation="reads_from",
                    state=claim.state,
                    lineage=document.name,
                    process_steps=steps.get(claim.definition_id, ()),
                    evidence=claim.evidence,
                    reviews=claim.reviews,
                )
            )
        for unit in document.write_units:
            if unit.state not in states:
                continue
            target_id = _resolve_or_add(unit.target, nodes, aliases)
            definition = definitions_by_id[unit.definition_id]
            for upstream in unit.sources:
                source_id = _resolve_or_add(upstream.target, nodes, aliases)
                edges.append(
                    _Edge(
                        id=_write_source_edge_id(document.name, unit.id, upstream),
                        source_id=source_id,
                        target_id=target_id,
                        relation="derived_from",
                        state=unit.state,
                        lineage=document.name,
                        role=upstream.role,
                        via_definition=definition.qualified_name,
                        process_steps=steps.get(unit.definition_id, ()),
                        via=upstream.via,
                        evidence=upstream.evidence,
                        write_evidence=unit.evidence,
                        reviews=unit.reviews,
                    )
                )
    return _Network(nodes, tuple(sorted(edges, key=_edge_key)))


def _add_graph_nodes(
    graph: GraphDocument,
    nodes: dict[str, _Node],
    edges: list[_Edge],
) -> None:
    by_id = graph.node_by_id()
    graph_ids: dict[str, str] = {}
    for node in graph.nodes:
        if node.type not in {"field", "table", "view"}:
            continue
        node_id = f"graph:{graph.name}:{node.id}"
        graph_ids[node.id] = node_id
        reference, aliases = _graph_reference(graph, node, by_id)
        annotation = node.annotation
        description = None
        annotation_state = None
        if annotation and annotation.state in DEFAULT_CONTEXT_ANNOTATION_STATES:
            description = annotation.description
            description_kind = "semantic_annotation"
            annotation_state = annotation.state
        else:
            technical = node.metadata.get("technical_description")
            description = (
                technical.strip()
                if isinstance(technical, str) and technical.strip()
                else None
            )
            description_kind = "technical_metadata" if description is not None else None
        nodes[node_id] = _Node(
            LineageReference(
                id=node_id,
                reference=reference,
                name=node.label,
                kind=node.type,
                source=f"graph:{graph.name}",
                description=description,
                description_kind=description_kind,
                annotation_state=annotation_state,
            ),
            aliases,
        )
    for node in graph.nodes:
        if node.type != "field" or node.id not in graph_ids:
            continue
        parent = node.metadata.get("object_id")
        if not isinstance(parent, str) or parent not in graph_ids:
            continue
        edges.append(
            _Edge(
                id=f"graph:{graph.name}:field-scope:{node.id}",
                source_id=graph_ids[parent],
                target_id=graph_ids[node.id],
                relation="field_of",
                state="observed",
                granularity_change="field_to_object",
            )
        )


def _graph_reference(
    graph: GraphDocument,
    node: GraphNode,
    by_id: dict[str, GraphNode],
) -> tuple[str, tuple[str, ...]]:
    if node.type in {"table", "view"}:
        reference = f"{graph.catalog}.{node.label}"
        return reference, (node.id, node.label, reference)
    parent_id = node.metadata.get("object_id")
    parent = by_id.get(parent_id) if isinstance(parent_id, str) else None
    if parent is None:
        reference = f"{graph.catalog}.{node.label}"
        return reference, (node.id, reference)
    local = f"{parent.label}.{node.label}"
    reference = f"{graph.catalog}.{local}"
    return reference, (node.id, local, reference)


def _steps_by_definition(document: LineageDocument) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for step in document.steps:
        values.setdefault(step.definition_id, []).append(step.name)
    return {key: tuple(items) for key, items in values.items()}


def _alias_map(nodes: dict[str, _Node]) -> dict[str, tuple[str, ...]]:
    values: dict[str, list[str]] = {}
    for node in nodes.values():
        for alias in node.aliases:
            values.setdefault(_normalize(alias), []).append(node.value.id)
    return {key: tuple(sorted(set(ids))) for key, ids in values.items()}


def _resolve_or_add(
    reference: str,
    nodes: dict[str, _Node],
    aliases: dict[str, tuple[str, ...]],
) -> str:
    matches = aliases.get(_normalize(reference), ())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        labels = sorted(nodes[item].value.reference for item in matches)
        preview = ", ".join(labels[:5])
        raise LineageFailure(
            "ambiguous_lineage_reference",
            f"Lineage reference is ambiguous: {reference}. Candidates: {preview}",
        )
    node_id = f"external:{_normalize(reference)}"
    if node_id not in nodes:
        nodes[node_id] = _Node(
            LineageReference(
                id=node_id,
                reference=reference,
                name=reference,
                kind="unresolved",
                source="external",
            ),
            (reference,),
        )
        aliases[_normalize(reference)] = (node_id,)
    return node_id


def _resolve_start(network: _Network, reference: str) -> str:
    normalized = _normalize(reference)
    exact = network.aliases().get(normalized, ())
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise _ambiguous(reference, exact, network)
    raise LineageFailure(
        "lineage_reference_not_found",
        f"Exact lineage reference not found: {reference}. Use lineage find first.",
    )


def _ambiguous(reference: str, matches: tuple[str, ...], network: _Network) -> LineageFailure:
    labels = sorted(network.nodes[item].value.reference for item in matches)
    preview = ", ".join(labels[:5])
    return LineageFailure(
        "ambiguous_lineage_reference",
        f"Lineage reference is ambiguous: {reference}. Candidates: {preview}",
    )


def _normalize(value: str) -> str:
    unquoted = value.translate(str.maketrans("", "", '[]"`'))
    return re.sub(r"\s*\.\s*", ".", unquoted.strip()).casefold()


def _edge_key(edge: _Edge) -> tuple[object, ...]:
    return (
        edge.target_id,
        edge.source_id,
        edge.relation,
        edge.lineage or "",
        edge.via_definition or "",
        edge.role or "",
        edge.state,
        _evidence_key(edge.write_evidence),
        _evidence_key(edge.evidence),
        edge.id,
    )


def _hop_key(hop: LineageHop) -> tuple[object, ...]:
    return (
        hop.depth,
        hop.target.reference.casefold(),
        hop.source.reference.casefold(),
        hop.relation,
        hop.lineage or "",
        hop.via_definition or "",
        hop.state,
        _evidence_key(hop.write_evidence),
        _evidence_key(hop.evidence),
        hop.id,
    )


def _evidence_key(evidence: LineageEvidence | None) -> tuple[object, ...]:
    if evidence is None:
        return ("", "", 0, 0, "")
    return (
        evidence.source,
        evidence.reference,
        evidence.line_start,
        evidence.line_end,
        evidence.reason,
    )


def _write_source_edge_id(document: str, unit: str, source: LineageWriteSource) -> str:
    identity = "\x1f".join(
        (
            _normalize(source.target),
            source.role,
            *source.via,
            source.evidence.source,
            source.evidence.reference,
            str(source.evidence.line_start),
            str(source.evidence.line_end),
            source.evidence.reason,
        )
    )
    suffix = sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"lineage:{document}:write:{unit}:source:{suffix}"
