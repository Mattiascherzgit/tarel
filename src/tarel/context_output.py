"""Versioned stable and dynamic projections of compiled agent context."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from tarel.annotations.states import DEFAULT_CONTEXT_ANNOTATION_STATES

DEFAULT_MAX_CONTEXT_CHARACTERS = 24_000
CONTEXT_CONTRACT_VERSION = "tarel.context.v0.2"


def canonical_json(value: object) -> str:
    """Serialize a context value identically across CLI and SDK callers."""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ContextScope:
    """The implemented selection boundary for one context packet."""

    mode: str = "retrieval"
    namespace: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"mode": self.mode, "namespace": self.namespace}


@dataclass(frozen=True, slots=True)
class ContextField:
    id: str
    name: str
    data_type: str
    nullable: bool
    description: str | None
    role: str | None
    semantic_type: str | None
    annotation_state: str | None
    reasons: tuple[str, ...]

    def stable_dict(self) -> dict[str, object]:
        return {
            "annotation_state": self.annotation_state,
            "data_type": self.data_type,
            "description": self.description,
            "id": self.id,
            "name": self.name,
            "nullable": self.nullable,
            "role": self.role,
            "semantic_type": self.semantic_type,
        }

    def selection_dict(self) -> dict[str, object]:
        return {"id": self.id, "reasons": list(self.reasons)}


@dataclass(frozen=True, slots=True)
class ContextObject:
    id: str
    label: str
    type: str
    selection: str
    distance: int
    search_score: int | None
    search_reasons: tuple[str, ...]
    description: str | None
    role: str | None
    grain: str | None
    warnings: tuple[str, ...]
    annotation_state: str | None
    fields: tuple[ContextField, ...]
    omitted_fields: int

    def stable_dict(self) -> dict[str, object]:
        return {
            "annotation_state": self.annotation_state,
            "description": self.description,
            "fields": [
                field.stable_dict()
                for field in sorted(self.fields, key=lambda candidate: candidate.id)
            ],
            "grain": self.grain,
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "type": self.type,
            "warnings": list(self.warnings),
        }

    def selection_dict(self) -> dict[str, object]:
        return {
            "distance": self.distance,
            "fields": [field.selection_dict() for field in self.fields],
            "id": self.id,
            "omitted_fields": self.omitted_fields,
            "search_reasons": list(self.search_reasons),
            "search_score": self.search_score,
            "selection": self.selection,
        }


@dataclass(frozen=True, slots=True)
class ContextJoin:
    id: str
    kind: str
    from_object_id: str
    from_object: str
    from_fields: tuple[str, ...]
    to_object_id: str
    to_object: str
    to_fields: tuple[str, ...]
    state: str
    origin: str
    reason: str | None
    confidence: float | None

    def to_dict(self) -> dict[str, object]:
        return {
            "confidence": self.confidence,
            "from_fields": list(self.from_fields),
            "from_object": self.from_object,
            "from_object_id": self.from_object_id,
            "id": self.id,
            "kind": self.kind,
            "origin": self.origin,
            "reason": self.reason,
            "state": self.state,
            "to_fields": list(self.to_fields),
            "to_object": self.to_object,
            "to_object_id": self.to_object_id,
        }


@dataclass(frozen=True, slots=True)
class ContextPath:
    seed: str
    target: str
    objects: tuple[str, ...]
    joins: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "joins": list(self.joins),
            "objects": list(self.objects),
            "seed": self.seed,
            "target": self.target,
        }


@dataclass(frozen=True, slots=True)
class ContextOmissions:
    objects: int = 0
    fields: int = 0
    joins: int = 0
    paths: int = 0
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": self.fields,
            "joins": self.joins,
            "objects": self.objects,
            "paths": self.paths,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class ContextPacket:
    graph: str
    graph_revision: str
    scope: ContextScope
    query: str
    terms: tuple[str, ...]
    objects: tuple[ContextObject, ...]
    joins: tuple[ContextJoin, ...]
    paths: tuple[ContextPath, ...]
    seed_limit: int
    max_objects: int
    max_joins: int
    max_hops: int
    max_fields_per_object: int
    max_characters: int
    stable_characters: int
    context_characters: int
    omissions: ContextOmissions
    retrieval_mode: str = "lexical"
    annotation_states: frozenset[str] = DEFAULT_CONTEXT_ANNOTATION_STATES
    contract_version: str = CONTEXT_CONTRACT_VERSION

    def stable_dict(self) -> dict[str, object]:
        return {
            "graph": {"name": self.graph, "revision": self.graph_revision},
            "annotation_states": sorted(self.annotation_states),
            "joins": [
                join.to_dict() for join in sorted(self.joins, key=lambda candidate: candidate.id)
            ],
            "objects": [
                item.stable_dict()
                for item in sorted(self.objects, key=lambda candidate: candidate.id)
            ],
            "scope": self.scope.to_dict(),
        }

    def dynamic_dict(self) -> dict[str, object]:
        return {
            "budgets": {
                "max_characters": self.max_characters,
                "max_fields_per_object": self.max_fields_per_object,
                "max_hops": self.max_hops,
                "max_joins": self.max_joins,
                "max_objects": self.max_objects,
                "seed_limit": self.seed_limit,
                "context_characters": self.context_characters,
                "stable_characters": self.stable_characters,
            },
            "omissions": self.omissions.to_dict(),
            "paths": [path.to_dict() for path in self.paths],
            "query": self.query,
            "retrieval": {"mode": self.retrieval_mode, "terms": list(self.terms)},
            "selection": [item.selection_dict() for item in self.objects],
        }

    def to_dict(self) -> dict[str, object]:
        stable = self.stable_dict()
        dynamic = self.dynamic_dict()
        return {
            "contract_version": self.contract_version,
            "stable": stable,
            "dynamic": dynamic,
            "identity": self.identity_dict(),
        }

    def identity_dict(self) -> dict[str, str]:
        stable_hash = self.stable_hash
        dynamic_hash = self.dynamic_hash
        return {
            "dynamic_hash": dynamic_hash,
            "packet_hash": canonical_hash(
                {
                    "contract_version": self.contract_version,
                    "dynamic_hash": dynamic_hash,
                    "stable_hash": stable_hash,
                }
            ),
            "stable_hash": stable_hash,
        }

    @property
    def stable_hash(self) -> str:
        return canonical_hash(self.stable_dict())

    @property
    def dynamic_hash(self) -> str:
        return canonical_hash(self.dynamic_dict())

    @property
    def packet_hash(self) -> str:
        return self.identity_dict()["packet_hash"]

    def canonical_json(self) -> str:
        # The contract defines this top-level order for prefix-cache reuse. Nested
        # collections are already constructed deterministically by the packet.
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        )


# The pre-0.2 application name remains import-compatible while callers migrate to
# the more precise public name.
ContextResult = ContextPacket
