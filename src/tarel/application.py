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
    AnnotationFailure,
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
    resolve_annotation_target,
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
    ObjectProfileConnector,
    ObjectProfileRequest,
    ObjectProfileResult,
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
    compile_context_prefix,
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
from tarel.focus.contracts import FocusDocument, FocusFailure
from tarel.focus.core import (
    build_focus,
    expand_graph_objects_one_hop,
    graph_object_ids,
    require_current_focus,
)
from tarel.focus.store import FileFocusStore
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.change_store import FileGraphChangeStore
from tarel.graph.contracts import GraphDocument, GraphEdge
from tarel.graph.refresh import GraphRefreshReport, refresh_graph
from tarel.graph.revision import graph_revision
from tarel.graph.store import FileGraphStore
from tarel.knowledge.contracts import (
    DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    KnowledgeContext,
    KnowledgeDocument,
    KnowledgeFailure,
    KnowledgeReference,
    KnowledgeScope,
)
from tarel.knowledge.core import resolve_knowledge
from tarel.knowledge.store import FileKnowledgeStore
from tarel.lineage.contracts import LineageDocument
from tarel.lineage.store import FileLineageStore
from tarel.lineage.traversal import DEFAULT_LINEAGE_STATES, trace_upstream
from tarel.providers.authoring import ProviderScaffoldResult, scaffold_provider
from tarel.providers.config import (
    BUILTIN_PROVIDER_ADAPTERS,
    HTTP_PROVIDER_ADAPTERS,
    check_provider,
    configure_http_provider,
    configure_installed_provider,
    configure_openrouter,
    list_provider_names,
)
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
from tarel.runtime import TarelRuntime
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
class KnowledgeChangeResult:
    document: KnowledgeDocument
    path: Path


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


@dataclass(frozen=True, slots=True)
class FocusBuildResult:
    focus: FocusDocument
    path: Path


def _graph_store(runtime: TarelRuntime | None) -> FileGraphStore:
    return FileGraphStore() if runtime is None else runtime.graph_store()


def _graph_change_store(runtime: TarelRuntime | None) -> FileGraphChangeStore:
    return FileGraphChangeStore() if runtime is None else runtime.graph_change_store()


def _lineage_store(runtime: TarelRuntime | None) -> FileLineageStore:
    return FileLineageStore() if runtime is None else runtime.lineage_store()


def _focus_store(runtime: TarelRuntime | None) -> FileFocusStore:
    return FileFocusStore() if runtime is None else runtime.focus_store()


def _workspace_store(runtime: TarelRuntime | None) -> FileWorkspaceStore:
    return FileWorkspaceStore() if runtime is None else runtime.workspace_store()


def _knowledge_store(runtime: TarelRuntime | None) -> FileKnowledgeStore:
    return FileKnowledgeStore() if runtime is None else runtime.knowledge_store()


def _retrieval_index(runtime: TarelRuntime | None) -> FileRetrievalIndex:
    return FileRetrievalIndex() if runtime is None else runtime.retrieval_index()


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


def profile_connector_use_case(
    name: str,
    *,
    config_path: Path | None,
    database: str | None,
    namespace: str,
    object_name: str,
    row_limit: int,
    small_domain_limit: int = 20,
    include_values: bool = False,
) -> ObjectProfileResult:
    config = _read_config(config_path)
    section = config.get(name, {})
    if not isinstance(section, dict):
        raise ConnectorFailure("invalid_config", f"Configuration section [{name}] must be a table.")
    url = _connection_url(name, section)
    selected_database = database or _optional_string(section.get("default_database"))
    connector = load_connector(name)
    if "profile_object" not in connector.manifest.capabilities or not hasattr(
        connector, "profile_object"
    ):
        raise ConnectorFailure(
            "unsupported_capability",
            f"Connector {name} does not support object profiles.",
        )
    profiler = cast(ObjectProfileConnector, connector)
    return profiler.profile_object(
        ObjectProfileRequest(
            url=url,
            database=selected_database,
            namespace=namespace,
            object_name=object_name,
            row_limit=row_limit,
            small_domain_limit=small_domain_limit,
            include_values=include_values,
        )
    )


