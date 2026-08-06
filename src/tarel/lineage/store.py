"""Atomic local JSON persistence for lineage documents."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from tarel.lineage.contracts import (
    LineageDocument,
    LineageFailure,
    validate_lineage_document,
)

_LINEAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class LineageStore(Protocol):
    def save(self, document: LineageDocument) -> Path | str | None: ...

    def load(self, name: str) -> LineageDocument: ...

    def list(self) -> tuple[str, ...]: ...


class FileLineageStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "lineage"

    def save(self, document: LineageDocument) -> Path:
        validate_lineage_document(document)
        path = self.path(document.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".lineage-",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            os.replace(temporary, path)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise LineageFailure(
                "lineage_save_failed",
                f"Could not save lineage: {document.name}",
            ) from exc
        return path

    def load(self, name: str) -> LineageDocument:
        try:
            payload = json.loads(self.path(name).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LineageFailure("lineage_not_found", f"Lineage not found: {name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise LineageFailure("invalid_lineage", f"Could not read lineage: {name}") from exc
        if not isinstance(payload, dict):
            raise LineageFailure("invalid_lineage", f"Lineage root must be an object: {name}")
        document = LineageDocument.from_dict(payload)
        if document.name != name:
            raise LineageFailure(
                "invalid_lineage",
                "Stored lineage name does not match its directory.",
            )
        return document

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(path.parent.name for path in self.root.glob("*/lineage.json") if path.is_file())
        )

    def path(self, name: str) -> Path:
        if not _LINEAGE_NAME.fullmatch(name):
            raise LineageFailure(
                "invalid_lineage_name",
                "Lineage names may contain letters, numbers, dots, underscores, and hyphens.",
            )
        return self.root / name / "lineage.json"
