"""Versioned contracts for bounded annotation reference documents."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

KNOWLEDGE_CONTRACT_VERSION = "tarel.knowledge.v0.1"
KNOWLEDGE_STATES = frozenset({"draft", "validated"})
KNOWLEDGE_SCOPE_KINDS = frozenset({"global", "system", "graph", "schema", "object"})
DEFAULT_MAX_KNOWLEDGE_CHARACTERS = 12_000
MAX_KNOWLEDGE_CHARACTERS = 100_000
MAX_KNOWLEDGE_DOCUMENT_BYTES = 256 * 1024
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class KnowledgeFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class KnowledgeScope:
    kind: str
    reference: str
    graph: str | None = None
    workspace: str | None = None

    @classmethod
    def parse(cls, value: str) -> KnowledgeScope:
        clean = value.strip()
        if clean == "global":
            return cls(kind="global", reference="*")
        kind, separator, remainder = clean.partition(":")
        if not separator or kind not in KNOWLEDGE_SCOPE_KINDS - {"global"}:
            raise KnowledgeFailure(
                "invalid_knowledge_scope",
                "Knowledge scope must be global, system:NAME, graph:NAME, "
                "schema:GRAPH:NAMESPACE, or object:GRAPH:OBJECT.",
            )
        if kind in {"system", "graph"}:
            if not remainder:
                raise KnowledgeFailure("invalid_knowledge_scope", "Knowledge scope is incomplete.")
            return cls(kind=kind, reference=remainder)
        graph, graph_separator, reference = remainder.partition(":")
        if not graph_separator or not graph or not reference:
            raise KnowledgeFailure(
                "invalid_knowledge_scope",
                f"{kind} knowledge scope requires GRAPH and a reference.",
            )
        return cls(kind=kind, graph=graph, reference=reference)

    def to_dict(self) -> dict[str, object]:
        return {
            "graph": self.graph,
            "kind": self.kind,
            "reference": self.reference,
            "workspace": self.workspace,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeScope:
        scope = cls(
            kind=_required_string(data, "kind"),
            reference=_required_string(data, "reference"),
            graph=_optional_string(data.get("graph")),
            workspace=_optional_string(data.get("workspace")),
        )
        _validate_scope(scope)
        return scope

    def label(self) -> str:
        if self.kind == "global":
            return "global"
        if self.kind == "system" and self.workspace is not None:
            return f"system:{self.workspace}:{self.reference}"
        if self.graph is not None:
            return f"{self.kind}:{self.graph}:{self.reference}"
        return f"{self.kind}:{self.reference}"


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    id: str
    title: str
    scope: KnowledgeScope
    content: str
    source_name: str
    state: str = "draft"
    contract_version: str = KNOWLEDGE_CONTRACT_VERSION

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    @property
    def revision(self) -> str:
        payload = json.dumps(
            {
                "content_hash": self.content_hash,
                "id": self.id,
                "scope": self.scope.to_dict(),
                "source_name": self.source_name,
                "state": self.state,
                "title": self.title,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self, *, include_content: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "characters": len(self.content),
            "content_hash": self.content_hash,
            "contract_version": self.contract_version,
            "id": self.id,
            "revision": self.revision,
            "scope": self.scope.to_dict(),
            "source_name": self.source_name,
            "state": self.state,
            "title": self.title,
        }
        if include_content:
            payload["content"] = self.content
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeDocument:
        if data.get("contract_version") != KNOWLEDGE_CONTRACT_VERSION:
            raise KnowledgeFailure(
                "unsupported_knowledge",
                "Unsupported TAREL knowledge contract.",
            )
        scope = data.get("scope")
        if not isinstance(scope, dict):
            raise KnowledgeFailure("invalid_knowledge", "Knowledge scope must be an object.")
        document = cls(
            id=_required_string(data, "id"),
            title=_required_string(data, "title"),
            scope=KnowledgeScope.from_dict(scope),
            content=_required_string(data, "content", strip=False),
            source_name=_required_string(data, "source_name"),
            state=_required_string(data, "state"),
        )
        validate_knowledge_document(document)
        expected_hash = data.get("content_hash")
        if expected_hash is not None and expected_hash != document.content_hash:
            raise KnowledgeFailure(
                "invalid_knowledge",
                f"Knowledge content hash does not match: {document.id}",
            )
        expected_revision = data.get("revision")
        if expected_revision is not None and expected_revision != document.revision:
            raise KnowledgeFailure(
                "invalid_knowledge",
                f"Knowledge revision does not match: {document.id}",
            )
        return document


@dataclass(frozen=True, slots=True)
class KnowledgeReference:
    id: str
    title: str
    scope: KnowledgeScope
    state: str
    revision: str
    characters: int
    truncated: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "characters": self.characters,
            "id": self.id,
            "revision": self.revision,
            "scope": self.scope.to_dict(),
            "state": self.state,
            "title": self.title,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeReference:
        scope = data.get("scope")
        if not isinstance(scope, dict):
            raise KnowledgeFailure("invalid_knowledge", "Knowledge reference scope is invalid.")
        characters = data.get("characters")
        truncated = data.get("truncated")
        if isinstance(characters, bool) or not isinstance(characters, int) or characters < 0:
            raise KnowledgeFailure("invalid_knowledge", "Knowledge character count is invalid.")
        if not isinstance(truncated, bool):
            raise KnowledgeFailure("invalid_knowledge", "Knowledge truncation flag is invalid.")
        state = _required_string(data, "state")
        if state not in KNOWLEDGE_STATES:
            raise KnowledgeFailure("invalid_knowledge", "Knowledge state is invalid.")
        return cls(
            id=_required_string(data, "id"),
            title=_required_string(data, "title"),
            scope=KnowledgeScope.from_dict(scope),
            state=state,
            revision=_required_string(data, "revision"),
            characters=characters,
            truncated=truncated,
        )


@dataclass(frozen=True, slots=True)
class ResolvedKnowledgeDocument:
    reference: KnowledgeReference
    content: str

    def to_dict(self, *, include_content: bool = True) -> dict[str, object]:
        payload = self.reference.to_dict()
        if include_content:
            payload["content"] = self.content
        return payload


@dataclass(frozen=True, slots=True)
class KnowledgeContext:
    documents: tuple[ResolvedKnowledgeDocument, ...] = ()
    omitted: tuple[str, ...] = ()
    max_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS

    @property
    def references(self) -> tuple[KnowledgeReference, ...]:
        return tuple(item.reference for item in self.documents)

    @property
    def characters(self) -> int:
        return sum(len(item.content) for item in self.documents)

    def to_dict(self, *, include_content: bool = True) -> dict[str, object]:
        return {
            "characters": self.characters,
            "documents": [
                item.to_dict(include_content=include_content) for item in self.documents
            ],
            "max_characters": self.max_characters,
            "omitted": list(self.omitted),
        }


def validate_knowledge_document(document: KnowledgeDocument) -> None:
    if document.contract_version != KNOWLEDGE_CONTRACT_VERSION:
        raise KnowledgeFailure("unsupported_knowledge", "Unsupported knowledge contract.")
    if not _IDENTIFIER.fullmatch(document.id):
        raise KnowledgeFailure(
            "invalid_knowledge_id",
            "Knowledge IDs may contain letters, numbers, dots, underscores, and hyphens.",
        )
    if not document.title.strip() or not document.content.strip():
        raise KnowledgeFailure("invalid_knowledge", "Knowledge title and content are required.")
    if "/" in document.source_name or "\\" in document.source_name:
        raise KnowledgeFailure(
            "invalid_knowledge",
            "Knowledge source_name must be a file name without a path.",
        )
    if document.state not in KNOWLEDGE_STATES:
        raise KnowledgeFailure(
            "invalid_knowledge_state",
            "Knowledge state must be draft or validated.",
        )
    if len(document.content.encode("utf-8")) > MAX_KNOWLEDGE_DOCUMENT_BYTES:
        raise KnowledgeFailure(
            "knowledge_document_too_large",
            f"Knowledge documents may contain at most {MAX_KNOWLEDGE_DOCUMENT_BYTES} bytes.",
        )
    _validate_scope(document.scope)


def _validate_scope(scope: KnowledgeScope) -> None:
    if scope.kind not in KNOWLEDGE_SCOPE_KINDS:
        raise KnowledgeFailure("invalid_knowledge_scope", "Unknown knowledge scope kind.")
    if scope.kind == "global":
        if scope.reference != "*" or scope.graph is not None or scope.workspace is not None:
            raise KnowledgeFailure(
                "invalid_knowledge_scope",
                "Global scope must use reference '*'.",
            )
        return
    if scope.kind == "system":
        if scope.graph is not None or not scope.workspace:
            raise KnowledgeFailure(
                "invalid_knowledge_scope",
                "System scope must be bound to a workspace.",
            )
    elif scope.workspace is not None:
        raise KnowledgeFailure(
            "invalid_knowledge_scope",
            f"{scope.kind} scope cannot contain a workspace field.",
        )
    if scope.kind in {"schema", "object"}:
        if scope.graph is None:
            raise KnowledgeFailure(
                "invalid_knowledge_scope",
                f"{scope.kind} scope requires a graph.",
            )
    elif scope.kind != "system" and scope.graph is not None:
        raise KnowledgeFailure(
            "invalid_knowledge_scope",
            f"{scope.kind} scope cannot contain a graph field.",
        )
    if not scope.reference.strip() or (scope.graph is not None and not scope.graph.strip()):
        raise KnowledgeFailure("invalid_knowledge_scope", "Knowledge scope is incomplete.")


def _required_string(data: dict[str, Any], key: str, *, strip: bool = True) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeFailure("invalid_knowledge", f"Knowledge field is required: {key}")
    return value.strip() if strip else value


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise KnowledgeFailure("invalid_knowledge", "Optional knowledge field must be a string.")
    return value.strip() or None