def scaffold_connector_use_case(name: str, *, output: Path | None = None) -> ScaffoldResult:
    return scaffold_connector(name, output=output)


def check_provider_use_case(name: str) -> ProviderCheck:
    return check_provider(name)


def list_provider_names_use_case() -> tuple[str, ...]:
    return list_provider_names()


def scaffold_provider_use_case(
    name: str,
    *,
    output: Path | None = None,
) -> ProviderScaffoldResult:
    return scaffold_provider(name, output=output)


def configure_provider_use_case(
    name: str,
    *,
    adapter: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    reasoning_effort: str | None = None,
    structured_mode: str | None = None,
    allow_no_api_key: bool = False,
) -> Path:
    selected_adapter = adapter or BUILTIN_PROVIDER_ADAPTERS.get(name)
    if selected_adapter is None:
        raise ProviderFailure(
            "missing_provider_adapter",
            "Custom provider profiles require --adapter.",
        )
    expected_adapter = BUILTIN_PROVIDER_ADAPTERS.get(name)
    if expected_adapter is not None and selected_adapter != expected_adapter:
        raise ProviderFailure(
            "invalid_provider_adapter",
            f"Provider profile {name} uses the reserved {expected_adapter} adapter.",
        )
    if allow_no_api_key and selected_adapter == "openrouter":
        raise ProviderFailure(
            "missing_api_key",
            f"Provider profile {name} requires an API key.",
        )
    if name == "openrouter" and selected_adapter == "openrouter":
        return configure_openrouter(
            api_key=api_key,
            model=model,
            base_url=base_url,
            reasoning_effort=reasoning_effort,
        )
    if selected_adapter in HTTP_PROVIDER_ADAPTERS:
        return configure_http_provider(
            name,
            adapter=selected_adapter,
            api_key=api_key,
            model=model,
            base_url=base_url,
            reasoning_effort=reasoning_effort,
            structured_mode=structured_mode,
            allow_no_api_key=allow_no_api_key,
        )
    if reasoning_effort is not None or structured_mode is not None:
        raise ProviderFailure(
            "invalid_provider_config",
            "Installed adapters own provider-specific reasoning and structured-output settings.",
        )
    return configure_installed_provider(
        name,
        adapter=selected_adapter,
        api_key=api_key,
        model=model,
        base_url=base_url,
    )


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
    runtime: TarelRuntime | None = None,
) -> GraphBuildResult:
    catalog = discover_catalog_use_case(
        connector_name,
        config_path=config_path,
        database=database,
        namespace=namespace,
    )
    graph = build_graph_from_catalog(name, catalog)
    path = _graph_store(runtime).save(graph)
    return GraphBuildResult(graph=graph, path=path)


def refresh_graph_use_case(
    name: str,
    *,
    config_path: Path | None = None,
    namespace: str | None = None,
    runtime: TarelRuntime | None = None,
) -> GraphRefreshResult:
    store = _graph_store(runtime)
    current = store.load(name)
    current_namespaces = {
        str(node.metadata.get("namespace"))
        for node in current.nodes
        if node.type in {"table", "view"} and node.metadata.get("namespace")
    }
    selected_namespace = (
        namespace
        if namespace is not None
        else next(iter(current_namespaces))
        if len(current_namespaces) == 1
        else None
    )
    catalog = discover_catalog_use_case(
        current.connector,
        config_path=config_path,
        database=current.catalog,
        namespace=selected_namespace,
    )
    discovered = build_graph_from_catalog(name, catalog)
    refreshed, report = refresh_graph(current, discovered)
    workspace_store = _workspace_store(runtime)
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
        _graph_change_store(runtime).save(name, report)
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


def list_graphs_use_case(*, runtime: TarelRuntime | None = None) -> tuple[str, ...]:
    return _graph_store(runtime).list()


def load_graph_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> GraphDocument:
    return _graph_store(runtime).load(name)


