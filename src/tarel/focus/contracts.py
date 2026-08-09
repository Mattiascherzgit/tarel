"""Dependency-free contracts for reproducible demand-driven focus snapshots."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

_CONTRACT_VERSION = "tarel.focus.v0.1"
_SOURCE_KINDS = frozenset({"graph", "lineage"})
_LINEAGE_STATES = frozenset({"draft", "rejected", "review_required", "validated"})


class FocusFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FocusSource:
    kind: str
    name: str
    revision: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "name": self.name, "revision": self.revision}


@dataclass(frozen=True, slots=True)
class FocusMember:
    id: str
    reference: str
    name: str
    kind: str
    source: str
    depth: int
    reasons: tuple[str, ...]
    origin: bool
    annotation_state: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "annotation_state": self.annotation_state,
            "depth": self.depth,
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "origin": self.origin,
            "reasons": list(self.reasons),
            "reference": self.reference,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class FocusHop:
    id: str
    depth: int
    source_id: str
    target_id: str
    relation: str
    state: str
    lineage: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "depth": self.depth,
            "id": self.id,
            "lineage": self.lineage,
            "relation": self.relation,
            "source_id": self.source_id,
            "state": self.state,
            "target_id": self.target_id,
        }


@dataclass(frozen=True, slots=True)
class FocusDocument:
    name: str
    seed: str
    seed_id: str
    max_hops: int
    states: tuple[str, ...]
    sources: tuple[FocusSource, ...]
    members: tuple[FocusMember, ...]
    hops: tuple[FocusHop, ...]
    warnings: tuple[str, ...]
    truncated: bool
    contract_version: str = _CONTRACT_VERSION

    @property
    def revision(self) -> str:
        payload = json.dumps(
            self.to_dict(include_revision=False),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self, *, include_revision: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "contract_version": self.contract_version,
            "hops": [item.to_dict() for item in self.hops],
            "max_hops": self.max_hops,
            "members": [item.to_dict() for item in self.members],
            "name": self.name,
            "seed": self.seed,
            "seed_id": self.seed_id,
            "sources": [item.to_dict() for item in self.sources],
            "states": list(self.states),
            "truncated": self.truncated,
            "warnings": list(self.warnings),
        }
        if include_revision:
            payload["revision"] = self.revision
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FocusDocument:
        if data.get("contract_version") != _CONTRACT_VERSION:
            raise FocusFailure("unsupported_focus", "Unsupported TAREL focus contract.")
        expected = {
            "contract_version",
            "hops",
            "max_hops",
            "members",
            "name",
            "revision",
            "seed",
            "seed_id",
            "sources",
            "states",
            "truncated",
            "warnings",
        }
        if set(data) != expected:
            raise FocusFailure("invalid_focus", "Invalid focus document fields.")
        document = cls(
            name=_text(data.get("name"), "name"),
            seed=_text(data.get("seed"), "seed"),
            seed_id=_text(data.get("seed_id"), "seed_id"),
            max_hops=_integer(data.get("max_hops"), "max_hops"),
            states=_strings(data.get("states"), "states"),
            sources=tuple(_source(item) for item in _objects(data.get("sources"), "sources")),
            members=tuple(_member(item) for item in _objects(data.get("members"), "members")),
            hops=tuple(_hop(item) for item in _objects(data.get("hops"), "hops")),
            warnings=_strings(data.get("warnings"), "warnings"),
            truncated=_boolean(data.get("truncated"), "truncated"),
        )
        validate_focus(document)
        if data.get("revision") != document.revision:
            raise FocusFailure("invalid_focus", "Focus revision does not match its contents.")
        return document


def validate_focus(document: FocusDocument) -> None:
    if not 1 <= document.max_hops <= 100:
        raise FocusFailure("invalid_focus", "Focus max_hops must be between 1 and 100.")
    if not document.states or set(document.states) - _LINEAGE_STATES:
        raise FocusFailure("invalid_focus", "Focus contains invalid lineage states.")
    if tuple(sorted(set(document.states))) != document.states:
        raise FocusFailure("invalid_focus", "Focus states must be unique and sorted.")
    source_keys = [(item.kind, item.name) for item in document.sources]
    if (
        len(source_keys) != len(set(source_keys))
        or tuple(sorted(source_keys)) != tuple(source_keys)
    ):
        raise FocusFailure("invalid_focus", "Focus sources must be unique and sorted.")
    for item in document.sources:
        if item.kind not in _SOURCE_KINDS:
            raise FocusFailure("invalid_focus", f"Unsupported focus source kind: {item.kind}")
        _revision(item.revision)
    member_ids = [item.id for item in document.members]
    if len(member_ids) != len(set(member_ids)) or document.seed_id not in set(member_ids):
        raise FocusFailure("invalid_focus", "Focus members or seed are invalid.")
    if tuple(sorted(document.members, key=_member_key)) != document.members:
        raise FocusFailure("invalid_focus", "Focus members must use deterministic order.")
    for item in document.members:
        if item.depth < 0 or not item.reasons:
            raise FocusFailure("invalid_focus", "Focus member depth or reasons are invalid.")
        if tuple(sorted(set(item.reasons))) != item.reasons:
            raise FocusFailure("invalid_focus", "Focus member reasons must be unique and sorted.")
    member_set = set(member_ids)
    hop_ids = [item.id for item in document.hops]
    if (
        len(hop_ids) != len(set(hop_ids))
        or tuple(sorted(document.hops, key=_hop_key)) != document.hops
    ):
        raise FocusFailure("invalid_focus", "Focus hops must be unique and sorted.")
    if any(
        item.source_id not in member_set or item.target_id not in member_set
        for item in document.hops
    ):
        raise FocusFailure("invalid_focus", "Focus hop references an unknown member.")


def focus_member_key(item: FocusMember) -> tuple[int, str, str]:
    return _member_key(item)


def focus_hop_key(item: FocusHop) -> tuple[int, str, str, str, str]:
    return _hop_key(item)


def _source(data: dict[str, Any]) -> FocusSource:
    _fields(data, {"kind", "name", "revision"}, "source")
    return FocusSource(
        kind=_text(data.get("kind"), "source kind"),
        name=_text(data.get("name"), "source name"),
        revision=_text(data.get("revision"), "source revision"),
    )


def _member(data: dict[str, Any]) -> FocusMember:
    _fields(
        data,
        {
            "annotation_state",
            "depth",
            "id",
            "kind",
            "name",
            "origin",
            "reasons",
            "reference",
            "source",
        },
        "member",
    )
    state = data.get("annotation_state")
    if state is not None and not isinstance(state, str):
        raise FocusFailure("invalid_focus", "Focus annotation_state must be a string or null.")
    return FocusMember(
        id=_text(data.get("id"), "member id"),
        reference=_text(data.get("reference"), "member reference"),
        name=_text(data.get("name"), "member name"),
        kind=_text(data.get("kind"), "member kind"),
        source=_text(data.get("source"), "member source"),
        depth=_integer(data.get("depth"), "member depth", minimum=0),
        reasons=_strings(data.get("reasons"), "member reasons"),
        origin=_boolean(data.get("origin"), "member origin"),
        annotation_state=state,
    )


def _hop(data: dict[str, Any]) -> FocusHop:
    _fields(
        data,
        {"depth", "id", "lineage", "relation", "source_id", "state", "target_id"},
        "hop",
    )
    lineage = data.get("lineage")
    if lineage is not None and not isinstance(lineage, str):
        raise FocusFailure("invalid_focus", "Focus hop lineage must be a string or null.")
    return FocusHop(
        id=_text(data.get("id"), "hop id"),
        depth=_integer(data.get("depth"), "hop depth", minimum=1),
        source_id=_text(data.get("source_id"), "hop source_id"),
        target_id=_text(data.get("target_id"), "hop target_id"),
        relation=_text(data.get("relation"), "hop relation"),
        state=_text(data.get("state"), "hop state"),
        lineage=lineage,
    )


def _fields(data: dict[str, Any], expected: set[str], label: str) -> None:
    if set(data) != expected:
        raise FocusFailure("invalid_focus", f"Invalid focus {label} fields.")


def _objects(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise FocusFailure("invalid_focus", f"Focus {label} must be an array of objects.")
    return value


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise FocusFailure("invalid_focus", f"Focus {label} must be an array of strings.")
    return tuple(value)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FocusFailure("invalid_focus", f"Focus {label} must be a non-empty string.")
    return value


def _integer(value: Any, label: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise FocusFailure("invalid_focus", f"Focus {label} must be an integer >= {minimum}.")
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise FocusFailure("invalid_focus", f"Focus {label} must be a boolean.")
    return value


def _revision(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise FocusFailure("invalid_focus", "Focus source revision must be SHA-256.")


def _member_key(item: FocusMember) -> tuple[int, str, str]:
    return item.depth, item.reference.casefold(), item.id


def _hop_key(item: FocusHop) -> tuple[int, str, str, str, str]:
    return item.depth, item.target_id, item.source_id, item.relation, item.id
