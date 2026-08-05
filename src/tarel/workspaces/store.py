"""Atomic local JSON workspace store."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from tarel.workspaces.contracts import (
    WorkspaceDocument,
    WorkspaceFailure,
    validate_workspace,
)

_WORKSPACE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class WorkspaceStore(Protocol):
    """Whole-document persistence boundary for local or shared workspace stores."""

    def save(self, workspace: WorkspaceDocument) -> Path | str | None: ...

    def load(self, name: str) -> WorkspaceDocument: ...

    def list(self) -> tuple[str, ...]: ...


class FileWorkspaceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "workspaces"

    def save(self, workspace: WorkspaceDocument) -> Path:
        validate_workspace(workspace)
        path = self.path(workspace.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(workspace.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".workspace-",
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
            raise WorkspaceFailure(
                "workspace_save_failed",
                f"Could not save workspace: {workspace.name}",
            ) from exc
        return path

    def load(self, name: str) -> WorkspaceDocument:
        path = self.path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise WorkspaceFailure(
                "workspace_not_found",
                f"Workspace not found: {name}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkspaceFailure(
                "invalid_workspace",
                f"Could not read workspace: {name}",
            ) from exc
        if not isinstance(data, dict):
            raise WorkspaceFailure(
                "invalid_workspace",
                f"Workspace root must be an object: {name}",
            )
        return WorkspaceDocument.from_dict(data)

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                path.parent.name
                for path in self.root.glob("*/workspace.json")
                if path.is_file()
            )
        )

    def path(self, name: str) -> Path:
        if not _WORKSPACE_NAME.fullmatch(name):
            raise WorkspaceFailure(
                "invalid_workspace_name",
                "Workspace names may contain letters, numbers, dots, underscores, and hyphens.",
            )
        return self.root / name / "workspace.json"