def build_focus_use_case(
    name: str,
    *,
    seed: str,
    lineage_names: tuple[str, ...],
    graph_names: tuple[str, ...],
    max_hops: int = 12,
    states: frozenset[str] | None = None,
    runtime: TarelRuntime | None = None,
) -> FocusBuildResult:
    if not lineage_names and not graph_names:
        raise FocusFailure(
            "missing_focus_sources",
            "Focus build requires at least one explicit --lineage or --graph source.",
        )
    _require_unique_names(lineage_names, "lineage")
    _require_unique_names(graph_names, "graph")
    lineage_store = _lineage_store(runtime)
    graph_store = _graph_store(runtime)
    lineages = tuple(lineage_store.load(item) for item in lineage_names)
    graphs = tuple(graph_store.load(item) for item in graph_names)
    selected_states = DEFAULT_LINEAGE_STATES if states is None else states
    trace = trace_upstream(
        lineages,
        graphs,
        seed,
        max_hops=max_hops,
        states=selected_states,
    )
    focus = build_focus(
        name,
        trace,
        lineages=lineages,
        graphs=graphs,
        states=selected_states,
        max_hops=max_hops,
    )
    return FocusBuildResult(focus, _focus_store(runtime).save(focus))


def load_focus_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> FocusDocument:
    return _focus_store(runtime).load(name)


def list_focuses_use_case(*, runtime: TarelRuntime | None = None) -> tuple[str, ...]:
    return _focus_store(runtime).list()


def create_workspace_use_case(
    name: str,
    *,
    description: str | None = None,
    runtime: TarelRuntime | None = None,
) -> WorkspaceChangeResult:
    store = _workspace_store(runtime)
    if name in store.list():
        raise WorkspaceFailure("workspace_exists", f"Workspace already exists: {name}")
    workspace = create_workspace(name, description=description)
    return WorkspaceChangeResult(workspace=workspace, path=store.save(workspace))


def list_workspaces_use_case(*, runtime: TarelRuntime | None = None) -> tuple[str, ...]:
    return _workspace_store(runtime).list()


def load_workspace_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> WorkspaceDocument:
    return _workspace_store(runtime).load(name)


