"""Atomic filesystem persistence for private logical source profiles."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Protocol

from tarel.sources.contracts import SourceFailure, SourceProfile, validate_source


class SourceStore(Protocol):
    def save(self, source: SourceProfile) -> Path | str | None: ...

    def load(self, name: str) -> SourceProfile: ...

    def list(self) -> tuple[str, ...]: ...

    def exists(self, name: str) -> bool: ...


class FileSourceStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "sources"

    def save(self, source: SourceProfile) -> Path:
        validate_source(source)
        path = self.path(source.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(source.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".source-",
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
            raise SourceFailure(
                "source_save_failed",
                f"Could not save source profile: {source.name}",
            ) from exc
        return path

    def load(self, name: str) -> SourceProfile:
        path = self.path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise SourceFailure("source_not_found", f"Source profile not found: {name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise SourceFailure("invalid_source", f"Could not read source profile: {name}") from exc
        if not isinstance(data, dict):
            raise SourceFailure("invalid_source", f"Source profile root must be an object: {name}")
        return SourceProfile.from_dict(data)

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(path.parent.name for path in self.root.glob("*/source.json") if path.is_file())
        )

    def exists(self, name: str) -> bool:
        return self.path(name).is_file()

    def path(self, name: str) -> Path:
        probe = SourceProfile(name=name, connector="probe")
        validate_source(probe)
        return self.root / name / "source.json"
