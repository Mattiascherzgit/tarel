"""Explicit local state boundary shared by the CLI and SDK."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tarel.focus.store import FileFocusStore
from tarel.graph.change_store import FileGraphChangeStore
from tarel.graph.store import FileGraphStore
from tarel.knowledge.store import FileKnowledgeStore
from tarel.lineage.analysis_cache import FileLineageAnalysisCache
from tarel.lineage.change_store import FileLineageChangeStore
from tarel.lineage.store import FileLineageStore
from tarel.retrieval.index import FileRetrievalIndex
from tarel.sources.store import FileSourceStore
from tarel.workspaces.store import FileWorkspaceStore


@dataclass(frozen=True, slots=True)
class TarelRuntime:
    """Filesystem-backed TAREL state rooted at one explicit ``.tarel`` directory."""

    root: Path

    @classmethod
    def local(cls, root: str | Path) -> TarelRuntime:
        return cls(root=Path(root).expanduser().resolve())

    def graph_store(self) -> FileGraphStore:
        return FileGraphStore(self.root / "graphs")

    def graph_change_store(self) -> FileGraphChangeStore:
        return FileGraphChangeStore(self.root / "graphs")

    def lineage_store(self) -> FileLineageStore:
        return FileLineageStore(self.root / "lineage")

    def lineage_change_store(self) -> FileLineageChangeStore:
        return FileLineageChangeStore(self.root / "lineage")

    def lineage_analysis_cache(self) -> FileLineageAnalysisCache:
        return FileLineageAnalysisCache(self.root / "lineage-analysis-cache")

    def knowledge_store(self) -> FileKnowledgeStore:
        return FileKnowledgeStore(self.root / "knowledge")

    def focus_store(self) -> FileFocusStore:
        return FileFocusStore(self.root / "focus")

    def workspace_store(self) -> FileWorkspaceStore:
        return FileWorkspaceStore(self.root / "workspaces")

    def retrieval_index(self) -> FileRetrievalIndex:
        return FileRetrievalIndex(self.root / "indexes")

    def source_store(self) -> FileSourceStore:
        return FileSourceStore(self.root / "sources")