def resolve_workspace_scope_use_case(
    workspace_name: str,
    *,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
    runtime: TarelRuntime | None = None,
) -> ResolvedScope:
    _workspace, _loaded, scope = _load_workspace_scope(
        workspace_name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
        runtime=runtime,
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
    runtime: TarelRuntime | None = None,
) -> tuple[WorkspaceDocument, dict[str, GraphDocument], ResolvedScope]:
    workspace = _workspace_store(runtime).load(workspace_name)
    graph_names = {
        graph_name
        for system in workspace.systems
        if not systems or system.name in systems
        for graph_name in system.graphs
    }
    graph_store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> WorkspaceChangeResult:
    workspace_store = _workspace_store(runtime)
    graph_store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> WorkspaceChangeResult:
    workspace_store = _workspace_store(runtime)
    graph_store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> WorkspaceChangeResult:
    workspace_store = _workspace_store(runtime)
    graph_store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> WorkspaceRelationshipChangeResult:
    workspace_store = _workspace_store(runtime)
    graph_store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> WorkspaceRelationshipChangeResult:
    workspace_store = _workspace_store(runtime)
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
    *,
    runtime: TarelRuntime | None = None,
) -> ResolvedZone:
    workspace = _workspace_store(runtime).load(workspace_name)
    system = require_system(workspace, system_name)
    graph_store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> SearchResults:
    graph = _graph_store(runtime).load(name)
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
        runtime=runtime,
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
    runtime: TarelRuntime | None = None,
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
        runtime=runtime,
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
            object_ids=frozenset(item.object_id for item in scope.objects if item.graph == name),
            mode=mode,
            resolved_model=resolved_model,
            embedder=embedder,
            annotation_states=selected_states,
            runtime=runtime,
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
    runtime: TarelRuntime | None = None,
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
        store=_retrieval_index(runtime),
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
    runtime: TarelRuntime | None = None,
) -> ContextResult:
    graph = _graph_store(runtime).load(name)
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
        runtime=runtime,
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
    runtime: TarelRuntime | None = None,
) -> ContextResult:
    workspace, loaded, scope = _load_workspace_scope(
        workspace_name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
        runtime=runtime,
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
        runtime=runtime,
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


def compile_context_prefix_use_case(
    name: str,
    *,
    namespace: str | None = None,
    max_objects: int = 250,
    max_joins: int = 500,
    max_fields_per_object: int = 50,
    max_characters: int = 500_000,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
    runtime: TarelRuntime | None = None,
) -> ContextResult:
    graph = _graph_store(runtime).load(name)
    selected_states = selected_annotation_states(
        annotation_states,
        validated_only=validated_only,
    )
    return compile_context_prefix(
        graph,
        namespace=namespace,
        max_objects=max_objects,
        max_joins=max_joins,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
        annotation_states=selected_states,
    )


def compile_workspace_context_prefix_use_case(
    workspace_name: str,
    *,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
    max_objects: int = 250,
    max_joins: int = 500,
    max_fields_per_object: int = 50,
    max_characters: int = 500_000,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
    runtime: TarelRuntime | None = None,
) -> ContextResult:
    workspace, loaded, scope = _load_workspace_scope(
        workspace_name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
        runtime=runtime,
    )
    selected_states = selected_annotation_states(
        annotation_states,
        validated_only=validated_only,
    )
    projection = project_workspace_scope(workspace, loaded, scope)
    selection = scope.selection
    return compile_context_prefix(
        projection,
        max_objects=max_objects,
        max_joins=max_joins,
        max_fields_per_object=max_fields_per_object,
        max_characters=max_characters,
        annotation_states=selected_states,
        scope=ContextScope(
            mode="workspace_prefix",
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
    *,
    runtime: TarelRuntime | None = None,
) -> ContextPacketImpact:
    graph = _graph_store(runtime).load(graph_name)
    current_revision = graph_revision(graph)
    packet = load_context_packet(packet_path)
    _packet_graph, packet_revision = context_packet_graph_identity(packet)
    change_store = _graph_change_store(runtime)
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
    progress: Callable[[int, int, str], None] | None = None,
    runtime: TarelRuntime | None = None,
) -> IndexBuildResult:
    graph = _graph_store(runtime).load(name)
    resolved_model = resolve_model_path(model_path)
    return _retrieval_index(runtime).build(
        graph,
        embedder=LlamaCppEmbedding(resolved_model, n_threads=n_threads),
        model_path=resolved_model,
        batch_size=batch_size,
        progress=progress,
    )


def retrieval_index_status_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> dict[str, object]:
    graph = _graph_store(runtime).load(name)
    store = _retrieval_index(runtime)
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
    runtime: TarelRuntime | None = None,
) -> RelationshipChangeResult:
    store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> RelationshipPairProfile:
    graph = _graph_store(runtime).load(name)
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
    focus_name: str | None = None,
    expand_one_hop: bool = False,
    runtime: TarelRuntime | None = None,
) -> RelationshipDiscoveryResult:
    if expand_one_hop and focus_name is None:
        raise FocusFailure(
            "invalid_focus_scope",
            "--expand-one-hop requires --focus.",
        )
    store = _graph_store(runtime)
    graph = store.load(name)
    allowed_object_ids = None
    if focus_name is not None:
        focus, _lineages, focus_graphs = _load_current_focus(focus_name, runtime=runtime)
        focus_graph = focus_graphs.get(name)
        if focus_graph is None:
            raise FocusFailure(
                "graph_outside_focus",
                f"Graph is outside focus {focus_name}: {name}",
            )
        allowed_object_ids = graph_object_ids(focus, name)
        if expand_one_hop:
            allowed_object_ids = expand_graph_objects_one_hop(graph, allowed_object_ids)
    pairs = candidate_pairs(
        graph,
        object_reference=object_reference,
        field_name=field_name,
        max_pairs=max_pairs,
        allowed_object_ids=allowed_object_ids,
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


def list_relationships_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> tuple[GraphEdge, ...]:
    return relationship_candidates(_graph_store(runtime).load(name))


def decide_relationship_use_case(
    name: str,
    *,
    edge_id: str,
    state: str,
    reason: str,
    runtime: TarelRuntime | None = None,
) -> RelationshipChangeResult:
    store = _graph_store(runtime)
    graph = store.load(name)
    updated, edge = decide_relationship(
        graph,
        edge_id=edge_id,
        state=state,
        reason=reason,
    )
    path = store.save(updated)
    return RelationshipChangeResult(graph=updated, path=path, edge=edge)


def add_knowledge_document_use_case(
    document_id: str,
    source_path: Path,
    *,
    scope_reference: str,
    title: str | None = None,
    state: str = "draft",
    workspace_name: str | None = None,
    replace_existing: bool = False,
    runtime: TarelRuntime | None = None,
) -> KnowledgeChangeResult:
    store = _knowledge_store(runtime)
    if document_id in store.list() and not replace_existing:
        raise KnowledgeFailure(
            "knowledge_exists",
            f"Knowledge document already exists: {document_id}",
        )
    path = source_path.expanduser()
    if path.suffix.casefold() not in {".md", ".txt"}:
        raise KnowledgeFailure(
            "unsupported_knowledge_format",
            "Knowledge documents must be UTF-8 Markdown or text files.",
        )
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise KnowledgeFailure(
            "knowledge_source_not_found",
            f"Knowledge source file not found: {source_path}",
        ) from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise KnowledgeFailure(
            "invalid_knowledge_source",
            f"Could not read UTF-8 knowledge source: {source_path}",
        ) from exc
    scope = _validated_knowledge_scope(
        KnowledgeScope.parse(scope_reference),
        workspace_name=workspace_name,
        runtime=runtime,
    )
    document = KnowledgeDocument(
        id=document_id,
        title=(title or path.stem.replace("-", " ").replace("_", " ")).strip(),
        scope=scope,
        content=content,
        source_name=path.name,
        state=state,
    )
    return KnowledgeChangeResult(document=document, path=store.save(document))


def list_knowledge_documents_use_case(
    *,
    runtime: TarelRuntime | None = None,
) -> tuple[KnowledgeDocument, ...]:
    store = _knowledge_store(runtime)
    return tuple(store.load(document_id) for document_id in store.list())


def load_knowledge_document_use_case(
    document_id: str,
    *,
    runtime: TarelRuntime | None = None,
) -> KnowledgeDocument:
    return _knowledge_store(runtime).load(document_id)


def resolve_knowledge_use_case(
    graph_name: str,
    object_reference: str,
    *,
    mode: str = "scoped",
    document_ids: tuple[str, ...] = (),
    workspace_name: str | None = None,
    max_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    runtime: TarelRuntime | None = None,
) -> KnowledgeContext:
    graph = _graph_store(runtime).load(graph_name)
    node, _reference = resolve_annotation_target(graph, object_reference)
    workspace = (
        _workspace_store(runtime).load(workspace_name) if workspace_name is not None else None
    )
    _require_graph_in_knowledge_workspace(graph_name, workspace)
    return resolve_knowledge(
        list_knowledge_documents_use_case(runtime=runtime),
        graph,
        node,
        workspace=workspace,
        mode=mode,
        document_ids=document_ids,
        max_characters=max_characters,
    )


def plan_annotations_use_case(
    name: str,
    *,
    namespace: str | None = None,
    objects: set[str] | None = None,
    limit: int | None = None,
    missing_only: bool = True,
    sample_limit: int = 0,
    profile_row_limit: int = 0,
    include_small_domain_values: bool = False,
    config_path: Path | None = None,
    knowledge_mode: str = "none",
    knowledge_document_ids: tuple[str, ...] = (),
    knowledge_workspace: str | None = None,
    max_knowledge_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    runtime: TarelRuntime | None = None,
) -> tuple[AnnotationTask, ...]:
    graph = _graph_store(runtime).load(name)
    return _plan_graph_annotations(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
        sample_limit=sample_limit,
        profile_row_limit=profile_row_limit,
        include_small_domain_values=include_small_domain_values,
        config_path=config_path,
        knowledge_mode=knowledge_mode,
        knowledge_document_ids=knowledge_document_ids,
        knowledge_workspace=knowledge_workspace,
        max_knowledge_characters=max_knowledge_characters,
        runtime=runtime,
    )


def plan_focus_annotations_use_case(
    focus_name: str,
    *,
    namespace: str | None = None,
    objects: set[str] | None = None,
    limit: int | None = None,
    missing_only: bool = True,
    sample_limit: int = 0,
    profile_row_limit: int = 0,
    include_small_domain_values: bool = False,
    config_path: Path | None = None,
    knowledge_mode: str = "none",
    knowledge_document_ids: tuple[str, ...] = (),
    knowledge_workspace: str | None = None,
    max_knowledge_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    runtime: TarelRuntime | None = None,
) -> tuple[AnnotationTask, ...]:
    if limit is not None and limit < 1:
        raise AnnotationFailure(
            "invalid_annotation_limit",
            "Focus annotation limit must be positive.",
        )
    focus, _lineages, graphs = _load_current_focus(focus_name, runtime=runtime)
    depths = {item.id: item.depth for item in focus.members}
    tasks: list[AnnotationTask] = []
    for graph_name, graph in sorted(graphs.items()):
        object_ids = graph_object_ids(focus, graph_name)
        selected_nodes = [
            item for item in graph.nodes if item.id in object_ids and item.type in {"table", "view"}
        ]
        selected_labels = {item.label for item in selected_nodes}
        if objects:
            requested = {item.casefold() for item in objects}
            selected_labels = {
                item.label
                for item in selected_nodes
                if item.label.casefold() in requested
                or str(item.metadata.get("name", "")).casefold() in requested
                or f"{graph.catalog}.{item.label}".casefold() in requested
            }
        if not selected_labels:
            continue
        tasks.extend(
            _plan_graph_annotations(
                graph,
                namespace=namespace,
                objects=selected_labels,
                limit=None,
                missing_only=missing_only,
                sample_limit=sample_limit,
                profile_row_limit=profile_row_limit,
                include_small_domain_values=include_small_domain_values,
                config_path=config_path,
                knowledge_mode=knowledge_mode,
                knowledge_document_ids=knowledge_document_ids,
                knowledge_workspace=knowledge_workspace,
                max_knowledge_characters=max_knowledge_characters,
                runtime=runtime,
            )
        )
    ordered = tuple(
        sorted(
            tasks,
            key=lambda item: (
                depths.get(f"graph:{item.graph_name}:{item.target_id}", focus.max_hops + 1),
                item.graph_name,
                item.target_label.casefold(),
                item.target_id,
            ),
        )
    )
    return ordered[:limit] if limit is not None else ordered


def apply_annotation_use_case(
    name: str,
    payload: dict[str, Any],
    *,
    source: str = "agent",
    runtime: TarelRuntime | None = None,
) -> AnnotationApplyResult:
    store = _graph_store(runtime)
    graph = store.load(name)
    envelope = AnnotationProposalEnvelope.from_dict(payload)
    _validate_knowledge_references(envelope.context_documents, runtime=runtime)
    updated = apply_annotation_proposal(graph, envelope, source=source)
    path = store.save(updated)
    return AnnotationApplyResult(graph=updated, path=path, target_id=envelope.target_id)


def show_annotation_use_case(
    name: str,
    reference: str,
    *,
    runtime: TarelRuntime | None = None,
) -> AnnotationReviewRecord:
    graph = _graph_store(runtime).load(name)
    return annotation_review_record(graph, reference)


def list_annotation_reviews_use_case(
    name: str,
    *,
    states: frozenset[str] | None = None,
    runtime: TarelRuntime | None = None,
) -> tuple[AnnotationReviewRecord, ...]:
    graph = _graph_store(runtime).load(name)
    return list_annotation_reviews(graph, states=states)


def edit_annotation_use_case(
    name: str,
    reference: str,
    patch: dict[str, Any],
    *,
    reason: str,
    runtime: TarelRuntime | None = None,
) -> AnnotationReviewResult:
    store = _graph_store(runtime)
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
    runtime: TarelRuntime | None = None,
) -> AnnotationReviewResult:
    store = _graph_store(runtime)
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
    profile_row_limit: int = 0,
    include_small_domain_values: bool = False,
    config_path: Path | None = None,
    knowledge_mode: str = "none",
    knowledge_document_ids: tuple[str, ...] = (),
    knowledge_workspace: str | None = None,
    max_knowledge_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    progress: Callable[[int, int, str, str], None] | None = None,
    runtime: TarelRuntime | None = None,
) -> AnnotationBatchResult:
    store = _graph_store(runtime)
    graph = store.load(name)
    tasks = _plan_graph_annotations(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
        sample_limit=sample_limit,
        profile_row_limit=profile_row_limit,
        include_small_domain_values=include_small_domain_values,
        config_path=config_path,
        knowledge_mode=knowledge_mode,
        knowledge_document_ids=knowledge_document_ids,
        knowledge_workspace=knowledge_workspace,
        max_knowledge_characters=max_knowledge_characters,
        runtime=runtime,
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
    profile_row_limit: int,
    include_small_domain_values: bool,
    config_path: Path | None,
    knowledge_mode: str,
    knowledge_document_ids: tuple[str, ...],
    knowledge_workspace: str | None,
    max_knowledge_characters: int,
    runtime: TarelRuntime | None,
) -> tuple[AnnotationTask, ...]:
    if not 0 <= sample_limit <= 10:
        raise ConnectorFailure("invalid_sample_limit", "Sample limit must be between 0 and 10.")
    if not 0 <= profile_row_limit <= 100_000:
        raise ConnectorFailure(
            "invalid_profile_row_limit",
            "Annotation profile row limit must be between 0 and 100000.",
        )
    if include_small_domain_values and profile_row_limit == 0:
        raise ConnectorFailure(
            "small_domain_values_without_profile",
            "Small-domain values require a positive annotation profile row limit.",
        )
    tasks = plan_annotation_tasks(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
    )
    node_by_id = graph.node_by_id()
    samples: dict[str, SampleResult] = {}
    profiles: dict[str, ObjectProfileResult] = {}
    if sample_limit:
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
    if profile_row_limit:
        for task in tasks:
            node = node_by_id[task.target_id]
            profiles[task.target_id] = profile_connector_use_case(
                graph.connector,
                config_path=config_path,
                database=graph.catalog,
                namespace=str(node.metadata["namespace"]),
                object_name=str(node.metadata["name"]),
                row_limit=profile_row_limit,
                include_values=include_small_domain_values,
            )
    knowledge = _knowledge_contexts_for_tasks(
        graph,
        tasks,
        mode=knowledge_mode,
        document_ids=knowledge_document_ids,
        workspace_name=knowledge_workspace,
        max_characters=max_knowledge_characters,
        runtime=runtime,
    )
    return plan_annotation_tasks(
        graph,
        namespace=namespace,
        objects=objects,
        limit=limit,
        missing_only=missing_only,
        samples_by_target=samples,
        profiles_by_target=profiles,
        knowledge_by_target=knowledge,
    )


