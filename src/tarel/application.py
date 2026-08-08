"""Application use cases shared by the CLI and the future SDK."""

from __future__ import annotations

import os
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from tarel.annotations.apply import apply_annotation_proposal
from tarel.annotations.contracts import (
    AnnotationProposalEnvelope,
    AnnotationRunResult,
    AnnotationTask,
)
from tarel.annotations.review import (
    AnnotationReviewRecord,
    annotation_review_record,
    decide_annotation_scope,
    edit_annotation,
    list_annotation_reviews,
)
from tarel.annotations.runner import run_annotation_batch
from tarel.annotations.states import selected_annotation_states
from tarel.annotations.tasks import plan_annotation_tasks
from tarel.connectors.authoring import ScaffoldResult, scaffold_connector
from tarel.connectors.contracts import (
    CatalogRequest,
    CatalogResult,
    ConnectorCheck,
    ConnectorFailure,
    ProbeRequest,
    ProbeResult,
    RelationshipPair,
    RelationshipPairProfile,
    RelationshipProbeConnector,
    RelationshipProbeRequest,
    SampleRequest,
    SampleResult,
)
from tarel.connectors.host import check_connector, load_connector
from tarel.context import (
    DEFAULT_MAX_CONTEXT_CHARACTERS,
    ContextResult,
    compile_context,
    compile_context_from_search,
)
from tarel.context_output import ContextScope
from tarel.context_packets import (
    ContextPacketDiff,
    ContextPacketImpact,
    context_packet_graph_identity,
    context_packet_impact,
    diff_context_packets,
    load_context_packet,
)
from tarel.demo import DemoCreateResult, DemoFailure, create_retail_demo
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.change_store import FileGraphChangeStore
from tarel.graph.contracts import GraphDocument, GraphEdge
from tarel.graph.refresh import GraphRefreshReport, refresh_graph
from tarel.graph.revision import graph_revision
from tarel.graph.store import FileGraphStore
from tarel.providers.config import check_openrouter, configure_openrouter
from tarel.providers.contracts import Message, ProviderCheck, ProviderFailure, StructuredRequest
from tarel.providers.host import load_provider
from tarel.relationships.core import (
    add_manual_relationship,
    add_profile_candidates,
    candidate_pairs,
    decide_relationship,
    relationship_candidates,
    relationship_pair,
)
from tarel.retrieval.contracts import IndexBuildResult
from tarel.retrieval.index import FileRetrievalIndex, search_retrieval
from tarel.retrieval.local import (
    DEFAULT_MODEL_NAME,
    LlamaCppEmbedding,
    ModelDownloadResult,
    default_model_path,
    download_model,
    model_spec,
    resolve_model_path,
    sha256_file,
)
from tarel.search import SearchFailure, SearchResults, search_graph
from tarel.workspaces.contracts import (
    SchemaReference,
    WorkspaceDocument,
    WorkspaceFailure,
    WorkspaceRelationship,
)
from tarel.workspaces.core import (
    ResolvedZone,
    add_workspace_relationship,
    create_workspace,
    decide_workspace_relationship,
    define_area,
    define_system,
    define_zone,
    parse_schema_reference,
    require_system,
    resolve_zone,
)
from tarel.workspaces.impact import WorkspaceChangeImpact, workspace_change_impacts
from tarel.workspaces.projection import project_workspace_scope
from tarel.workspaces.retrieval import combine_workspace_search
from tarel.workspaces.scope import ResolvedScope, ScopeSelection, resolve_scope
from tarel.workspaces.store import FileWorkspaceStore


@dataclass(frozen=True, slots=True)
class GraphBuildResult:
    graph: GraphDocument
    path: Path


@dataclass(frozen=True, slots=True)
class AnnotationApplyResult:
    graph: GraphDocument
    path: Path
    target_id: str


@dataclass(frozen=True, slots=True)
class AnnotationBatchResult:
    graph: GraphDocument
    path: Path
    run: AnnotationRunResult


@dataclass(frozen=True, slots=True)
class AnnotationReviewResult:
    graph: GraphDocument
    path: Path
    records: tuple[AnnotationReviewRecord, ...]

    @property
    def record(self) -> AnnotationReviewRecord:
        return self.records[0]


