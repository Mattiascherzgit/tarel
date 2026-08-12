"""Atomic local JSON persistence for knowledge attachments."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from tarel.knowledge.contracts import (
    KnowledgeDocument,
    KnowledgeFailure,
    validate_knowledge_document,
)

_KNOWLEDGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class KnowledgeStore(Protocol):
    def save(self, document: KnowledgeDocument) -> Path | str | None: ...

    def load(self, document_id: str) -> KnowledgeDocument: ...

    def list(self) -> tuple[str, ...]: ...


class FileKnowledgeStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "knowledge"

    def save(self, document: KnowledgeDocument) -> Path:
        validate_knowledge_document(document)
        path = self.path(document.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".knowledge-",
            suffix=".tmp",
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            os.replace(temporary_path, path)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise KnowledgeFailure(
                "knowledge_save_failed",
                f"Could not save knowledge document: {document.id}",
            ) from exc
        return path

    def load(self, document_id: str) -> KnowledgeDocument:
        path = self.path(document_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise KnowledgeFailure(
                "knowledge_not_found",
                f"Knowledge document not found: {document_id}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise KnowledgeFailure(
                "invalid_knowledge",
                f"Could not read knowledge document: {document_id}",
            ) from exc
        if not isinstance(data, dict):
            raise KnowledgeFailure("invalid_knowledge", "Knowledge root must be an object.")
        return KnowledgeDocument.from_dict(data)

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                path.parent.name
                for path in self.root.glob("*/document.json")
                if path.is_file()
            )
        )

    def path(self, document_id: str) -> Path:
        if not _KNOWLEDGE_ID.fullmatch(document_id):
            raise KnowledgeFailure(
                "invalid_knowledge_id",
                "Knowledge IDs may contain letters, numbers, dots, underscores, and hyphens.",
            )
        return self.root / document_id / "document.json"
