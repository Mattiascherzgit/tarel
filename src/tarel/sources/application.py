"""Application operations over logical sources and connector capabilities."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tarel.application import (
    GraphBuildResult,
    GraphRefreshResult,
    build_graph_use_case,
    discover_catalog_use_case,
    load_graph_use_case,
    probe_connector_use_case,
    refresh_graph_use_case,
)
from tarel.connectors.contracts import CatalogResult, ConnectorCheck, ProbeResult
from tarel.connectors.host import check_connector
from tarel.graph.store import FileGraphStore
from tarel.runtime import TarelRuntime
from tarel.sources.contracts import (
    SourceFailure,
    SourceProfile,
    config_reference_parts,
    create_source,
)
from tarel.sources.store import FileSourceStore


@dataclass(frozen=True, slots=True)
class SourceChangeResult:
    source: SourceProfile
    path: Path
    created: bool


@dataclass(frozen=True, slots=True)
class SourceCheck:
    source: SourceProfile
    connector: ConnectorCheck
    config_status: str

    @property
    def available(self) -> bool:
        return self.connector.available and self.config_status != "missing"

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "config_status": self.config_status,
            "connector": self.connector.to_dict(),
            "source": self.source.to_dict(),
            "source_revision": self.source.revision,
        }


def configure_source_use_case(
    name: str,
    *,
    connector: str,
    config_reference: str | None = None,
    database: str | None = None,
    namespace: str | None = None,
    graphs: tuple[str, ...] = (),
    replace: bool = False,
    runtime: TarelRuntime | None = None,
) -> SourceChangeResult:
    check_connector(connector)
    source = create_source(
        name,
        connector=connector,
        config_reference=config_reference,
        database=database,
        namespace=namespace,
        graphs=graphs,
    )
    store = _source_store(runtime)
    created = not store.exists(name)
    if not created:
        current = store.load(name)
        if current == source:
            return SourceChangeResult(source=current, path=store.path(name), created=False)
        if not replace:
            raise SourceFailure(
                "source_exists",
                f"Source profile already exists with different settings: {name}",
            )
    _validate_graph_bindings(source, runtime=runtime)
    return SourceChangeResult(source=source, path=store.save(source), created=created)


def list_sources_use_case(*, runtime: TarelRuntime | None = None) -> tuple[str, ...]:
    return _source_store(runtime).list()


def load_source_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> SourceProfile:
    return _source_store(runtime).load(name)


def check_source_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> SourceCheck:
    source = load_source_use_case(name, runtime=runtime)
    return SourceCheck(
        source=source,
        connector=check_connector(source.connector),
        config_status=_config_status(source, runtime=runtime),
    )


def probe_source_use_case(
    name: str,
    *,
    database: str | None = None,
    runtime: TarelRuntime | None = None,
) -> ProbeResult:
    source = load_source_use_case(name, runtime=runtime)
    return probe_connector_use_case(
        source.connector,
        config_path=_config_path(source, runtime=runtime),
        database=database or source.database,
    )


def discover_source_use_case(
    name: str,
    *,
    database: str | None = None,
    namespace: str | None = None,
    runtime: TarelRuntime | None = None,
) -> CatalogResult:
    source = load_source_use_case(name, runtime=runtime)
    return discover_catalog_use_case(
        source.connector,
        config_path=_config_path(source, runtime=runtime),
        database=database or source.database,
        namespace=namespace or source.namespace,
    )


def build_source_graph_use_case(
    name: str,
    graph_name: str,
    *,
    database: str | None = None,
    namespace: str | None = None,
    runtime: TarelRuntime | None = None,
) -> GraphBuildResult:
    store = _source_store(runtime)
    source = store.load(name)
    result = build_graph_use_case(
        graph_name,
        connector_name=source.connector,
        config_path=_config_path(source, runtime=runtime),
        database=database or source.database,
        namespace=namespace or source.namespace,
        runtime=runtime,
    )
    store.save(source.with_graph(graph_name))
    return result


def refresh_source_graph_use_case(
    name: str,
    graph_name: str,
    *,
    namespace: str | None = None,
    runtime: TarelRuntime | None = None,
) -> GraphRefreshResult:
    source = load_source_use_case(name, runtime=runtime)
    graph = load_graph_use_case(graph_name, runtime=runtime)
    if graph.connector != source.connector:
        raise SourceFailure(
            "source_graph_mismatch",
            f"Graph {graph_name} uses connector {graph.connector}, not {source.connector}.",
        )
    return refresh_graph_use_case(
        graph_name,
        config_path=_config_path(source, runtime=runtime),
        namespace=namespace or source.namespace,
        runtime=runtime,
    )


def _validate_graph_bindings(
    source: SourceProfile,
    *,
    runtime: TarelRuntime | None,
) -> None:
    graph_store = runtime.graph_store() if runtime is not None else FileGraphStore()
    available = set(graph_store.list())
    for name in source.graphs:
        if name not in available:
            continue
        graph = graph_store.load(name)
        if graph.connector != source.connector:
            raise SourceFailure(
                "source_graph_mismatch",
                f"Graph {name} uses connector {graph.connector}, not {source.connector}.",
            )


def _config_path(
    source: SourceProfile,
    *,
    runtime: TarelRuntime | None,
) -> Path | None:
    reference = source.config_reference
    if reference is None:
        return None
    kind, value = config_reference_parts(reference)
    if kind == "env":
        resolved = os.getenv(value)
        if not resolved:
            raise SourceFailure(
                "source_config_not_resolved",
                f"Source config environment reference is not set: {value}",
            )
        return Path(resolved).expanduser()
    root = (runtime.root if runtime is not None else Path.cwd() / ".tarel").resolve()
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise SourceFailure(
            "invalid_config_reference",
            "State config reference resolves outside the TAREL state directory.",
        )
    return path


def _config_status(
    source: SourceProfile,
    *,
    runtime: TarelRuntime | None,
) -> str:
    if source.config_reference is None:
        return "connector_environment"
    try:
        path = _config_path(source, runtime=runtime)
    except SourceFailure:
        return "missing"
    return "resolved" if path is not None and path.is_file() else "missing"


def _source_store(runtime: TarelRuntime | None) -> FileSourceStore:
    return runtime.source_store() if runtime is not None else FileSourceStore()