def _knowledge_contexts_for_tasks(
    graph: GraphDocument,
    tasks: tuple[AnnotationTask, ...],
    *,
    mode: str,
    document_ids: tuple[str, ...],
    workspace_name: str | None,
    max_characters: int,
    runtime: TarelRuntime | None,
) -> dict[str, KnowledgeContext]:
    if mode == "none" and not document_ids:
        return {}
    workspace = (
        _workspace_store(runtime).load(workspace_name) if workspace_name is not None else None
    )
    _require_graph_in_knowledge_workspace(graph.name, workspace)
    documents = list_knowledge_documents_use_case(runtime=runtime)
    node_by_id = graph.node_by_id()
    return {
        task.target_id: resolve_knowledge(
            documents,
            graph,
            node_by_id[task.target_id],
            workspace=workspace,
            mode=mode,
            document_ids=document_ids,
            max_characters=max_characters,
        )
        for task in tasks
    }


def _validated_knowledge_scope(
    scope: KnowledgeScope,
    *,
    workspace_name: str | None,
    runtime: TarelRuntime | None,
) -> KnowledgeScope:
    if scope.kind == "global":
        return scope
    if scope.kind == "system":
        if workspace_name is None:
            raise KnowledgeFailure(
                "knowledge_workspace_required",
                "Registering system knowledge requires --workspace for validation.",
            )
        workspace = _workspace_store(runtime).load(workspace_name)
        if not any(
            item.name.casefold() == scope.reference.casefold()
            for item in workspace.systems
        ):
            raise KnowledgeFailure(
                "knowledge_scope_not_found",
                f"Workspace system not found: {scope.reference}",
            )
        system = next(
            item for item in workspace.systems if item.name.casefold() == scope.reference.casefold()
        )
        return KnowledgeScope(
            kind="system",
            reference=system.name,
            workspace=workspace.name,
        )
    graph_name = scope.reference if scope.kind == "graph" else scope.graph
    assert graph_name is not None
    graph = _graph_store(runtime).load(graph_name)
    if scope.kind == "graph":
        return KnowledgeScope(kind="graph", reference=graph.name)
    if scope.kind == "schema":
        namespaces = {
            str(item.metadata.get("namespace") or "")
            for item in graph.nodes
            if item.type in {"table", "view"}
        }
        matches = [item for item in namespaces if item.casefold() == scope.reference.casefold()]
        if len(matches) != 1:
            raise KnowledgeFailure(
                "knowledge_scope_not_found",
                f"Graph schema not found: {graph.name}:{scope.reference}",
            )
        return KnowledgeScope(kind="schema", graph=graph.name, reference=matches[0])
    node, reference = resolve_annotation_target(graph, scope.reference)
    if node.type not in {"table", "view"}:
        raise KnowledgeFailure(
            "invalid_knowledge_scope",
            "Object knowledge must target a table or view.",
        )
    return KnowledgeScope(kind="object", graph=graph.name, reference=reference)


