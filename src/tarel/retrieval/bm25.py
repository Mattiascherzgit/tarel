"""Dependency-free BM25 used by local and hybrid retrieval."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from tarel.retrieval.contracts import RankedDocument, RetrievalDocument

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "bei",
    "by",
    "das",
    "der",
    "die",
    "ein",
    "eine",
    "for",
    "from",
    "für",
    "in",
    "is",
    "mit",
    "of",
    "on",
    "or",
    "the",
    "to",
    "und",
    "von",
    "with",
}


def tokenize(value: str) -> tuple[str, ...]:
    split = _CAMEL_BOUNDARY.sub(" ", value)
    folded = unicodedata.normalize("NFKD", split.replace("ß", "ss"))
    ascii_text = folded.encode("ascii", "ignore").decode("ascii").lower()
    return tuple(
        token
        for token in _NON_ALPHANUMERIC.sub(" ", ascii_text).split()
        if token and token not in _STOP_WORDS
    )


def rank_bm25(
    documents: tuple[RetrievalDocument, ...],
    query: str,
    *,
    limit: int,
) -> tuple[RankedDocument, ...]:
    query_terms = tokenize(query)
    if not query_terms or not documents:
        return ()
    document_terms = [tokenize(f"{document.label} {document.text}") for document in documents]
    frequencies: Counter[str] = Counter()
    for terms in document_terms:
        frequencies.update(set(terms))
    average_length = sum(len(terms) for terms in document_terms) / len(document_terms)

    ranked: list[RankedDocument] = []
    for document, terms in zip(documents, document_terms, strict=True):
        counts = Counter(terms)
        score = sum(
            _term_score(
                term_count=counts[term],
                document_length=len(terms),
                average_length=average_length,
                document_count=len(documents),
                document_frequency=frequencies[term],
            )
            for term in query_terms
            if counts[term]
        )
        if score > 0:
            ranked.append(RankedDocument(document=document, score=score, sources=("bm25",)))
    return tuple(
        sorted(
            ranked,
            key=lambda item: (-item.score, item.document.label.casefold(), item.document.id),
        )[:limit]
    )


def _term_score(
    *,
    term_count: int,
    document_length: int,
    average_length: float,
    document_count: int,
    document_frequency: int,
) -> float:
    k1 = 1.5
    b = 0.75
    inverse_frequency = math.log(
        1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
    )
    denominator = term_count + k1 * (
        1 - b + b * document_length / max(average_length, 1.0)
    )
    return inverse_frequency * (term_count * (k1 + 1)) / denominator
