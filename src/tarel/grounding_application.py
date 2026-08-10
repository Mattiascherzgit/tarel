"""Application use cases for agent-facing semantic grounding."""

from __future__ import annotations

from pathlib import Path

from tarel.application import (
    compile_context_use_case,
    compile_workspace_context_use_case,
    load_graph_use_case,
    resolve_workspace_scope_use_case,
    show_annotation_use_case,
)
from tarel.context import ContextResult
from tarel.context_output import DEFAULT_MAX_CONTEXT_CHARACTERS
from tarel.graph.contracts import GraphDocument
from tarel.graph.revision import graph_revision
from tarel.grounding import GroundingAsset, GroundingBundle, LineageTarget, SourceTarget
from tarel.lineage.application import (
    find_lineage_references_use_case,
    load_lineage_use_case,
    trace_upstream_use_case,
)
from tarel.lineage.contracts import LineageFailure
from tarel.lineage.revision import lineage_revision
from tarel.runtime import TarelRuntime
from tarel.sources.contracts import SourceFailure, SourceProfile
from tarel.sources.store import FileSourceStore
from tarel.workspaces.projection import scoped_node_id


def describe_grounding_asset_use_case(
    graph_name: str,
    reference: str,
    *,
    source_name: str | None = None,
    runtime: TarelRuntime | None = None,
) -> GroundingAsset:
    """Resolve an asset exactly; fuzzy discovery remains a separate operation."""
    graph = load_graph_use_case(graph_name, runtime=runtime)
    source = _source_for_graph(
        graph,
        source_names=(source_name,) if source_name is not None else (),
        explicit=source_name is not None,
        runtime=runtime,
    )
    record = show_annotation_use_case(graph_name, reference, runtime=runtime)
    fields = (
        tuple(
            sorted(
                (
                    node
                    for node in graph.nodes
                    if node.type == "field" and node.metadata.get("object_id") == record.node.id
                ),
                key=lambda item: (int(item.metadata.get("position") or 0), item.id),
            )
        )
        if record.node.type in {"table", "view"}
        else ()
    )
    return GroundingAsset(
        reference=record.reference,
        node=record.node,
        fields=fields,
        source=SourceTarget(
            graph=graph.name,
            revision=graph_revision(graph),
            connector=graph.connector,
            source_type=graph.source_type,
            catalog=graph.catalog,
            dialect=graph.dialect,
            object_ids=(
                str(record.node.metadata["object_id"])
                if record.node.type == "field"
                else record.node.id,
            ),
            source=source.name if source else None,
            source_revision=source.revision if source else None,
        ),
    )


def compile_graph_grounding_use_case(
    name: str,
    query: str,
    *,
    namespace: str | None = None,
    lineage_names: tuple[str, ...] = (),
    source_names: tuple[str, ...] = (),
    trace_reference: str | None = None,
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
    model_path: Path | None = None,
    n_threads: int | None = None,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
    runtime: TarelRuntime | None = None,
) -> GroundingBundle:
    lineage_names = _lineage_names(lineage_names)
    source_names = _source_names(source_names)
    context = compile_context_use_case(
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
        model_path=model_path,
        n_threads=n_threads,
        annotation_states=annotation_states,
        validated_only=validated_only,
        runtime=runtime,
    )
    graph = load_graph_use_case(name, runtime=runtime)
    return _bundle(
        context,
        graphs=(graph,),
        source_names=source_names,
        lineage_names=lineage_names,
        trace_reference=trace_reference,
        lineage_limit=lineage_limit,
        lineage_mode=lineage_mode,
        lineage_states=lineage_states,
        max_trace_hops=max_trace_hops,
        model_path=model_path,
        n_threads=n_threads,
        runtime=runtime,
    )


def compile_workspace_grounding_use_case(
    name: str,
    query: str,
    *,
    systems: tuple[str, ...] = (),
    graphs: tuple[str, ...] = (),
    areas: tuple[str, ...] = (),
    schemas: tuple[str, ...] = (),
    zones: tuple[str, ...] = (),
    lineage_names: tuple[str, ...] = (),
    source_names: tuple[str, ...] = (),
    trace_reference: str | None = None,
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
    model_path: Path | None = None,
    n_threads: int | None = None,
    annotation_states: frozenset[str] | None = None,
    validated_only: bool = False,
    runtime: TarelRuntime | None = None,
) -> GroundingBundle:
    lineage_names = _lineage_names(lineage_names)
    source_names = _source_names(source_names)
    scope = resolve_workspace_scope_use_case(
        name,
        systems=systems,
        graphs=graphs,
        areas=areas,
        schemas=schemas,
        zones=zones,
        runtime=runtime,
    )
    loaded_graphs = tuple(load_graph_use_case(item, runtime=runtime) for item in scope.graph_names)
    context = compile_workspace_context_use_case(
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
        model_path=model_path,
        n_threads=n_threads,
        annotation_states=annotation_states,
        validated_only=validated_only,
        runtime=runtime,
    )
    return _bundle(
        context,
        graphs=loaded_graphs,
        source_names=source_names,
        lineage_names=lineage_names,
        trace_reference=trace_reference,
        lineage_limit=lineage_limit,
        lineage_mode=lineage_mode,
        lineage_states=lineage_states,
        max_trace_hops=max_trace_hops,
        model_path=model_path,
        n_threads=n_threads,
        runtime=runtime,
    )


