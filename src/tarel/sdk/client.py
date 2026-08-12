"""Local-first SDK facades that share implementation with the CLI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from tarel.annotations.contracts import AnnotationTask
from tarel.annotations.review import AnnotationReviewRecord
from tarel.application import (
    AnnotationApplyResult,
    AnnotationBatchResult,
    AnnotationReviewResult,
    FocusBuildResult,
    GraphBuildResult,
    GraphRefreshResult,
    KnowledgeChangeResult,
    RelationshipChangeResult,
    RelationshipDiscoveryResult,
    WorkspaceChangeResult,
    WorkspaceRelationshipChangeResult,
    add_knowledge_document_use_case,
    add_relationship_use_case,
    add_workspace_relationship_use_case,
    apply_annotation_use_case,
    build_focus_use_case,
    build_graph_use_case,
    build_retrieval_index_use_case,
    check_relationship_use_case,
    compile_context_prefix_use_case,
    compile_context_use_case,
    compile_workspace_context_prefix_use_case,
    compile_workspace_context_use_case,
    context_packet_impact_use_case,
    create_workspace_use_case,
    decide_annotation_use_case,
    decide_relationship_use_case,
    decide_workspace_relationship_use_case,
    define_workspace_area_use_case,
    define_workspace_system_use_case,
    define_workspace_zone_use_case,
    diff_context_packets_use_case,
    discover_relationships_use_case,
    download_embedding_model_use_case,
    edit_annotation_use_case,
    embedding_model_status_use_case,
    list_annotation_reviews_use_case,
    list_focuses_use_case,
    list_graphs_use_case,
    list_knowledge_documents_use_case,
    list_relationships_use_case,
    list_workspaces_use_case,
    load_focus_use_case,
    load_graph_use_case,
    load_knowledge_document_use_case,
    load_workspace_use_case,
    plan_annotations_use_case,
    plan_focus_annotations_use_case,
    refresh_graph_use_case,
    resolve_knowledge_use_case,
    resolve_workspace_scope_use_case,
    retrieval_index_status_use_case,
    run_annotation_batch_use_case,
    search_graph_use_case,
    search_workspace_use_case,
    show_annotation_use_case,
    show_workspace_zone_use_case,
)
from tarel.connectors.contracts import CatalogResult, ProbeResult, RelationshipPairProfile
from tarel.context import ContextResult
from tarel.context_caching import ContextCacheParts, split_context_packet
from tarel.context_output import DEFAULT_MAX_CONTEXT_CHARACTERS
from tarel.context_packets import ContextPacketDiff, ContextPacketImpact
from tarel.focus.contracts import FocusDocument
from tarel.graph.contracts import GraphDocument, GraphEdge
from tarel.grounding import GroundingAsset, GroundingBundle
from tarel.grounding_application import (
    compile_graph_grounding_use_case,
    compile_workspace_grounding_use_case,
    describe_grounding_asset_use_case,
)
from tarel.knowledge.contracts import (
    DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    KnowledgeContext,
    KnowledgeDocument,
)
from tarel.lineage.application import (
    LineageChangeResult,
    LineageProviderRunResult,
    LineageReviewResult,
    ManualHopResult,
    ManualJobResult,
    add_manual_hop_use_case,
    add_manual_job_use_case,
    apply_lineage_proposal_use_case,
    build_lineage_use_case,
    decide_lineage_item_use_case,
    find_lineage_references_use_case,
    lineage_status_use_case,
    list_lineage_items_use_case,
    list_lineages_use_case,
    load_lineage_use_case,
    next_lineage_task_use_case,
    process_lineage_view_use_case,
    run_lineage_provider_use_case,
    table_lineage_view_use_case,
    trace_upstream_use_case,
)
from tarel.lineage.contracts import LineageDocument
from tarel.lineage.core import ProcessStep, TableLineage
from tarel.lineage.review import LineageReviewItem
from tarel.lineage.status import LineageStatus
from tarel.lineage.tasks import LineageTask
from tarel.lineage.traversal import LineageReference, UpstreamTrace
from tarel.retrieval.contracts import IndexBuildResult
from tarel.retrieval.local import DEFAULT_MODEL_NAME, ModelDownloadResult
from tarel.runtime import TarelRuntime
from tarel.search import SearchResults
from tarel.sources.application import (
    SourceChangeResult,
    SourceCheck,
    build_source_graph_use_case,
    check_source_use_case,
    configure_source_use_case,
    discover_source_use_case,
    list_sources_use_case,
    load_source_use_case,
    probe_source_use_case,
    refresh_source_graph_use_case,
)
from tarel.sources.contracts import SourceProfile
from tarel.ui.presentation import browser_graph, browser_workspace
from tarel.workspaces.contracts import (
    WorkspaceDocument,
    WorkspaceFailure,
    WorkspaceRelationship,
)
from tarel.workspaces.core import ResolvedZone
from tarel.workspaces.scope import ResolvedScope, ScopeSelection


class Tarel:
    """One embedded TAREL client bound to an explicit local state directory."""

    __slots__ = (
        "annotation",
        "context",
        "focus",
        "graph",
        "grounding",
        "index",
        "knowledge",
        "lineage",
        "model",
        "relationship",
        "runtime",
        "search",
        "source",
        "view",
        "workspace",
    )

    def __init__(self, root: str | Path) -> None:
        self.runtime = TarelRuntime.local(root)
        self.graph = GraphAPI(self.runtime)
        self.workspace = WorkspaceAPI(self.runtime)
        self.search = SearchAPI(self.runtime)
        self.source = SourceAPI(self.runtime)
        self.context = ContextAPI(self.runtime)
        self.grounding = GroundingAPI(self.runtime)
        self.lineage = LineageAPI(self.runtime)
        self.focus = FocusAPI(self.runtime)
        self.annotation = AnnotationAPI(self.runtime)
        self.relationship = RelationshipAPI(self.runtime)
        self.model = ModelAPI(self.runtime)
        self.index = IndexAPI(self.runtime)
        self.knowledge = KnowledgeAPI(self.runtime)
        self.view = ViewAPI(self.runtime)

    @property
    def root(self) -> Path:
        return self.runtime.root


class _RuntimeAPI:
    __slots__ = ("_runtime",)

    def __init__(self, runtime: TarelRuntime) -> None:
        self._runtime = runtime


class GraphAPI(_RuntimeAPI):
    def list(self) -> tuple[str, ...]:
        return list_graphs_use_case(runtime=self._runtime)

    def load(self, name: str) -> GraphDocument:
        return load_graph_use_case(name, runtime=self._runtime)

    def build(
        self,
        name: str,
        *,
        connector: str,
        config: str | Path | None = None,
        database: str | None = None,
        namespace: str | None = None,
    ) -> GraphBuildResult:
        return build_graph_use_case(
            name,
            connector_name=connector,
            config_path=_optional_path(config),
            database=database,
            namespace=namespace,
            runtime=self._runtime,
        )

    def refresh(
        self,
        name: str,
        *,
        config: str | Path | None = None,
        namespace: str | None = None,
    ) -> GraphRefreshResult:
        return refresh_graph_use_case(
            name,
            config_path=_optional_path(config),
            namespace=namespace,
            runtime=self._runtime,
        )


class SourceAPI(_RuntimeAPI):
    """Manage private logical sources without storing resolved credentials."""

    def configure(
        self,
        name: str,
        *,
        connector: str,
        config_reference: str | None = None,
        database: str | None = None,
        namespace: str | None = None,
        graphs: tuple[str, ...] = (),
        replace: bool = False,
    ) -> SourceChangeResult:
        return configure_source_use_case(
            name,
            connector=connector,
            config_reference=config_reference,
            database=database,
            namespace=namespace,
            graphs=graphs,
            replace=replace,
            runtime=self._runtime,
        )

    def list(self) -> tuple[str, ...]:
        return list_sources_use_case(runtime=self._runtime)

    def load(self, name: str) -> SourceProfile:
        return load_source_use_case(name, runtime=self._runtime)

    def check(self, name: str) -> SourceCheck:
        return check_source_use_case(name, runtime=self._runtime)

    def probe(self, name: str, *, database: str | None = None) -> ProbeResult:
        return probe_source_use_case(
            name,
            database=database,
            runtime=self._runtime,
        )

    def discover(
        self,
        name: str,
        *,
        database: str | None = None,
        namespace: str | None = None,
    ) -> CatalogResult:
        return discover_source_use_case(
            name,
            database=database,
            namespace=namespace,
            runtime=self._runtime,
        )

    def build_graph(
        self,
        name: str,
        graph: str,
        *,
        database: str | None = None,
        namespace: str | None = None,
    ) -> GraphBuildResult:
        return build_source_graph_use_case(
            name,
            graph,
            database=database,
            namespace=namespace,
            runtime=self._runtime,
        )

    def refresh_graph(
        self,
        name: str,
        graph: str,
        *,
        namespace: str | None = None,
    ) -> GraphRefreshResult:
        return refresh_source_graph_use_case(
            name,
            graph,
            namespace=namespace,
            runtime=self._runtime,
        )


class WorkspaceAPI(_RuntimeAPI):
    def create(
        self,
        name: str,
        *,
        description: str | None = None,
    ) -> WorkspaceChangeResult:
        return create_workspace_use_case(
            name,
            description=description,
            runtime=self._runtime,
        )

    def list(self) -> tuple[str, ...]:
        return list_workspaces_use_case(runtime=self._runtime)

    def load(self, name: str) -> WorkspaceDocument:
        return load_workspace_use_case(name, runtime=self._runtime)

    def define_system(
        self,
        name: str,
        system: str,
        *,
        graphs: tuple[str, ...],
        description: str | None = None,
    ) -> WorkspaceChangeResult:
        return define_workspace_system_use_case(
            name,
            system,
            graph_names=graphs,
            description=description,
            runtime=self._runtime,
        )

    def define_area(
        self,
        name: str,
        system: str,
        area: str,
        *,
        schemas: tuple[str, ...],
        description: str | None = None,
    ) -> WorkspaceChangeResult:
        return define_workspace_area_use_case(
            name,
            system,
            area,
            schema_references=schemas,
            description=description,
            runtime=self._runtime,
        )

    def define_zone(
        self,
        name: str,
        system: str,
        zone: str,
        *,
        objects: tuple[str, ...],
        description: str | None = None,
    ) -> WorkspaceChangeResult:
        return define_workspace_zone_use_case(
            name,
            system,
            zone,
            object_references=objects,
            description=description,
            runtime=self._runtime,
        )

    def zone(self, name: str, system: str, zone: str) -> ResolvedZone:
        return show_workspace_zone_use_case(
            name,
            system,
            zone,
            runtime=self._runtime,
        )

    def relationships(self, name: str) -> tuple[WorkspaceRelationship, ...]:
        return self.load(name).relationships

    def add_relationship(
        self,
        name: str,
        *,
        source: str,
        target: str,
        reason: str,
        validated: bool = False,
    ) -> WorkspaceRelationshipChangeResult:
        return add_workspace_relationship_use_case(
            name,
            source_reference=source,
            target_reference=target,
            reason=reason,
            validated=validated,
            runtime=self._runtime,
        )

    def decide_relationship(
        self,
        name: str,
        relationship_id: str,
        *,
        state: str,
        reason: str,
    ) -> WorkspaceRelationshipChangeResult:
        return decide_workspace_relationship_use_case(
            name,
            relationship_id,
            state=state,
            reason=reason,
            runtime=self._runtime,
        )

    def scope(
        self,
        name: str,
        *,
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
    ) -> ResolvedScope:
        systems, graphs, areas, schemas, zones = _workspace_scope_arguments(
            selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        return resolve_workspace_scope_use_case(
            name,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
            runtime=self._runtime,
        )


class SearchAPI(_RuntimeAPI):
    def graph(
        self,
        name: str,
        query: str,
        *,
        limit: int = 20,
        namespace: str | None = None,
        mode: str = "lexical",
        model_path: str | Path | None = None,
        n_threads: int | None = None,
        annotation_states: frozenset[str] | None = None,
        validated_only: bool = False,
    ) -> SearchResults:
        return search_graph_use_case(
            name,
            query,
            limit=limit,
            namespace=namespace,
            mode=mode,
            model_path=_optional_path(model_path),
            n_threads=n_threads,
            annotation_states=annotation_states,
            validated_only=validated_only,
            runtime=self._runtime,
        )

    def workspace(
        self,
        name: str,
        query: str,
        *,
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        limit: int = 20,
        mode: str = "lexical",
        model_path: str | Path | None = None,
        n_threads: int | None = None,
        annotation_states: frozenset[str] | None = None,
        validated_only: bool = False,
    ) -> SearchResults:
        systems, graphs, areas, schemas, zones = _workspace_scope_arguments(
            selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        return search_workspace_use_case(
            name,
            query,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
            limit=limit,
            mode=mode,
            model_path=_optional_path(model_path),
            n_threads=n_threads,
            annotation_states=annotation_states,
            validated_only=validated_only,
            runtime=self._runtime,
        )


class ContextAPI(_RuntimeAPI):
    def prefix_graph(
        self,
        name: str,
        *,
        namespace: str | None = None,
        max_objects: int = 250,
        max_joins: int = 500,
        max_fields_per_object: int = 50,
        max_characters: int = 500_000,
        annotation_states: frozenset[str] | None = None,
        validated_only: bool = False,
    ) -> ContextResult:
        return compile_context_prefix_use_case(
            name,
            namespace=namespace,
            max_objects=max_objects,
            max_joins=max_joins,
            max_fields_per_object=max_fields_per_object,
            max_characters=max_characters,
            annotation_states=annotation_states,
            validated_only=validated_only,
            runtime=self._runtime,
        )

    def prefix_workspace(
        self,
        name: str,
        *,
        selection: ScopeSelection | None = None,
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
    ) -> ContextResult:
        systems, graphs, areas, schemas, zones = _workspace_scope_arguments(
            selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        return compile_workspace_context_prefix_use_case(
            name,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
            max_objects=max_objects,
            max_joins=max_joins,
            max_fields_per_object=max_fields_per_object,
            max_characters=max_characters,
            annotation_states=annotation_states,
            validated_only=validated_only,
            runtime=self._runtime,
        )

    def split(self, packet: ContextResult) -> ContextCacheParts:
        return split_context_packet(packet)

    def diff(self, left: str | Path, right: str | Path) -> ContextPacketDiff:
        return diff_context_packets_use_case(Path(left), Path(right))

    def impact(self, packet: str | Path, *, graph: str) -> ContextPacketImpact:
        return context_packet_impact_use_case(
            Path(packet),
            graph,
            runtime=self._runtime,
        )

    def graph(
        self,
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
        model_path: str | Path | None = None,
        n_threads: int | None = None,
        annotation_states: frozenset[str] | None = None,
        validated_only: bool = False,
    ) -> ContextResult:
        return compile_context_use_case(
            name,
            query,
            namespace=namespace,
            seed_limit=seed_limit,
            max_objects=max_objects,
            max_joins=max_joins,
            max_hops=max_hops,
            max_fields_per_object=max_fields_per_object,
            max_characters=max_characters,
            mode=mode,
            model_path=_optional_path(model_path),
            n_threads=n_threads,
            annotation_states=annotation_states,
            validated_only=validated_only,
            runtime=self._runtime,
        )

    def workspace(
        self,
        name: str,
        query: str,
        *,
        selection: ScopeSelection | None = None,
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
        model_path: str | Path | None = None,
        n_threads: int | None = None,
        annotation_states: frozenset[str] | None = None,
        validated_only: bool = False,
    ) -> ContextResult:
        systems, graphs, areas, schemas, zones = _workspace_scope_arguments(
            selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        return compile_workspace_context_use_case(
            name,
            query,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
            seed_limit=seed_limit,
            max_objects=max_objects,
            max_joins=max_joins,
            max_hops=max_hops,
            max_fields_per_object=max_fields_per_object,
            max_characters=max_characters,
            mode=mode,
            model_path=_optional_path(model_path),
            n_threads=n_threads,
            annotation_states=annotation_states,
            validated_only=validated_only,
            runtime=self._runtime,
        )


class GroundingAPI(_RuntimeAPI):
    """Compile agent-ready semantic context with source and lineage identity."""

    def context(
        self,
        question: str,
        *,
        graph: str | None = None,
        workspace: str | None = None,
        namespace: str | None = None,
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        lineages: tuple[str, ...] = (),
        sources: tuple[str, ...] = (),
        trace: str | None = None,
        lineage_limit: int = 8,
        lineage_mode: str = "bm25",
        lineage_states: frozenset[str] | None = None,
        seed_limit: int = 3,
        max_objects: int = 10,
        max_joins: int = 12,
        max_hops: int = 2,
        max_trace_hops: int = 12,
        max_fields_per_object: int = 12,
        max_characters: int = DEFAULT_MAX_CONTEXT_CHARACTERS,
        mode: str = "lexical",
        model_path: str | Path | None = None,
        n_threads: int | None = None,
        annotation_states: frozenset[str] | None = None,
        validated_only: bool = False,
    ) -> GroundingBundle:
        if (graph is None) == (workspace is None):
            raise WorkspaceFailure(
                "invalid_grounding_scope",
                "Grounding requires exactly one graph or workspace.",
            )
        common = {
            "lineage_names": lineages,
            "source_names": sources,
            "trace_reference": trace,
            "lineage_limit": lineage_limit,
            "lineage_mode": lineage_mode,
            "lineage_states": lineage_states,
            "seed_limit": seed_limit,
            "max_objects": max_objects,
            "max_joins": max_joins,
            "max_hops": max_hops,
            "max_trace_hops": max_trace_hops,
            "max_fields_per_object": max_fields_per_object,
            "max_characters": max_characters,
            "mode": mode,
            "model_path": _optional_path(model_path),
            "n_threads": n_threads,
            "annotation_states": annotation_states,
            "validated_only": validated_only,
            "runtime": self._runtime,
        }
        if graph is not None:
            if selection is not None or any((systems, graphs, areas, schemas, zones)):
                raise WorkspaceFailure(
                    "invalid_grounding_scope",
                    "Workspace scope filters cannot be used with a graph.",
                )
            return compile_graph_grounding_use_case(
                graph,
                question,
                namespace=namespace,
                **common,
            )
        if namespace is not None:
            raise WorkspaceFailure(
                "invalid_grounding_scope",
                "Use workspace schemas instead of namespace for workspace grounding.",
            )
        systems, graphs, areas, schemas, zones = _workspace_scope_arguments(
            selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        assert workspace is not None
        return compile_workspace_grounding_use_case(
            workspace,
            question,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
            **common,
        )

    def find(
        self,
        query: str,
        *,
        graph: str | None = None,
        workspace: str | None = None,
        namespace: str | None = None,
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        lineages: tuple[str, ...] = (),
        sources: tuple[str, ...] = (),
        limit: int = 10,
        lineage_mode: str = "bm25",
        mode: str = "lexical",
        model_path: str | Path | None = None,
        n_threads: int | None = None,
        annotation_states: frozenset[str] | None = None,
        validated_only: bool = False,
    ) -> GroundingBundle:
        """Return ranked semantic assets without relationship expansion."""
        if not 1 <= limit <= 20:
            raise WorkspaceFailure(
                "invalid_grounding_limit",
                "Grounding find limit must be between 1 and 20.",
            )
        return self.context(
            query,
            graph=graph,
            workspace=workspace,
            namespace=namespace,
            selection=selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
            lineages=lineages,
            sources=sources,
            lineage_mode=lineage_mode,
            seed_limit=limit,
            max_objects=limit,
            max_joins=0,
            max_hops=0,
            mode=mode,
            model_path=model_path,
            n_threads=n_threads,
            annotation_states=annotation_states,
            validated_only=validated_only,
        )

    def describe(
        self,
        graph: str,
        reference: str,
        *,
        source: str | None = None,
    ) -> GroundingAsset:
        return describe_grounding_asset_use_case(
            graph,
            reference,
            source_name=source,
            runtime=self._runtime,
        )

    def upstream(
        self,
        reference: str,
        *,
        lineages: tuple[str, ...],
        graph: str | None = None,
        workspace: str | None = None,
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        max_hops: int = 12,
        states: frozenset[str] | None = None,
    ) -> UpstreamTrace:
        if (graph is None) == (workspace is None):
            raise WorkspaceFailure(
                "invalid_grounding_scope",
                "Grounding requires exactly one graph or workspace.",
            )
        if graph is not None:
            if selection is not None or any((systems, graphs, areas, schemas, zones)):
                raise WorkspaceFailure(
                    "invalid_grounding_scope",
                    "Workspace scope filters cannot be used with a graph.",
                )
            graph_names = (graph,)
        else:
            assert workspace is not None
            resolved = _resolve_workspace_scope(
                self._runtime,
                workspace,
                selection=selection,
                systems=systems,
                graphs=graphs,
                areas=areas,
                schemas=schemas,
                zones=zones,
            )
            graph_names = resolved.graph_names
        return trace_upstream_use_case(
            reference,
            lineage_names=lineages,
            graph_names=graph_names,
            max_hops=max_hops,
            states=states,
            runtime=self._runtime,
        )


class LineageAPI(_RuntimeAPI):
    def list(self) -> tuple[str, ...]:
        return list_lineages_use_case(runtime=self._runtime)

    def load(self, name: str) -> LineageDocument:
        return load_lineage_use_case(name, runtime=self._runtime)

    def build(self, name: str, *, source: str | Path) -> LineageChangeResult:
        return build_lineage_use_case(
            name,
            source_path=Path(source),
            runtime=self._runtime,
        )

    def add_job(
        self,
        name: str,
        *,
        kind: str,
        job_name: str,
        qualified_name: str,
        language: str,
        source_reference: str,
        description: str,
        expected_revision: str | None = None,
    ) -> ManualJobResult:
        return add_manual_job_use_case(
            name,
            kind=kind,
            job_name=job_name,
            qualified_name=qualified_name,
            language=language,
            source_reference=source_reference,
            description=description,
            expected_revision=expected_revision,
            runtime=self._runtime,
        )

    def add_hop(
        self,
        name: str,
        *,
        job: str,
        source: str,
        target: str,
        operation: str,
        role: str = "business_data",
        evidence_reference: str,
        reason: str,
        line_start: int = 1,
        line_end: int = 1,
        expected_revision: str | None = None,
    ) -> ManualHopResult:
        return add_manual_hop_use_case(
            name,
            job_reference=job,
            source=source,
            target=target,
            operation=operation,
            role=role,
            evidence_reference=evidence_reference,
            reason=reason,
            line_start=line_start,
            line_end=line_end,
            expected_revision=expected_revision,
            runtime=self._runtime,
        )

    def next(self, name: str, *, source: str | Path) -> LineageTask | None:
        return next_lineage_task_use_case(
            name,
            source_path=Path(source),
            runtime=self._runtime,
        )

    def apply(
        self,
        name: str,
        *,
        source: str | Path,
        proposal: dict[str, Any],
    ) -> LineageChangeResult:
        return apply_lineage_proposal_use_case(
            name,
            source_path=Path(source),
            payload=proposal,
            runtime=self._runtime,
        )

    def analyze(
        self,
        name: str,
        *,
        source: str | Path,
        provider: str,
        model: str | None = None,
        timeout: float = 180.0,
        retry: int = 1,
        limit: int | None = None,
        definitions: tuple[str, ...] = (),
        review_passes: int = 1,
        max_output_tokens: int | None = None,
        reasoning_effort: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> LineageProviderRunResult:
        return run_lineage_provider_use_case(
            name,
            source_path=Path(source),
            provider_name=provider,
            model=model,
            timeout=timeout,
            retry=retry,
            limit=limit,
            definition_references=definitions,
            review_passes=review_passes,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            progress=progress,
            runtime=self._runtime,
        )

    def find(
        self,
        query: str,
        *,
        lineages: tuple[str, ...],
        graphs: tuple[str, ...] = (),
        limit: int = 20,
        mode: str = "lexical",
        model_path: str | Path | None = None,
        n_threads: int | None = None,
    ) -> tuple[LineageReference, ...]:
        return find_lineage_references_use_case(
            query,
            lineage_names=lineages,
            graph_names=graphs,
            limit=limit,
            mode=mode,
            model_path=_optional_path(model_path),
            n_threads=n_threads,
            runtime=self._runtime,
        )

    def find_workspace(
        self,
        workspace: str,
        query: str,
        *,
        lineages: tuple[str, ...],
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        limit: int = 20,
        mode: str = "lexical",
        model_path: str | Path | None = None,
        n_threads: int | None = None,
    ) -> tuple[LineageReference, ...]:
        scope = _resolve_workspace_scope(
            self._runtime,
            workspace,
            selection=selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        return self.find(
            query,
            lineages=lineages,
            graphs=scope.graph_names,
            limit=limit,
            mode=mode,
            model_path=model_path,
            n_threads=n_threads,
        )

    def upstream(
        self,
        reference: str,
        *,
        lineages: tuple[str, ...],
        graphs: tuple[str, ...] = (),
        max_hops: int = 12,
        states: frozenset[str] | None = None,
    ) -> UpstreamTrace:
        return trace_upstream_use_case(
            reference,
            lineage_names=lineages,
            graph_names=graphs,
            max_hops=max_hops,
            states=states,
            runtime=self._runtime,
        )

    def upstream_workspace(
        self,
        workspace: str,
        reference: str,
        *,
        lineages: tuple[str, ...],
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        max_hops: int = 12,
        states: frozenset[str] | None = None,
    ) -> UpstreamTrace:
        scope = _resolve_workspace_scope(
            self._runtime,
            workspace,
            selection=selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        return self.upstream(
            reference,
            lineages=lineages,
            graphs=scope.graph_names,
            max_hops=max_hops,
            states=states,
        )

    def process(self, name: str) -> tuple[ProcessStep, ...]:
        return process_lineage_view_use_case(name, runtime=self._runtime)

    def tables(self, name: str) -> tuple[TableLineage, ...]:
        return table_lineage_view_use_case(name, runtime=self._runtime)

    def status(self, name: str) -> LineageStatus:
        return lineage_status_use_case(name, runtime=self._runtime)

    def reviews(
        self,
        name: str,
        *,
        states: frozenset[str] | None = None,
    ) -> tuple[LineageReviewItem, ...]:
        return list_lineage_items_use_case(name, states=states, runtime=self._runtime)

    def decide(
        self,
        name: str,
        claim_id: str,
        *,
        decision: str,
        reason: str,
        expected_revision: str | None = None,
    ) -> LineageReviewResult:
        return decide_lineage_item_use_case(
            name,
            claim_id,
            decision=decision,
            reason=reason,
            expected_revision=expected_revision,
            runtime=self._runtime,
        )


class ViewAPI(_RuntimeAPI):
    """Build the combined payload used by Space and Lineage canvases."""

    def graph(
        self,
        name: str,
        *,
        lineages: tuple[str, ...] = (),
        editable: bool = False,
    ) -> dict[str, object]:
        graph = load_graph_use_case(name, runtime=self._runtime)
        documents = tuple(load_lineage_use_case(item, runtime=self._runtime) for item in lineages)
        workspaces = tuple(
            load_workspace_use_case(item, runtime=self._runtime)
            for item in list_workspaces_use_case(runtime=self._runtime)
        )
        return browser_graph(
            graph,
            workspaces=workspaces,
            editable=editable,
            lineage_documents=documents,
        )

    def workspace(
        self,
        name: str,
        *,
        lineages: tuple[str, ...] = (),
        selection: ScopeSelection | None = None,
        systems: tuple[str, ...] = (),
        graphs: tuple[str, ...] = (),
        areas: tuple[str, ...] = (),
        schemas: tuple[str, ...] = (),
        zones: tuple[str, ...] = (),
        editable: bool = False,
    ) -> dict[str, object]:
        scope = _resolve_workspace_scope(
            self._runtime,
            name,
            selection=selection,
            systems=systems,
            graphs=graphs,
            areas=areas,
            schemas=schemas,
            zones=zones,
        )
        workspace = load_workspace_use_case(name, runtime=self._runtime)
        graph_documents = tuple(
            load_graph_use_case(item, runtime=self._runtime) for item in scope.graph_names
        )
        lineage_documents = tuple(
            load_lineage_use_case(item, runtime=self._runtime) for item in lineages
        )
        return browser_workspace(
            graph_documents,
            scope,
            workspace=workspace,
            editable=editable,
            lineage_documents=lineage_documents,
        )


class FocusAPI(_RuntimeAPI):
    def list(self) -> tuple[str, ...]:
        return list_focuses_use_case(runtime=self._runtime)

    def load(self, name: str) -> FocusDocument:
        return load_focus_use_case(name, runtime=self._runtime)

    def build(
        self,
        name: str,
        *,
        seed: str,
        lineages: tuple[str, ...],
        graphs: tuple[str, ...],
        max_hops: int = 12,
        states: frozenset[str] | None = None,
    ) -> FocusBuildResult:
        return build_focus_use_case(
            name,
            seed=seed,
            lineage_names=lineages,
            graph_names=graphs,
            max_hops=max_hops,
            states=states,
            runtime=self._runtime,
        )


class AnnotationAPI(_RuntimeAPI):
    def plan_graph(
        self,
        name: str,
        *,
        namespace: str | None = None,
        objects: set[str] | None = None,
        limit: int | None = None,
        missing_only: bool = True,
        sample_limit: int = 0,
        config: str | Path | None = None,
        knowledge: str = "none",
        knowledge_documents: tuple[str, ...] = (),
        knowledge_workspace: str | None = None,
        max_knowledge_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    ) -> tuple[AnnotationTask, ...]:
        return plan_annotations_use_case(
            name,
            namespace=namespace,
            objects=objects,
            limit=limit,
            missing_only=missing_only,
            sample_limit=sample_limit,
            config_path=_optional_path(config),
            knowledge_mode=knowledge,
            knowledge_document_ids=knowledge_documents,
            knowledge_workspace=knowledge_workspace,
            max_knowledge_characters=max_knowledge_characters,
            runtime=self._runtime,
        )

    def plan_focus(
        self,
        name: str,
        *,
        namespace: str | None = None,
        objects: set[str] | None = None,
        limit: int | None = None,
        missing_only: bool = True,
        sample_limit: int = 0,
        config: str | Path | None = None,
        knowledge: str = "none",
        knowledge_documents: tuple[str, ...] = (),
        knowledge_workspace: str | None = None,
        max_knowledge_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    ) -> tuple[AnnotationTask, ...]:
        return plan_focus_annotations_use_case(
            name,
            namespace=namespace,
            objects=objects,
            limit=limit,
            missing_only=missing_only,
            sample_limit=sample_limit,
            config_path=_optional_path(config),
            knowledge_mode=knowledge,
            knowledge_document_ids=knowledge_documents,
            knowledge_workspace=knowledge_workspace,
            max_knowledge_characters=max_knowledge_characters,
            runtime=self._runtime,
        )

    def show(self, graph: str, reference: str) -> AnnotationReviewRecord:
        return show_annotation_use_case(
            graph,
            reference,
            runtime=self._runtime,
        )

    def apply(
        self,
        graph: str,
        proposal: dict[str, Any],
        *,
        source: str = "agent",
    ) -> AnnotationApplyResult:
        return apply_annotation_use_case(
            graph,
            proposal,
            source=source,
            runtime=self._runtime,
        )

    def reviews(
        self,
        graph: str,
        *,
        states: frozenset[str] | None = None,
    ) -> tuple[AnnotationReviewRecord, ...]:
        return list_annotation_reviews_use_case(
            graph,
            states=states,
            runtime=self._runtime,
        )

    def edit(
        self,
        graph: str,
        reference: str,
        patch: dict[str, Any],
        *,
        reason: str,
    ) -> AnnotationReviewResult:
        return edit_annotation_use_case(
            graph,
            reference,
            patch,
            reason=reason,
            runtime=self._runtime,
        )

    def decide(
        self,
        graph: str,
        reference: str,
        *,
        state: str,
        reason: str,
        include_fields: bool = False,
    ) -> AnnotationReviewResult:
        return decide_annotation_use_case(
            graph,
            reference,
            state=state,
            reason=reason,
            include_fields=include_fields,
            runtime=self._runtime,
        )

    def run(
        self,
        graph: str,
        *,
        provider: str,
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
        config: str | Path | None = None,
        knowledge: str = "none",
        knowledge_documents: tuple[str, ...] = (),
        knowledge_workspace: str | None = None,
        max_knowledge_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
        progress: Callable[[int, int, str, str], None] | None = None,
    ) -> AnnotationBatchResult:
        return run_annotation_batch_use_case(
            graph,
            provider_name=provider,
            namespace=namespace,
            objects=objects,
            limit=limit,
            missing_only=missing_only,
            workers=workers,
            retry=retry,
            retry_backoff=retry_backoff,
            skip_errors=skip_errors,
            max_errors=max_errors,
            model=model,
            timeout=timeout,
            sample_limit=sample_limit,
            config_path=_optional_path(config),
            knowledge_mode=knowledge,
            knowledge_document_ids=knowledge_documents,
            knowledge_workspace=knowledge_workspace,
            max_knowledge_characters=max_knowledge_characters,
            progress=progress,
            runtime=self._runtime,
        )


class KnowledgeAPI(_RuntimeAPI):
    def add(
        self,
        document_id: str,
        path: str | Path,
        *,
        scope: str,
        title: str | None = None,
        state: str = "draft",
        workspace: str | None = None,
        replace: bool = False,
    ) -> KnowledgeChangeResult:
        return add_knowledge_document_use_case(
            document_id,
            Path(path),
            scope_reference=scope,
            title=title,
            state=state,
            workspace_name=workspace,
            replace_existing=replace,
            runtime=self._runtime,
        )

    def list(self) -> tuple[KnowledgeDocument, ...]:
        return list_knowledge_documents_use_case(runtime=self._runtime)

    def load(self, document_id: str) -> KnowledgeDocument:
        return load_knowledge_document_use_case(document_id, runtime=self._runtime)

    def resolve(
        self,
        graph: str,
        object_reference: str,
        *,
        mode: str = "scoped",
        documents: tuple[str, ...] = (),
        workspace: str | None = None,
        max_characters: int = DEFAULT_MAX_KNOWLEDGE_CHARACTERS,
    ) -> KnowledgeContext:
        return resolve_knowledge_use_case(
            graph,
            object_reference,
            mode=mode,
            document_ids=documents,
            workspace_name=workspace,
            max_characters=max_characters,
            runtime=self._runtime,
        )


class RelationshipAPI(_RuntimeAPI):
    def list(self, graph: str) -> tuple[GraphEdge, ...]:
        return list_relationships_use_case(graph, runtime=self._runtime)

    def add(
        self,
        graph: str,
        *,
        source: str,
        target: str,
        reason: str,
        validated: bool = False,
    ) -> RelationshipChangeResult:
        return add_relationship_use_case(
            graph,
            from_reference=source,
            to_reference=target,
            reason=reason,
            validated=validated,
            runtime=self._runtime,
        )

    def check(
        self,
        graph: str,
        *,
        source: str,
        target: str,
        config: str | Path,
        row_limit: int = 10_000,
    ) -> RelationshipPairProfile:
        return check_relationship_use_case(
            graph,
            from_reference=source,
            to_reference=target,
            config_path=Path(config),
            row_limit=row_limit,
            runtime=self._runtime,
        )

    def discover(
        self,
        graph: str,
        *,
        object_reference: str,
        config: str | Path,
        field: str | None = None,
        max_pairs: int = 20,
        row_limit: int = 10_000,
        min_source_coverage: float = 0.85,
        min_overlap_count: int = 3,
        min_target_uniqueness: float = 0.9,
        persist: bool = True,
        focus: str | None = None,
        expand_one_hop: bool = False,
    ) -> RelationshipDiscoveryResult:
        return discover_relationships_use_case(
            graph,
            object_reference=object_reference,
            field_name=field,
            config_path=Path(config),
            max_pairs=max_pairs,
            row_limit=row_limit,
            min_source_coverage=min_source_coverage,
            min_overlap_count=min_overlap_count,
            min_target_uniqueness=min_target_uniqueness,
            persist=persist,
            focus_name=focus,
            expand_one_hop=expand_one_hop,
            runtime=self._runtime,
        )

    def decide(
        self,
        graph: str,
        edge_id: str,
        *,
        state: str,
        reason: str,
    ) -> RelationshipChangeResult:
        return decide_relationship_use_case(
            graph,
            edge_id=edge_id,
            state=state,
            reason=reason,
            runtime=self._runtime,
        )


class ModelAPI(_RuntimeAPI):
    def download(
        self,
        *,
        name: str = DEFAULT_MODEL_NAME,
        target: str | Path | None = None,
        force: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> ModelDownloadResult:
        return download_embedding_model_use_case(
            name=name,
            target=_optional_path(target),
            force=force,
            progress=progress,
        )

    def status(
        self,
        *,
        name: str = DEFAULT_MODEL_NAME,
        model_path: str | Path | None = None,
    ) -> dict[str, object]:
        return embedding_model_status_use_case(
            name=name,
            model_path=_optional_path(model_path),
        )


class IndexAPI(_RuntimeAPI):
    def build(
        self,
        graph: str,
        *,
        model_path: str | Path | None = None,
        batch_size: int = 16,
        n_threads: int | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> IndexBuildResult:
        return build_retrieval_index_use_case(
            graph,
            model_path=_optional_path(model_path),
            batch_size=batch_size,
            n_threads=n_threads,
            progress=progress,
            runtime=self._runtime,
        )

    def status(self, graph: str) -> dict[str, object]:
        return retrieval_index_status_use_case(graph, runtime=self._runtime)


def _optional_path(value: str | Path | None) -> Path | None:
    return None if value is None else Path(value)


def _resolve_workspace_scope(
    runtime: TarelRuntime,
    name: str,
    *,
    selection: ScopeSelection | None,
    systems: tuple[str, ...],
    graphs: tuple[str, ...],
    areas: tuple[str, ...],
    schemas: tuple[str, ...],
    zones: tuple[str, ...],
) -> ResolvedScope:
    systems, graphs, areas, schemas, zones = _workspace_scope_arguments(
        selection,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
    )
    return resolve_workspace_scope_use_case(
        name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
        runtime=runtime,
    )


def _workspace_scope_arguments(
    selection: ScopeSelection | None,
    *,
    systems: tuple[str, ...],
    graphs: tuple[str, ...],
    areas: tuple[str, ...],
    schemas: tuple[str, ...],
    zones: tuple[str, ...],
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
]:
    if selection is None:
        return systems, graphs, areas, schemas, zones
    if any((systems, graphs, areas, schemas, zones)):
        raise WorkspaceFailure(
            "conflicting_workspace_scope",
            "Pass either one WorkspaceScope selection or individual workspace filters.",
        )
    return (
        selection.systems,
        selection.graphs,
        selection.areas,
        selection.schemas,
        selection.zones,
    )
