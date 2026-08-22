"""Atomic local persistence for entity-resolution candidates."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from tarel.entity_resolution.contracts import (
    EntityResolutionCandidate,
    EntityResolutionFailure,
    validate_entity_resolution_candidate,
)

_CANDIDATE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class EntityResolutionStore(Protocol):
    def save(self, candidate: EntityResolutionCandidate) -> Path | str | None: ...

    def load(self, candidate_id: str) -> EntityResolutionCandidate: ...

    def list(self) -> tuple[str, ...]: ...

    def exists(self, candidate_id: str) -> bool: ...


class FileEntityResolutionStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "entity-resolution"

    def save(self, candidate: EntityResolutionCandidate) -> Path:
        validate_entity_resolution_candidate(candidate)
        path = self.path(candidate.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            candidate.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".entity-resolution-",
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
            raise EntityResolutionFailure(
                "entity_resolution_save_failed",
                f"Could not save entity-resolution candidate: {candidate.id}",
            ) from exc
        return path

    def load(self, candidate_id: str) -> EntityResolutionCandidate:
        try:
            payload = json.loads(self.path(candidate_id).read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise EntityResolutionFailure(
                "entity_resolution_not_found",
                f"Entity-resolution candidate not found: {candidate_id}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                f"Could not read entity-resolution candidate: {candidate_id}",
            ) from exc
        if not isinstance(payload, dict):
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Entity-resolution candidate root must be an object.",
            )
        candidate = EntityResolutionCandidate.from_dict(payload)
        if candidate.id != candidate_id:
            raise EntityResolutionFailure(
                "invalid_entity_resolution",
                "Stored entity-resolution ID does not match its directory.",
            )
        return candidate

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                path.parent.name
                for path in self.root.glob("*/candidate.json")
                if path.is_file()
            )
        )

    def exists(self, candidate_id: str) -> bool:
        return self.path(candidate_id).is_file()

    def path(self, candidate_id: str) -> Path:
        if not _CANDIDATE_ID.fullmatch(candidate_id):
            raise EntityResolutionFailure(
                "invalid_entity_resolution_id",
                "Entity-resolution IDs may contain letters, numbers, dots, underscores, "
                "and hyphens.",
            )
        return self.root / candidate_id / "candidate.json"
