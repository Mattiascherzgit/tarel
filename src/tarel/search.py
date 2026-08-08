"""Deterministic lexical search over graph objects and their fields."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from tarel.annotations.states import (
    DEFAULT_CONTEXT_ANNOTATION_STATES,
    annotation_is_visible,
)
from tarel.graph.contracts import GraphDocument, GraphNode

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "by",
    "describe",
    "find",
    "for",
    "from",
    "in",
    "of",
    "on",
    "or",
    "show",
    "the",
    "to",
    "which",
    "with",
}
_MAX_FIELD_MATCHES = 8


class SearchFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FieldSearchHit:
    id: str
    label: str
    score: int
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "reasons": list(self.reasons),
            "score": self.score,
        }


@dataclass(frozen=True, slots=True)
class SearchHit:
    id: str
    label: str
    type: str
    score: int
    matched_terms: tuple[str, ...]
    reasons: tuple[str, ...]
    fields: tuple[FieldSearchHit, ...]
    source_graph: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "fields": [field.to_dict() for field in self.fields],
            "id": self.id,
            "label": self.label,
            "matched_terms": list(self.matched_terms),
            "reasons": list(self.reasons),
            "score": self.score,
            "type": self.type,
        }
        if self.source_graph is not None:
            payload["source_graph"] = self.source_graph
        return payload


@dataclass(frozen=True, slots=True)
class SearchResults:
    graph: str
    query: str
    terms: tuple[str, ...]
    hits: tuple[SearchHit, ...]
    mode: str = "lexical"
    workspace: str | None = None
    graphs: tuple[str, ...] = ()
    scope_hash: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "graph": self.graph,
            "hits": [hit.to_dict() for hit in self.hits],
            "mode": self.mode,
            "query": self.query,
            "terms": list(self.terms),
        }
        if self.workspace is not None:
            payload.update(
                {
                    "graphs": list(self.graphs),
                    "scope_hash": self.scope_hash,
                    "workspace": self.workspace,
                }
            )
        return payload


def search_graph(
    graph: GraphDocument,
    query: str,
    *,
    limit: int = 20,
    namespace: str | None = None,
    object_ids: frozenset[str] | None = None,
    annotation_states: frozenset[str] = DEFAULT_CONTEXT_ANNOTATION_STATES,
) -> SearchResults:
    """Rank tables and views using only persisted graph metadata."""
    if not 1 <= limit <= 100:
        raise SearchFailure("invalid_limit", "Search limit must be between 1 and 100.")
    terms = _query_terms(query)
    if not terms:
        raise SearchFailure("empty_query", "Search query must contain a meaningful term.")

    fields_by_object: dict[str, list[GraphNode]] = {}
    for node in graph.nodes:
        object_id = node.metadata.get("object_id")
        if node.type == "field" and isinstance(object_id, str):
            fields_by_object.setdefault(object_id, []).append(node)

    hits: list[SearchHit] = []
    for node in graph.nodes:
        if node.type not in {"table", "view"}:
            continue
        if object_ids is not None and node.id not in object_ids:
            continue
        node_namespace = str(node.metadata.get("namespace") or "")
        if namespace is not None and node_namespace.casefold() != namespace.casefold():
            continue
        hit = _score_object(
            node,
            fields_by_object.get(node.id, []),
            terms,
            annotation_states=annotation_states,
        )
        if hit is not None:
            hits.append(hit)

    ranked = sorted(hits, key=lambda hit: (-hit.score, hit.label.casefold(), hit.id))[:limit]
    return SearchResults(graph=graph.name, query=query, terms=terms, hits=tuple(ranked))


def _score_object(
    node: GraphNode,
    fields: list[GraphNode],
    terms: tuple[str, ...],
    *,
    annotation_states: frozenset[str],
) -> SearchHit | None:
    object_sources = _object_sources(node, annotation_states)
    field_matches = [_score_field(field, terms, annotation_states) for field in fields]
    field_hits = [hit for hit in field_matches if hit is not None]
    best_by_term: dict[str, tuple[int, str]] = {}

    for term in terms:
        for reason, text, weight in object_sources:
            if term in _tokens(text):
                _keep_strongest(best_by_term, term, weight, f"{reason}:{term}")
        for field in sorted(
            field_hits,
            key=lambda candidate: (-candidate.score, candidate.label.casefold(), candidate.id),
        ):
            for reason in field.reasons:
                if reason.endswith(f":{term}"):
                    _keep_strongest(
                        best_by_term,
                        term,
                        field.score_for_term(term),
                        f"field:{field.label}:{reason}",
                    )

    if not best_by_term:
        return None

    matched_terms = tuple(term for term in terms if term in best_by_term)
    score = sum(best_by_term[term][0] for term in matched_terms) + 2 * len(matched_terms)
    if len(matched_terms) == len(terms):
        score += 10
    object_name_tokens = _tokens(str(node.metadata.get("name") or node.label))
    if set(terms).issubset(object_name_tokens):
        score += 12

    ranked_fields = sorted(
        field_hits,
        key=lambda field: (-field.score, field.label.casefold(), field.id),
    )[:_MAX_FIELD_MATCHES]
    return SearchHit(
        id=node.id,
        label=node.label,
        type=node.type,
        score=score,
        matched_terms=matched_terms,
        reasons=tuple(best_by_term[term][1] for term in matched_terms),
        fields=tuple(
            FieldSearchHit(
                id=field.id,
                label=field.label,
                score=field.score,
                reasons=field.reasons,
            )
            for field in ranked_fields
        ),
    )


@dataclass(frozen=True, slots=True)
class _ScoredField:
    id: str
    label: str
    score: int
    reasons: tuple[str, ...]
    term_scores: tuple[tuple[str, int], ...]

    def score_for_term(self, term: str) -> int:
        return dict(self.term_scores)[term]


def _score_field(
    field: GraphNode,
    terms: tuple[str, ...],
    annotation_states: frozenset[str],
) -> _ScoredField | None:
    best_by_term: dict[str, tuple[int, str]] = {}
    for term in terms:
        for reason, text, weight in _field_sources(field, annotation_states):
            if term in _tokens(text):
                _keep_strongest(best_by_term, term, weight, f"{reason}:{term}")
    if not best_by_term:
        return None
    matched_terms = tuple(term for term in terms if term in best_by_term)
    return _ScoredField(
        id=field.id,
        label=field.label,
        score=sum(best_by_term[term][0] for term in matched_terms),
        reasons=tuple(best_by_term[term][1] for term in matched_terms),
        term_scores=tuple((term, best_by_term[term][0]) for term in matched_terms),
    )


def _object_sources(
    node: GraphNode,
    annotation_states: frozenset[str],
) -> tuple[tuple[str, str, int], ...]:
    annotation = (
        node.annotation if annotation_is_visible(node.annotation, annotation_states) else None
    )
    semantic_metadata_visible = node.annotation is None or annotation is not None
    return (
        ("object_name", str(node.metadata.get("name") or node.label), 12),
        ("object_description", _annotation_value(annotation, "description"), 8),
        ("object_synonym", " ".join(annotation.synonyms) if annotation else "", 10),
        ("object_role", (annotation.role or "") if annotation else "", 6),
        ("technical_description", str(node.metadata.get("technical_description") or ""), 5),
        (
            "grain",
            str(node.metadata.get("grain") or "") if semantic_metadata_visible else "",
            5,
        ),
    )


def _field_sources(
    field: GraphNode,
    annotation_states: frozenset[str],
) -> tuple[tuple[str, str, int], ...]:
    annotation = (
        field.annotation if annotation_is_visible(field.annotation, annotation_states) else None
    )
    semantic_metadata_visible = field.annotation is None or annotation is not None
    return (
        ("name", field.label, 9),
        ("synonym", " ".join(annotation.synonyms) if annotation else "", 8),
        ("description", _annotation_value(annotation, "description"), 7),
        ("role", (annotation.role or "") if annotation else "", 5),
        (
            "semantic_type",
            str(field.metadata.get("semantic_type") or "")
            if semantic_metadata_visible
            else "",
            5,
        ),
        ("technical_description", str(field.metadata.get("technical_description") or ""), 4),
    )


def _annotation_value(annotation: object, attribute: str) -> str:
    value = getattr(annotation, attribute, "") if annotation else ""
    return value if isinstance(value, str) else ""


def _keep_strongest(
    matches: dict[str, tuple[int, str]],
    term: str,
    score: int,
    reason: str,
) -> None:
    current = matches.get(term)
    if current is None or score > current[0]:
        matches[term] = (score, reason)


def _query_terms(value: str) -> tuple[str, ...]:
    return tuple(sorted(_tokens(value) - _STOP_WORDS))


def _tokens(value: str) -> set[str]:
    split = _CAMEL_BOUNDARY.sub(" ", value)
    folded = unicodedata.normalize("NFKD", split.replace("ß", "ss"))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    return {
        normalized
        for token in _NON_ALPHANUMERIC.sub(" ", ascii_text).split()
        if (normalized := _singular(token))
    }


def _singular(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return f"{token[:-3]}y"
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token
