"""Atomic persistence for immutable runtime-lineage documents."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from tarel.lineage.contracts import LineageFailure
from tarel.lineage.runtime import (
    RuntimeLineageDocument,
    validate_runtime_lineage_document,
)

_RUNTIME_LINEAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class FileRuntimeLineageStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "runtime-lineage"

    def create(self, document: RuntimeLineageDocument) -> Path:
        validate_runtime_lineage_document(document)
        path = self.path(document.name)
        if path.exists():
            raise LineageFailure(
                "runtime_lineage_exists",
                f"Runtime lineage already exists: {document.name}.",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".runtime-lineage-",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            payload = json.dumps(
                document.to_dict(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            try:
                os.link(temporary, path)
            except FileExistsError as exc:
                raise LineageFailure(
                    "runtime_lineage_exists",
                    f"Runtime lineage already exists: {document.name}.",
                ) from exc
            finally:
                temporary.unlink(missing_ok=True)
        except LineageFailure:
            temporary.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise LineageFailure(
                "runtime_lineage_save_failed",
                f"Could not save runtime lineage: {document.name}.",
            ) from exc
        return path

    def load(self, name: str) -> RuntimeLineageDocument:
        try:
            payload = json.loads(self.path(name).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LineageFailure(
                "runtime_lineage_not_found",
                f"Runtime lineage not found: {name}.",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise LineageFailure(
                "invalid_runtime_lineage",
                f"Could not read runtime lineage: {name}.",
            ) from exc
        if not isinstance(payload, dict):
            raise LineageFailure(
                "invalid_runtime_lineage",
                f"Runtime lineage root must be an object: {name}.",
            )
        document = RuntimeLineageDocument.from_dict(payload)
        if document.name != name:
            raise LineageFailure(
                "invalid_runtime_lineage",
                "Stored runtime-lineage name does not match its directory.",
            )
        return document

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(path.parent.name for path in self.root.glob("*/run.json") if path.is_file())
        )

    def path(self, name: str) -> Path:
        if not _RUNTIME_LINEAGE_NAME.fullmatch(name):
            raise LineageFailure(
                "invalid_runtime_lineage_name",
                "Runtime-lineage names may contain letters, numbers, dots, underscores, "
                "and hyphens.",
            )
        return self.root / name / "run.json"
