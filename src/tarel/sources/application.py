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
    profile_connector_use_case,
    refresh_graph_use_case,
    sample_connector_use_case,
)
from tarel.connectors.contracts import (
    CatalogResult,
    ConnectorCheck,
    ConnectorFailure,
    ObjectProfileResult,
    ProbeResult,
    SampleResult,
)
from tarel.connectors.host import check_connector
from tarel.enrichment import (
    EnrichmentFailure,
    EnrichmentWorkfile,
    compile_enrichment_workfile,
)
from tarel.graph.contracts import GraphDocument, GraphEdge, GraphNode
from tarel.graph.revision import graph_revision
from tarel.graph.store import FileGraphStore
from tarel.relationships.core import add_transformed_profile_candidates
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


@dataclass(frozen=True, slots=True)
class SourceEnrichmentResult:
    workfile: EnrichmentWorkfile
    persisted_candidates: tuple[GraphEdge, ...] = ()
    graph_path: Path | str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            **self.workfile.to_dict(),
            "persistence": {
                "candidate_ids": [edge.id for edge in self.persisted_candidates],
                "graph_path": str(self.graph_path) if self.graph_path is not None else None,
                "raw_samples_persisted": False,
            },
        }


def configure_source_use_case(
    name: str,
    *,
    connector: str,
    config_reference: str | None = None,
    database: str | None = None,
    namespace: str | None = None,
    graphs: tuple[str, ...] = (),
    enrichment_permissions: tuple[str, ...] = (),
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
        enrichment_permissions=enrichment_permissions,
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


def enrich_source_use_case(
    name: str,
    graph_name: str,
    *,
    profile_row_limit: int = 10_000,
    sample_limit: int = 10,
    persist_join_candidates: bool = False,
    runtime: TarelRuntime | None = None,
) -> SourceEnrichmentResult:
    source = load_source_use_case(name, runtime=runtime)
    graph_store = runtime.graph_store() if runtime is not None else FileGraphStore()
    graph = graph_store.load(graph_name)
    _validate_enrichment_scope(source, graph_name=graph_name, graph_connector=graph.connector)
    if not 1 <= profile_row_limit <= 100_000:
        raise SourceFailure(
            "invalid_profile_row_limit",
            "Profile row limit must be between 1 and 100000.",
        )
    if not 1 <= sample_limit <= 10:
        raise SourceFailure(
            "invalid_sample_limit",
            "Sample limit must be between 1 and 10.",
        )
    if persist_join_candidates and not source.allows_enrichment("raw_samples"):
        raise SourceFailure(
            "raw_samples_not_allowed",
            "Persisting transformed join candidates requires raw-sample permission.",
        )

    config_path = _config_path(source, runtime=runtime)
    profiles: dict[str, ObjectProfileResult] = {}
    samples: dict[str, SampleResult] = {}
    failures: dict[str, tuple[EnrichmentFailure, ...]] = {}
    for object_node in _graph_objects(graph):
        namespace = str(object_node.metadata["namespace"])
        object_name = str(object_node.metadata["name"])
        object_failures: list[EnrichmentFailure] = []
        if source.allows_enrichment("aggregates"):
            try:
                profiles[object_node.id] = profile_connector_use_case(
                    source.connector,
                    config_path=config_path,
                    database=graph.catalog,
                    namespace=namespace,
                    object_name=object_name,
                    row_limit=profile_row_limit,
                    include_values=source.allows_enrichment("small_domains"),
                )
            except ConnectorFailure as exc:
                object_failures.append(
                    EnrichmentFailure(
                        operation="profile",
                        code=exc.code,
                        message=str(exc),
                    )
                )
        if source.allows_enrichment("raw_samples"):
            try:
                samples[object_node.id] = sample_connector_use_case(
                    source.connector,
                    config_path=config_path,
                    database=graph.catalog,
                    namespace=namespace,
                    object_name=object_name,
                    limit=sample_limit,
                )
            except ConnectorFailure as exc:
                object_failures.append(
                    EnrichmentFailure(
                        operation="sample",
                        code=exc.code,
                        message=str(exc),
                    )
                )
        if object_failures:
            failures[object_node.id] = tuple(object_failures)

    workfile = compile_enrichment_workfile(
        graph,
        source,
        graph_revision=graph_revision(graph),
        profiles=profiles,
        samples=samples,
        failures=failures,
    )
    if not persist_join_candidates:
        return SourceEnrichmentResult(workfile=workfile)

    enriched_graph, candidates = add_transformed_profile_candidates(
        graph,
        workfile.transformed_join_candidates,
    )
    if not candidates:
        return SourceEnrichmentResult(workfile=workfile)
    return SourceEnrichmentResult(
        workfile=workfile,
        persisted_candidates=candidates,
        graph_path=graph_store.save(enriched_graph),
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


def _validate_enrichment_scope(
    source: SourceProfile,
    *,
    graph_name: str,
    graph_connector: str,
) -> None:
    if graph_name not in source.graphs:
        raise SourceFailure(
            "source_graph_not_mapped",
            f"Graph {graph_name} is not bound to source {source.name}.",
        )
    if graph_connector != source.connector:
        raise SourceFailure(
            "source_graph_mismatch",
            f"Graph {graph_name} uses connector {graph_connector}, not {source.connector}.",
        )
    if not (
        source.allows_enrichment("aggregates")
        or source.allows_enrichment("raw_samples")
    ):
        raise SourceFailure(
            "enrichment_not_allowed",
            f"Source {source.name} grants no enrichment permissions.",
        )


def _graph_objects(graph: GraphDocument) -> tuple[GraphNode, ...]:
    return tuple(
        sorted(
            (node for node in graph.nodes if node.type in {"table", "view"}),
            key=lambda node: (node.label.casefold(), node.id),
        )
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
