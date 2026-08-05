"""Atomic local persistence for revision-bound graph change reports."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from tarel.graph.contracts import GraphFailure
from tarel.graph.refresh import GraphRefreshReport

_GRAPH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")


class FileGraphChangeStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "graphs"

    def save(self, graph_name: str, report: GraphRefreshReport) -> Path:
        path = self.path(graph_name, report.before_revision, report.after_revision)
        if path.exists():
            existing = self.load(graph_name, report.before_revision, report.after_revision)
            if existing.to_dict() != report.to_dict():
                raise GraphFailure(
                    "change_report_conflict",
                    "A different report already exists for the same graph transition.",
                )
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".change-",
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
            raise GraphFailure(
                "change_report_save_failed",
                f"Could not save change report for graph: {graph_name}",
            ) from exc
        return path

    def load(
        self,
        graph_name: str,
        before_revision: str,
        after_revision: str,
    ) -> GraphRefreshReport:
        path = self.path(graph_name, before_revision, after_revision)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise GraphFailure(
                "change_report_not_found",
                f"Change report not found for graph {graph_name}: "
                f"{before_revision} -> {after_revision}",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise GraphFailure(
                "invalid_change_report",
                f"Could not read change report for graph: {graph_name}",
            ) from exc
        if not isinstance(data, dict):
            raise GraphFailure("invalid_change_report", "Change report root must be an object.")
        return GraphRefreshReport.from_dict(data)

    def path(
        self,
        graph_name: str,
        before_revision: str,
        after_revision: str,
    ) -> Path:
        if not _GRAPH_NAME.fullmatch(graph_name):
            raise GraphFailure("invalid_graph_name", f"Invalid graph name: {graph_name}")
        if not _REVISION.fullmatch(before_revision) or not _REVISION.fullmatch(after_revision):
            raise GraphFailure("invalid_graph_revision", "Graph revision must be a SHA-256 value.")
        return self.root / graph_name / "changes" / f"{before_revision}--{after_revision}.json"
