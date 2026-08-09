"""Atomic local JSON persistence for focus snapshots."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from tarel.focus.contracts import FocusDocument, FocusFailure, validate_focus

_FOCUS_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class FileFocusStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "focus"

    def save(self, document: FocusDocument) -> Path:
        validate_focus(document)
        path = self.path(document.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(document.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".focus-",
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
            raise FocusFailure(
                "focus_save_failed",
                f"Could not save focus: {document.name}",
            ) from exc
        return path

    def load(self, name: str) -> FocusDocument:
        try:
            payload = json.loads(self.path(name).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FocusFailure("focus_not_found", f"Focus not found: {name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise FocusFailure("invalid_focus", f"Could not read focus: {name}") from exc
        if not isinstance(payload, dict):
            raise FocusFailure("invalid_focus", f"Focus root must be an object: {name}")
        document = FocusDocument.from_dict(payload)
        if document.name != name:
            raise FocusFailure("invalid_focus", "Stored focus name does not match its directory.")
        return document

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(path.parent.name for path in self.root.glob("*/focus.json") if path.is_file())
        )

    def path(self, name: str) -> Path:
        if not _FOCUS_NAME.fullmatch(name):
            raise FocusFailure(
                "invalid_focus_name",
                "Focus names may contain letters, numbers, dots, underscores, and hyphens.",
            )
        return self.root / name / "focus.json"