def _require_graph_in_knowledge_workspace(
    graph_name: str,
    workspace: WorkspaceDocument | None,
) -> None:
    if workspace is not None and not any(
        graph_name in system.graphs for system in workspace.systems
    ):
        raise KnowledgeFailure(
            "knowledge_graph_outside_workspace",
            f"Graph {graph_name} is outside workspace {workspace.name}.",
        )


def _validate_knowledge_references(
    references: tuple[KnowledgeReference, ...],
    *,
    runtime: TarelRuntime | None,
) -> None:
    store = _knowledge_store(runtime)
    for reference in references:
        document = store.load(reference.id)
        valid_length = 0 < reference.characters <= len(document.content)
        expected_truncation = reference.characters < len(document.content)
        if (
            reference.title != document.title
            or reference.scope != document.scope
            or reference.state != document.state
            or reference.revision != document.revision
            or not valid_length
            or reference.truncated != expected_truncation
        ):
            raise KnowledgeFailure(
                "stale_knowledge_reference",
                f"Knowledge reference is stale or inconsistent: {reference.id}",
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
    if "probe_relationships" not in connector.manifest.capabilities or not hasattr(
        connector, "probe_relationships"
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


def _load_current_focus(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> tuple[FocusDocument, dict[str, LineageDocument], dict[str, GraphDocument]]:
    focus = _focus_store(runtime).load(name)
    lineage_store = _lineage_store(runtime)
    graph_store = _graph_store(runtime)
    available_lineages = set(lineage_store.list())
    available_graphs = set(graph_store.list())
    missing = [
        item
        for item in focus.sources
        if (item.kind == "lineage" and item.name not in available_lineages)
        or (item.kind == "graph" and item.name not in available_graphs)
    ]
    if missing:
        rendered = ", ".join(f"{item.kind}:{item.name}" for item in missing)
        raise FocusFailure(
            "focus_stale",
            f"Focus {name} references missing sources: {rendered}",
        )
    lineages = {
        item.name: lineage_store.load(item.name) for item in focus.sources if item.kind == "lineage"
    }
    graphs = {
        item.name: graph_store.load(item.name) for item in focus.sources if item.kind == "graph"
    }
    require_current_focus(focus, lineages=lineages, graphs=graphs)
    return focus, lineages, graphs


def _require_unique_names(names: tuple[str, ...], label: str) -> None:
    if len(names) != len(set(names)):
        raise FocusFailure(
            "duplicate_focus_source",
            f"Focus {label} sources must not contain duplicates.",
        )