@dataclass(frozen=True, slots=True)
class RelationshipChangeResult:
    graph: GraphDocument
    path: Path
    edge: GraphEdge


@dataclass(frozen=True, slots=True)
class RelationshipDiscoveryResult:
    graph: GraphDocument
    path: Path | None
    profiles: tuple[RelationshipPairProfile, ...]
    candidates: tuple[GraphEdge, ...]


@dataclass(frozen=True, slots=True)
class GraphRefreshResult:
    graph: GraphDocument
    path: Path
    change_report_path: Path | None
    report: GraphRefreshReport
    workspace_impacts: tuple[WorkspaceChangeImpact, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceChangeResult:
    workspace: WorkspaceDocument
    path: Path


@dataclass(frozen=True, slots=True)
class WorkspaceRelationshipChangeResult:
    workspace: WorkspaceDocument
    path: Path
    relationship: WorkspaceRelationship


def create_demo_use_case(
    name: str,
    *,
    path: Path | None = None,
    version: int = 1,
    force: bool = False,
) -> DemoCreateResult:
    if name != "retail-dwh":
        raise DemoFailure("unknown_demo", f"Unknown demo: {name}")
    return create_retail_demo(path=path, version=version, force=force)


def check_connector_use_case(name: str) -> ConnectorCheck:
    return check_connector(name)


def probe_connector_use_case(
    name: str,
    *,
    config_path: Path | None = None,
    database: str | None = None,
) -> ProbeResult:
    config = _read_config(config_path)
    section = config.get(name, {})
    if not isinstance(section, dict):
        raise ConnectorFailure("invalid_config", f"Configuration section [{name}] must be a table.")

    url = _connection_url(name, section)
    selected_database = database or _optional_string(section.get("default_database"))
    connector = load_connector(name)
    return connector.probe(ProbeRequest(url=url, database=selected_database))


def discover_catalog_use_case(
    name: str,
    *,
    config_path: Path | None = None,
    database: str | None = None,
    namespace: str | None = None,
) -> CatalogResult:
    config = _read_config(config_path)
    section = config.get(name, {})
    if not isinstance(section, dict):
        raise ConnectorFailure("invalid_config", f"Configuration section [{name}] must be a table.")

    url = _connection_url(name, section)
    selected_database = database or _optional_string(section.get("default_database"))
    connector = load_connector(name)
    return connector.discover_catalog(
        CatalogRequest(url=url, database=selected_database, namespace=namespace)
    )


def sample_connector_use_case(
    name: str,
    *,
    config_path: Path | None,
    database: str | None,
    namespace: str,
    object_name: str,
    limit: int,
) -> SampleResult:
    config = _read_config(config_path)
    section = config.get(name, {})
    if not isinstance(section, dict):
        raise ConnectorFailure("invalid_config", f"Configuration section [{name}] must be a table.")
    url = _connection_url(name, section)
    selected_database = database or _optional_string(section.get("default_database"))
    connector = load_connector(name)
    return connector.sample_rows(
        SampleRequest(
            url=url,
            database=selected_database,
            namespace=namespace,
            object_name=object_name,
            limit=limit,
        )
    )


def scaffold_connector_use_case(name: str, *, output: Path | None = None) -> ScaffoldResult:
    return scaffold_connector(name, output=output)


def check_provider_use_case(name: str) -> ProviderCheck:
    if name != "openrouter":
        raise ProviderFailure("unknown_provider", f"Unknown annotation provider: {name}")
    return check_openrouter()


def configure_provider_use_case(
    name: str,
    *,
    api_key: str,
    model: str | None = None,
    base_url: str | None = None,
) -> Path:
    if name != "openrouter":
        raise ProviderFailure("unknown_provider", f"Unknown annotation provider: {name}")
    return configure_openrouter(api_key=api_key, model=model, base_url=base_url)


def test_provider_use_case(name: str, *, timeout: float = 120.0) -> dict[str, object]:
    provider = load_provider(name, timeout=timeout)
    result = provider.generate_structured(
        StructuredRequest(
            messages=(
                Message(
                    role="user",
                    content='Return exactly {"status":"ok"} as structured JSON.',
                ),
            ),
            schema_name="TarelProviderCheck",
            schema={
                "additionalProperties": False,
                "properties": {"status": {"const": "ok", "type": "string"}},
                "required": ["status"],
                "type": "object",
            },
        )
    )
    if result != {"status": "ok"}:
        raise ProviderFailure(
            "invalid_provider_response",
            "Provider check returned an unexpected structured response.",
        )
    return {"name": name, "status": "ok"}


def build_graph_use_case(
    name: str,
    *,
    connector_name: str,
    config_path: Path | None = None,
    database: str | None = None,
    namespace: str | None = None,
) -> GraphBuildResult:
    catalog = discover_catalog_use_case(
        connector_name,
        config_path=config_path,
        database=database,
        namespace=namespace,
    )
    graph = build_graph_from_catalog(name, catalog)
    path = FileGraphStore().save(graph)
    return GraphBuildResult(graph=graph, path=path)


def refresh_graph_use_case(
    name: str,
    *,
    config_path: Path | None = None,
    namespace: str | None = None,
) -> GraphRefreshResult:
    store = FileGraphStore()
    current = store.load(name)
    current_namespaces = {
        str(node.metadata.get("namespace"))
        for node in current.nodes
        if node.type in {"table", "view"} and node.metadata.get("namespace")
    }
    selected_namespace = (
        namespace
        if namespace is not None
        else next(iter(current_namespaces)) if len(current_namespaces) == 1 else None
    )
    catalog = discover_catalog_use_case(
        current.connector,
        config_path=config_path,
        database=current.catalog,
        namespace=selected_namespace,
    )
    discovered = build_graph_from_catalog(name, catalog)
    refreshed, report = refresh_graph(current, discovered)
    workspace_store = FileWorkspaceStore()
    workspace_impacts = tuple(
        impact
        for workspace_name in workspace_store.list()
        for impact in workspace_change_impacts(
            workspace_store.load(workspace_name),
            name,
            report,
        )
    )
    change_report_path = (
        FileGraphChangeStore().save(name, report)
        if report.before_revision != report.after_revision
        else None
    )
    path = store.save(refreshed)
    return GraphRefreshResult(
        graph=refreshed,
        path=path,
        change_report_path=change_report_path,
        report=report,
        workspace_impacts=workspace_impacts,
    )


def list_graphs_use_case() -> tuple[str, ...]:
    return FileGraphStore().list()


def load_graph_use_case(name: str) -> GraphDocument:
    return FileGraphStore().load(name)


def create_workspace_use_case(
    name: str,
    *,
    description: str | None = None,
) -> WorkspaceChangeResult:
    store = FileWorkspaceStore()
    if name in store.list():
        raise WorkspaceFailure("workspace_exists", f"Workspace already exists: {name}")
    workspace = create_workspace(name, description=description)
    return WorkspaceChangeResult(workspace=workspace, path=store.save(workspace))


def list_workspaces_use_case() -> tuple[str, ...]:
    return FileWorkspaceStore().list()


def load_workspace_use_case(name: str) -> WorkspaceDocument:
    return FileWorkspaceStore().load(name)


def resolve_workspace_scope_use_case(
    workspace_name: str,
    *,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
) -> ResolvedScope:
    _workspace, _loaded, scope = _load_workspace_scope(
        workspace_name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
    )
    return scope


def _load_workspace_scope(
    workspace_name: str,
    *,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
) -> tuple[WorkspaceDocument, dict[str, GraphDocument], ResolvedScope]:
    workspace = FileWorkspaceStore().load(workspace_name)
    graph_names = {
        graph_name
        for system in workspace.systems
        if not systems or system.name in systems
        for graph_name in system.graphs
    }
    graph_store = FileGraphStore()
    loaded = {name: graph_store.load(name) for name in sorted(graph_names)}
    scope = resolve_scope(
        workspace,
        loaded,
        ScopeSelection(
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        ),
    )
    return workspace, loaded, scope


def define_workspace_system_use_case(
    workspace_name: str,
    system_name: str,
    *,
    graph_names: tuple[str, ...],
    description: str | None = None,
) -> WorkspaceChangeResult:
    workspace_store = FileWorkspaceStore()
    graph_store = FileGraphStore()
    workspace = workspace_store.load(workspace_name)
    graphs = {name: graph_store.load(name) for name in graph_names}
    updated = define_system(
        workspace,
        system_name,
        graph_names=graph_names,
        graphs=graphs,
        description=description,
    )
    return WorkspaceChangeResult(workspace=updated, path=workspace_store.save(updated))


def define_workspace_area_use_case(
    workspace_name: str,
    system_name: str,
    area_name: str,
    *,
    schema_references: tuple[str, ...],
    description: str | None = None,
) -> WorkspaceChangeResult:
    workspace_store = FileWorkspaceStore()
    graph_store = FileGraphStore()
    workspace = workspace_store.load(workspace_name)
    system = require_system(workspace, system_name)
    schemas: tuple[SchemaReference, ...] = tuple(
        parse_schema_reference(reference) for reference in schema_references
    )
    graphs = {name: graph_store.load(name) for name in system.graphs}
    updated = define_area(
        workspace,
        system_name,
        area_name,
        schemas=schemas,
        graphs=graphs,
        description=description,
    )
    return WorkspaceChangeResult(workspace=updated, path=workspace_store.save(updated))


def define_workspace_zone_use_case(
    workspace_name: str,
    system_name: str,
    zone_name: str,
    *,
    object_references: tuple[str, ...],
    description: str | None = None,
) -> WorkspaceChangeResult:
    workspace_store = FileWorkspaceStore()
    graph_store = FileGraphStore()
    workspace = workspace_store.load(workspace_name)
    system = require_system(workspace, system_name)
    graphs = {name: graph_store.load(name) for name in system.graphs}
    updated = define_zone(
        workspace,
        system_name,
        zone_name,
        object_references=object_references,
        graphs=graphs,
        description=description,
    )
    return WorkspaceChangeResult(workspace=updated, path=workspace_store.save(updated))


def add_workspace_relationship_use_case(
    workspace_name: str,
    *,
    source_reference: str,
    target_reference: str,
    reason: str,
    validated: bool = False,
) -> WorkspaceRelationshipChangeResult:
    workspace_store = FileWorkspaceStore()
    graph_store = FileGraphStore()
    workspace = workspace_store.load(workspace_name)
    graph_names = {name for system in workspace.systems for name in system.graphs}
    graphs = {name: graph_store.load(name) for name in sorted(graph_names)}
    updated, relationship = add_workspace_relationship(
        workspace,
        source_reference=source_reference,
        target_reference=target_reference,
        graphs=graphs,
        reason=reason,
        validated=validated,
    )
    return WorkspaceRelationshipChangeResult(
        workspace=updated,
        path=workspace_store.save(updated),
        relationship=relationship,
    )


def decide_workspace_relationship_use_case(
    workspace_name: str,
    relationship_id: str,
    *,
    state: str,
    reason: str,
) -> WorkspaceRelationshipChangeResult:
    workspace_store = FileWorkspaceStore()
    workspace = workspace_store.load(workspace_name)
    updated, relationship = decide_workspace_relationship(
        workspace,
        relationship_id,
        state=state,
        reason=reason,
    )
    return WorkspaceRelationshipChangeResult(
        workspace=updated,
        path=workspace_store.save(updated),
        relationship=relationship,
    )


def show_workspace_zone_use_case(
    workspace_name: str,
    system_name: str,
    zone_name: str,
) -> ResolvedZone:
    workspace = FileWorkspaceStore().load(workspace_name)
    system = require_system(workspace, system_name)
    graph_store = FileGraphStore()
    graphs = {name: graph_store.load(name) for name in system.graphs}
    return resolve_zone(
        workspace,
        system_name,
        zone_name,
        graphs=graphs,
    )


def search_graph_use_case(
    name: str,
    query: str,
    *,
    limit: int = 20,
    namespace: str | None = None,
    mode: str = "lexical",
    model_path: Path | None = None,
    n_threads: int | None = None,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
) -> SearchResults:
    graph = FileGraphStore().load(name)
    selected_states = selected_annotation_states(
        annotation_states,
        validated_only=validated_only,
    )
    return _search_loaded_graph(
        graph,
        query,
        limit=limit,
        namespace=namespace,
        mode=mode,
        model_path=model_path,
        n_threads=n_threads,
        annotation_states=selected_states,
    )


def search_workspace_use_case(
    workspace_name: str,
    query: str,
    *,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
    limit: int = 20,
    mode: str = "lexical",
    model_path: Path | None = None,
    n_threads: int | None = None,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
) -> SearchResults:
    if not 1 <= limit <= 100:
        raise SearchFailure("invalid_limit", "Search limit must be between 1 and 100.")
    _workspace, loaded, scope = _load_workspace_scope(
        workspace_name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
    )
    selected_states = selected_annotation_states(
        annotation_states,
        validated_only=validated_only,
    )
    resolved_model = resolve_model_path(model_path) if mode in {"vector", "hybrid"} else None
    embedder = (
        LlamaCppEmbedding(resolved_model, n_threads=n_threads)
        if resolved_model is not None
        else None
    )
    results = tuple(
        _search_loaded_graph(
            loaded[name],
            query,
            limit=100,
            object_ids=frozenset(
                item.object_id for item in scope.objects if item.graph == name
            ),
            mode=mode,
            resolved_model=resolved_model,
            embedder=embedder,
            annotation_states=selected_states,
        )
        for name in scope.graph_names
    )
    return combine_workspace_search(scope, results, limit=limit)


def _search_loaded_graph(
    graph: GraphDocument,
    query: str,
    *,
    limit: int,
    namespace: str | None = None,
    object_ids: frozenset[str] | None = None,
    mode: str,
    model_path: Path | None = None,
    resolved_model: Path | None = None,
    embedder: LlamaCppEmbedding | None = None,
    n_threads: int | None = None,
    annotation_states: frozenset[str],
) -> SearchResults:
    if mode == "lexical":
        return search_graph(
            graph,
            query,
            limit=limit,
            namespace=namespace,
            object_ids=object_ids,
            annotation_states=annotation_states,
        )
    if mode == "bm25":
        return search_retrieval(
            graph,
            query,
            mode=mode,
            limit=limit,
            namespace=namespace,
            object_ids=object_ids,
            annotation_states=annotation_states,
        )
    selected_model = resolved_model or resolve_model_path(model_path)
    selected_embedder = embedder or LlamaCppEmbedding(selected_model, n_threads=n_threads)
    return search_retrieval(
        graph,
        query,
        mode=mode,
        limit=limit,
        namespace=namespace,
        object_ids=object_ids,
        embedder=selected_embedder,
        model_path=selected_model,
        annotation_states=annotation_states,
    )


def compile_context_use_case(
    name: str,
    query: str,
    *,
    namespace: str | None = None,
    seed_limit: int = 3,
    max_objects: int = 10,
    max_joins: int = 12,
    max_hops: int = 2,
    max_fields_per_object: int = 12,
    max_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS,
    mode: str = "lexical",
    model_path: Path | None = None,
    n_threads: int | None = None,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
) -> ContextResult:
    graph = FileGraphStore().load(name)
    selected_states = selected_annotation_states(
        annotation_states,
        validated_only=validated_only,
    )
    if mode == "lexical":
        return compile_context(
            graph,
            query,
            namespace=namespace,
            seed_limit=seed_limit,
            max_objects=max_objects,
            max_joins=max_joins,
            max_hops=max_hops,
            max_fields_per_object=max_fields_per_object,
            max_characters=max_characters,
            annotation_states=selected_states,
        )
    search = search_graph_use_case(
        name,
        query,
        limit=100,
        namespace=namespace,
        mode=mode,
        model_path=model_path,
        n_threads=n_threads,
        annotation_states=selected_states,
    )
    return compile_context_from_search(
        graph,
        search,
        namespace=namespace,
        seed_limit=seed_limit,
        max_objects=max_objects,
        max_joins=max_joins,
        max_hops=max_hops,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
        annotation_states=selected_states,
    )


def compile_workspace_context_use_case(
    workspace_name: str,
    query: str,
    *,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
    seed_limit: int = 3,
    max_objects: int = 10,
    max_joins: int = 12,
    max_hops: int = 2,
    max_fields_per_object: int = 12,
    max_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS,
    mode: str = "lexical",
    model_path: Path | None = None,
    n_threads: int | None = None,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
) -> ContextResult:
    workspace, loaded, scope = _load_workspace_scope(
        workspace_name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
    )
    selected_states = selected_annotation_states(
        annotation_states,
        validated_only=validated_only,
    )
    search = search_workspace_use_case(
        workspace_name,
        query,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
        limit=100,
        mode=mode,
        model_path=model_path,
        n_threads=n_threads,
        annotation_states=selected_states,
    )
    projection = project_workspace_scope(workspace, loaded, scope)
    selection = scope.selection
    return compile_context_from_search(
        projection,
        search,
        seed_limit=seed_limit,
        max_objects=max_objects,
        max_joins=max_joins,
        max_hops=max_hops,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
        annotation_states=selected_states,
        scope=ContextScope(
            mode="workspace_retrieval",
            workspace=workspace_name,
            scope_hash=scope.scope_hash,
            systems=tuple(sorted(set(selection.systems))),
            graphs=scope.graph_names,
            areas=tuple(sorted(set(selection.areas))),
            schemas=tuple(sorted(set(selection.schemas))),
            zones=tuple(sorted(set(selection.zones))),
        ),
    )


def diff_context_packets_use_case(left: Path, right: Path) -> ContextPacketDiff:
    return diff_context_packets(load_context_packet(left), load_context_packet(right))


def context_packet_impact_use_case(
    packet_path: Path,
    graph_name: str,
) -> ContextPacketImpact:
    graph = FileGraphStore().load(graph_name)
    current_revision = graph_revision(graph)
    packet = load_context_packet(packet_path)
    _packet_graph, packet_revision = context_packet_graph_identity(packet)
    change_store = FileGraphChangeStore()
    report_path = change_store.path(graph_name, packet_revision, current_revision)
    report = (
        change_store.load(graph_name, packet_revision, current_revision)
        if report_path.exists()
        else None
    )
    return context_packet_impact(packet, graph, report)


def download_embedding_model_use_case(
    *,
    name: str = DEFAULT_MODEL_NAME,
    target: Path | None = None,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> ModelDownloadResult:
    return download_model(name=name, target=target, force=force, progress=progress)


def embedding_model_status_use_case(
    *,
    name: str = DEFAULT_MODEL_NAME,
    model_path: Path | None = None,
) -> dict[str, object]:
    spec = model_spec(name)
    path = (model_path or default_model_path(name)).expanduser().resolve()
    exists = path.is_file()
    return {
        "exists": exists,
        "model": name,
        "path": str(path),
        "sha256_valid": sha256_file(path) == spec.sha256 if exists else False,
        "size": path.stat().st_size if exists else None,
        "source": spec.source,
    }


def build_retrieval_index_use_case(
    name: str,
    *,
    model_path: Path | None = None,
    batch_size: int = 16,
    n_threads: int | None = None,
) -> IndexBuildResult:
    graph = FileGraphStore().load(name)
    resolved_model = resolve_model_path(model_path)
    return FileRetrievalIndex().build(
        graph,
        embedder=LlamaCppEmbedding(resolved_model, n_threads=n_threads),
        model_path=resolved_model,
        batch_size=batch_size,
    )


def retrieval_index_status_use_case(name: str) -> dict[str, object]:
    graph = FileGraphStore().load(name)
    store = FileRetrievalIndex()
    metadata = store.metadata(name)
    return {
        "current": metadata.graph_hash == graph_revision(graph),
        "index": metadata.to_dict(),
        "model_available": Path(metadata.model_path).is_file(),
        "path": str(store.path(name)),
    }


def add_relationship_use_case(
    name: str,
    *,
    from_reference: str,
    to_reference: str,
    reason: str,
    validated: bool,
) -> RelationshipChangeResult:
    store = FileGraphStore()
    graph = store.load(name)
    pair = relationship_pair(graph, from_reference, to_reference)
    updated, edge = add_manual_relationship(
        graph,
        pair=pair,
        reason=reason,
        validated=validated,
    )
    path = store.save(updated)
    return RelationshipChangeResult(graph=updated, path=path, edge=edge)


def check_relationship_use_case(
    name: str,
    *,
    from_reference: str,
    to_reference: str,
    config_path: Path | None,
    row_limit: int,
) -> RelationshipPairProfile:
    graph = FileGraphStore().load(name)
    pair = relationship_pair(graph, from_reference, to_reference)
    return _probe_relationship_pairs(
        graph,
        (pair,),
        config_path=config_path,
        row_limit=row_limit,
    )[0]


def discover_relationships_use_case(
    name: str,
    *,
    object_reference: str,
    field_name: str | None,
    config_path: Path | None,
    max_pairs: int,
    row_limit: int,
    min_source_coverage: float,
    min_overlap_count: int,
    min_target_uniqueness: float,
    persist: bool,
) -> RelationshipDiscoveryResult:
    store = FileGraphStore()
    graph = store.load(name)
    pairs = candidate_pairs(
        graph,
        object_reference=object_reference,
        field_name=field_name,
        max_pairs=max_pairs,
    )
    if not pairs:
        return RelationshipDiscoveryResult(graph=graph, path=None, profiles=(), candidates=())
    profiles = _probe_relationship_pairs(
        graph,
        pairs,
        config_path=config_path,
        row_limit=row_limit,
    )
    updated, candidates = add_profile_candidates(
        graph,
        profiles,
        min_source_coverage=min_source_coverage,
        min_overlap_count=min_overlap_count,
        min_target_uniqueness=min_target_uniqueness,
    )
    path = store.save(updated) if persist and candidates else None
    return RelationshipDiscoveryResult(
        graph=updated,
        path=path,
        profiles=profiles,
        candidates=candidates,
    )


def list_relationships_use_case(name: str) -> tuple[GraphEdge, ...]:
    return relationship_candidates(FileGraphStore().load(name))


def decide_relationship_use_case(
    name: str,
    *,
    edge_id: str,
    state: str,
    reason: str,
) -> RelationshipChangeResult:
    store = FileGraphStore()
    graph = store.load(name)
    updated, edge = decide_relationship(
        graph,
        edge_id=edge_id,
        state=state,
        reason=reason,
    )
    path = store.save(updated)
    return RelationshipChangeResult(graph=updated, path=path, edge=edge)


def plan_annotations_use_case(
    name: str,
    *,
    namespace: str | None = None,
    objects: set[str] | None = None,
    limit: int | None = None,
    missing_only: bool = True,
    sample_limit: int = 0,
    config_path: Path | None = None,
) -> tuple[AnnotationTask, ...]:
    graph = FileGraphStore().load(name)
    return _plan_graph_annotations(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
        sample_limit=sample_limit,
        config_path=config_path,
    )


def apply_annotation_use_case(
    name: str,
    payload: dict[str, Any],
    *,
    source: str = "agent",
) -> AnnotationApplyResult:
    store = FileGraphStore()
    graph = store.load(name)
    envelope = AnnotationProposalEnvelope.from_dict(payload)
    updated = apply_annotation_proposal(graph, envelope, source=source)
    path = store.save(updated)
    return AnnotationApplyResult(graph=updated, path=path, target_id=envelope.target_id)


def show_annotation_use_case(name: str, reference: str) -> AnnotationReviewRecord:
    return annotation_review_record(FileGraphStore().load(name), reference)


def list_annotation_reviews_use_case(
    name: str,
    *,
    states: frozenset[str] | None = None,
) -> tuple[AnnotationReviewRecord, ...]:
    return list_annotation_reviews(FileGraphStore().load(name), states=states)


def edit_annotation_use_case(
    name: str,
    reference: str,
    patch: dict[str, Any],
    *,
    reason: str,
) -> AnnotationReviewResult:
    store = FileGraphStore()
    graph = store.load(name)
    updated, record = edit_annotation(graph, reference, patch, reason=reason)
    path = store.save(updated)
    return AnnotationReviewResult(graph=updated, path=path, records=(record,))


def decide_annotation_use_case(
    name: str,
    reference: str,
    *,
    state: str,
    reason: str,
    include_fields: bool = False,
) -> AnnotationReviewResult:
    store = FileGraphStore()
    graph = store.load(name)
    updated, records = decide_annotation_scope(
        graph,
        reference,
        state=state,
        reason=reason,
        include_fields=include_fields,
    )
    path = store.save(updated)
    return AnnotationReviewResult(graph=updated, path=path, records=records)


def run_annotation_batch_use_case(
    name: str,
    *,
    provider_name: str,
    namespace: str | None = None,
    objects: set[str] | None = None,
    limit: int | None = None,
    missing_only: bool = True,
    workers: int = 1,
    retry: int = 0,
    retry_backoff: float = 2.0,
    skip_errors: bool = False,
    max_errors: int | None = None,
    model: str | None = None,
    timeout: float = 120.0,
    sample_limit: int = 0,
    config_path: Path | None = None,
    progress: Callable[[int, int, str, str], None] | None = None,
) -> AnnotationBatchResult:
    store = FileGraphStore()
    graph = store.load(name)
    tasks = _plan_graph_annotations(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
        sample_limit=sample_limit,
        config_path=config_path,
    )
    provider = load_provider(provider_name, timeout=timeout)
    updated, run = run_annotation_batch(
        graph,
        tasks,
        provider,
        workers=workers,
        retry=retry,
        retry_backoff=retry_backoff,
        skip_errors=skip_errors,
        max_errors=max_errors,
        model=model,
        after_annotation=store.save,
        progress=progress,
    )
    path = store.save(updated)
    return AnnotationBatchResult(graph=updated, path=path, run=run)


def _plan_graph_annotations(
    graph: GraphDocument,
    *,
    namespace: str | None,
    objects: set[str] | None,
    limit: int | None,
    missing_only: bool,
    sample_limit: int,
    config_path: Path | None,
) -> tuple[AnnotationTask, ...]:
    if not 0 <= sample_limit <= 10:
        raise ConnectorFailure("invalid_sample_limit", "Sample limit must be between 0 and 10.")
    tasks = plan_annotation_tasks(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
    )
    if sample_limit == 0:
        return tasks

    node_by_id = graph.node_by_id()
    samples: dict[str, SampleResult] = {}
    for task in tasks:
        node = node_by_id[task.target_id]
        samples[task.target_id] = sample_connector_use_case(
            graph.connector,
            config_path=config_path,
            database=graph.catalog,
            namespace=str(node.metadata["namespace"]),
            object_name=str(node.metadata["name"]),
            limit=sample_limit,
        )
    return plan_annotation_tasks(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
        samples_by_target=samples,
    )


def _read_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        with path.expanduser().open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConnectorFailure("config_not_found", f"Configuration file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConnectorFailure(
            "invalid_config",
            f"Configuration file is not valid TOML: {path}",
        ) from exc
    return data


def _probe_relationship_pairs(
    graph: GraphDocument,
    pairs: tuple[RelationshipPair, ...],
    *,
    config_path: Path | None,
    row_limit: int,
) -> tuple[RelationshipPairProfile, ...]:
    config = _read_config(config_path)
    section = config.get(graph.connector, {})
    if not isinstance(section, dict):
        raise ConnectorFailure(
            "invalid_config",
            f"Configuration section [{graph.connector}] must be a table.",
        )
    connector = load_connector(graph.connector)
    if (
        "probe_relationships" not in connector.manifest.capabilities
        or not hasattr(connector, "probe_relationships")
    ):
        raise ConnectorFailure(
            "unsupported_capability",
            f"Connector {graph.connector} does not support relationship probes.",
        )
    profiler = cast(RelationshipProbeConnector, connector)
    result = profiler.probe_relationships(
        RelationshipProbeRequest(
            url=_connection_url(graph.connector, section),
            database=graph.catalog,
            pairs=pairs,
            row_limit=row_limit,
        )
    )
    return result.profiles


def _connection_url(name: str, section: dict[str, Any]) -> str:
    env_name = f"TAREL_{name.upper()}_URL"
    value = os.getenv(env_name) or section.get("url")
    if not isinstance(value, str) or not value.strip():
        raise ConnectorFailure(
            "missing_config",
            f"No connection URL configured. Set {env_name} or [{name}].url in --config.",
        )
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConnectorFailure("invalid_config", "default_database must be a string.")
    return value.strip() or None