def _bundle(
    context: ContextResult,
    *,
    graphs: tuple[GraphDocument, ...],
    source_names: tuple[str, ...],
    lineage_names: tuple[str, ...],
    trace_reference: str | None,
    lineage_limit: int,
    lineage_mode: str,
    lineage_states: frozenset[str] | None,
    max_trace_hops: int,
    model_path: Path | None,
    n_threads: int | None,
    runtime: TarelRuntime | None,
) -> GroundingBundle:
    if not 0 <= lineage_limit <= 100:
        raise LineageFailure(
            "invalid_lineage_limit",
            "Grounding lineage limit must be between 0 and 100.",
        )
    if trace_reference is not None and not lineage_names:
        raise LineageFailure(
            "missing_lineage_scope",
            "An upstream trace requires at least one explicit lineage document.",
        )
    documents = tuple(load_lineage_use_case(name, runtime=runtime) for name in lineage_names)
    graph_names = tuple(graph.name for graph in graphs)
    matches = (
        find_lineage_references_use_case(
            context.query,
            lineage_names=lineage_names,
            graph_names=graph_names,
            limit=lineage_limit,
            mode=lineage_mode,
            model_path=model_path,
            n_threads=n_threads,
            runtime=runtime,
        )
        if lineage_names and lineage_limit
        else ()
    )
    trace = (
        trace_upstream_use_case(
            trace_reference,
            lineage_names=lineage_names,
            graph_names=graph_names,
            max_hops=max_trace_hops,
            states=lineage_states,
            runtime=runtime,
        )
        if trace_reference is not None
        else None
    )
    return GroundingBundle(
        context=context,
        sources=_source_targets(
            context,
            graphs,
            source_names=source_names,
            runtime=runtime,
        ),
        lineages=tuple(
            LineageTarget(
                name=document.name,
                revision=lineage_revision(document),
                source_kind=document.source_kind,
                source_name=document.source_name,
            )
            for document in documents
        ),
        lineage_matches=matches,
        trace=trace,
    )


def _source_targets(
    context: ContextResult,
    graphs: tuple[GraphDocument, ...],
    *,
    source_names: tuple[str, ...],
    runtime: TarelRuntime | None,
) -> tuple[SourceTarget, ...]:
    workspace = context.scope.workspace is not None
    object_ids = tuple(item.id for item in context.objects)
    result = []
    for graph in sorted(graphs, key=lambda candidate: candidate.name):
        if workspace:
            prefix = scoped_node_id(graph.name, "")
            selected = tuple(sorted(item for item in object_ids if item.startswith(prefix)))
        else:
            selected = tuple(sorted(object_ids))
        if not selected:
            continue
        source = _source_for_graph(
            graph,
            source_names=source_names,
            explicit=bool(source_names),
            runtime=runtime,
        )
        result.append(
            SourceTarget(
                graph=graph.name,
                revision=graph_revision(graph),
                connector=graph.connector,
                source_type=graph.source_type,
                catalog=graph.catalog,
                dialect=graph.dialect,
                object_ids=selected,
                source=source.name if source else None,
                source_revision=source.revision if source else None,
            )
        )
    return tuple(result)


def _lineage_names(names: tuple[str, ...]) -> tuple[str, ...]:
    if len(names) != len(set(names)):
        raise LineageFailure(
            "duplicate_lineage_name",
            "Grounding lineage documents must be unique.",
        )
    return tuple(sorted(names))


def _source_names(names: tuple[str, ...]) -> tuple[str, ...]:
    if len(names) != len(set(names)):
        raise SourceFailure(
            "duplicate_source_name",
            "Grounding source profiles must be unique.",
        )
    return tuple(sorted(names))


def _source_for_graph(
    graph: GraphDocument,
    *,
    source_names: tuple[str, ...],
    explicit: bool,
    runtime: TarelRuntime | None,
) -> SourceProfile | None:
    store = runtime.source_store() if runtime is not None else FileSourceStore()
    names = source_names if explicit else store.list()
    profiles = tuple(store.load(name) for name in names)
    matches = tuple(profile for profile in profiles if graph.name in profile.graphs)
    if len(matches) > 1:
        raise SourceFailure(
            "ambiguous_source_mapping",
            f"Multiple logical sources are mapped to graph {graph.name}; select one explicitly.",
        )
    if explicit and not matches:
        raise SourceFailure(
            "source_graph_not_mapped",
            f"No selected logical source is mapped to graph {graph.name}.",
        )
    if not matches:
        return None
    source = matches[0]
    if source.connector != graph.connector:
        raise SourceFailure(
            "source_graph_mismatch",
            f"Source {source.name} uses connector {source.connector}, not {graph.connector}.",
        )
    return source
