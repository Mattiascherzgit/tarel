"""Atomic local persistence for semantic source snapshots and mappings."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from tarel.semantics.contracts import (
    SemanticFailure,
    SemanticImportDocument,
    SourceSnapshot,
    validate_semantic_import,
)


class SemanticImportStore(Protocol):
    def save(self, document: SemanticImportDocument) -> Path | str | None: ...

    def load(self, name: str) -> SemanticImportDocument: ...

    def list(self) -> tuple[str, ...]: ...

    def exists(self, name: str) -> bool: ...


class FileSemanticImportStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "semantic-imports"

    def save(self, document: SemanticImportDocument) -> Path:
        validate_semantic_import(document)
        path = self.path(document.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            document.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".semantic-import-",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise SemanticFailure(
                "semantic_import_save_failed",
                f"Could not save semantic import: {document.name}",
            ) from exc
        return path

    def load(self, name: str) -> SemanticImportDocument:
        try:
            payload = json.loads(self.path(name).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SemanticFailure(
                "semantic_import_not_found",
                f"Semantic import not found: {name}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticFailure(
                "invalid_semantic_import",
                f"Could not read semantic import: {name}",
            ) from exc
        if not isinstance(payload, dict):
            raise SemanticFailure(
                "invalid_semantic_import",
                f"Semantic import root must be an object: {name}",
            )
        document = SemanticImportDocument.from_dict(payload)
        if document.name != name:
            raise SemanticFailure(
                "invalid_semantic_import",
                "Stored semantic import name does not match its directory.",
            )
        return document

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                path.parent.name
                for path in self.root.glob("*/semantic-import.json")
                if path.is_file()
            )
        )

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def path(self, name: str) -> Path:
        probe = SemanticImportDocument(
            name=name,
            graph_name="probe",
            format_name="apache-ossie",
            format_version="probe",
            snapshot=_empty_snapshot(),
            models=(),
        )
        validate_semantic_import(probe)
        return self.root / name / "semantic-import.json"


def _empty_snapshot() -> SourceSnapshot:
    return SourceSnapshot.from_content("{}", media_type="application/json")
