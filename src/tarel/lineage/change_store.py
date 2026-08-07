"""Atomic local persistence for revision-bound lineage change reports."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from tarel.lineage.contracts import LineageFailure
from tarel.lineage.refresh import LineageRefreshReport

_LINEAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")


class FileLineageChangeStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "lineage"

    def save(self, lineage_name: str, report: LineageRefreshReport) -> Path:
        path = self.path(lineage_name, report.before_revision, report.after_revision)
        if path.exists():
            existing = self.load(lineage_name, report.before_revision, report.after_revision)
            if existing.to_dict() != report.to_dict():
                raise LineageFailure(
                    "lineage_change_report_conflict",
                    "A different report already exists for the same lineage transition.",
                )
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".change-",
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
                "lineage_change_report_save_failed",
                f"Could not save change report for lineage: {lineage_name}",
            ) from exc
        return path

    def load(
        self,
        lineage_name: str,
        before_revision: str,
        after_revision: str,
    ) -> LineageRefreshReport:
        path = self.path(lineage_name, before_revision, after_revision)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise LineageFailure(
                "lineage_change_report_not_found",
                f"Change report not found for lineage: {lineage_name}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise LineageFailure(
                "invalid_lineage_change_report",
                f"Could not read change report for lineage: {lineage_name}",
            ) from exc
        if not isinstance(payload, dict):
            raise LineageFailure(
                "invalid_lineage_change_report",
                "Lineage change-report root must be an object.",
            )
        return LineageRefreshReport.from_dict(payload)

    def path(
        self,
        lineage_name: str,
        before_revision: str,
        after_revision: str,
    ) -> Path:
        if not _LINEAGE_NAME.fullmatch(lineage_name):
            raise LineageFailure(
                "invalid_lineage_name",
                "Lineage names may contain letters, numbers, dots, underscores, and hyphens.",
            )
        if not _REVISION.fullmatch(before_revision) or not _REVISION.fullmatch(after_revision):
            raise LineageFailure(
                "invalid_lineage_revision",
                "Lineage revision must be a SHA-256 value.",
            )
        filename = f"{before_revision}--{after_revision}.json"
        return self.root / lineage_name / "changes" / filename
