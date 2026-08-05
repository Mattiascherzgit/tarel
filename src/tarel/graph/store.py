"""Atomic local JSON graph store."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

from tarel.graph.contracts import GraphDocument, GraphFailure

_GRAPH_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class GraphStore(Protocol):
    """Whole-document persistence boundary for local or shared graph stores."""

    def save(self, graph: GraphDocument) -> Path | str | None: ...

    def load(self, name: str) -> GraphDocument: ...

    def list(self) -> tuple[str, ...]: ...


class FileGraphStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "graphs"

    def save(self, graph: GraphDocument) -> Path:
        path = self.path(graph.name)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(graph.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".graph-",
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
            raise GraphFailure("graph_save_failed", f"Could not save graph: {graph.name}") from exc
        return path

    def load(self, name: str) -> GraphDocument:
        path = self.path(name)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise GraphFailure("graph_not_found", f"Graph not found: {name}") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise GraphFailure("invalid_graph", f"Could not read graph: {name}") from exc
        if not isinstance(data, dict):
            raise GraphFailure("invalid_graph", f"Graph root must be an object: {name}")
        return GraphDocument.from_dict(data)

    def list(self) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        return tuple(
            sorted(
                path.parent.name
                for path in self.root.glob("*/graph.json")
                if path.is_file()
            )
        )

    def path(self, name: str) -> Path:
        if not _GRAPH_NAME.fullmatch(name):
            raise GraphFailure(
                "invalid_graph_name",
                "Graph names may contain letters, numbers, dots, underscores, and hyphens.",
            )
        return self.root / name / "graph.json"
