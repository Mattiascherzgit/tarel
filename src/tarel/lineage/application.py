"""Lineage use cases shared by the CLI and a future SDK surface."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.graph.revision import graph_revision
from tarel.graph.store import FileGraphStore
from tarel.lineage.analysis_cache import (
    FileLineageAnalysisCache,
    LineageAnalysisCacheIdentity,
)
from tarel.lineage.change_store import FileLineageChangeStore
from tarel.lineage.contracts import (
    LineageDefinition,
    LineageDocument,
    LineageFailure,
    LineageWriteUnit,
)
from tarel.lineage.core import (
    ProcessStep,
    TableLineage,
    apply_lineage_proposal,
    build_lineage,
    process_view,
    record_lineage_analysis_failure,
    table_lineage,
)
from tarel.lineage.manual import add_manual_hop, add_manual_job, create_manual_lineage
from tarel.lineage.refresh import LineageRefreshReport, refresh_lineage
from tarel.lineage.review import LineageReviewItem, decide_lineage_item, list_lineage_items
from tarel.lineage.revision import lineage_revision
from tarel.lineage.runtime import (
    RuntimeDependency,
    RuntimeEvent,
    RuntimeEventInput,
    RuntimeFederatedQuery,
    RuntimeFederatedQueryInput,
    RuntimeInputReference,
    RuntimeLineageDocument,
    RuntimeLineageInput,
    RuntimeLineageTrace,
    RuntimeMongoAttempt,
    RuntimeMongoAttemptInput,
    RuntimePythonAnalysis,
    RuntimePythonAnalysisInput,
    RuntimeSQLAttempt,
    RuntimeTraceCall,
    runtime_lineage_document_version,
    validate_runtime_lineage_input,
)
from tarel.lineage.runtime_store import FileRuntimeLineageStore
from tarel.lineage.source import LineageInput, load_lineage_input
from tarel.lineage.status import LineageStatus, lineage_status
from tarel.lineage.store import FileLineageStore
from tarel.lineage.tasks import LineageTask, lineage_analyzer_version, plan_lineage_tasks
from tarel.lineage.traversal import (
    LineageReference,
    UpstreamTrace,
    find_lineage_references,
    trace_upstream,
)
from tarel.providers.contracts import (
    Message,
    ProviderFailure,
    StructuredProvider,
    StructuredRequest,
)
from tarel.providers.host import load_provider
from tarel.retrieval.local import LlamaCppEmbedding, resolve_model_path
from tarel.runtime import TarelRuntime


@dataclass(frozen=True, slots=True)
class LineageChangeResult:
    document: LineageDocument
    path: Path
    report: LineageRefreshReport | None = None
    report_path: Path | None = None


@dataclass(frozen=True, slots=True)
class LineageReviewResult:
    document: LineageDocument
    item: LineageReviewItem
    path: Path


@dataclass(frozen=True, slots=True)
class LineageProviderRunResult:
    document: LineageDocument
    path: Path
    provider: str
    model: str | None
    planned: int
    applied: int
    cache_hits: int
    provider_requests: int


@dataclass(frozen=True, slots=True)
class ManualJobResult:
    document: LineageDocument
    definition: LineageDefinition
    path: Path


@dataclass(frozen=True, slots=True)
class ManualHopResult:
    document: LineageDocument
    item: LineageWriteUnit
    path: Path


@dataclass(frozen=True, slots=True)
class RuntimeLineageImportResult:
    document: RuntimeLineageDocument
    path: Path


def _graph_store(runtime: TarelRuntime | None) -> FileGraphStore:
    return FileGraphStore() if runtime is None else runtime.graph_store()


def _lineage_store(runtime: TarelRuntime | None) -> FileLineageStore:
    return FileLineageStore() if runtime is None else runtime.lineage_store()


def _lineage_change_store(runtime: TarelRuntime | None) -> FileLineageChangeStore:
    return FileLineageChangeStore() if runtime is None else runtime.lineage_change_store()


def _runtime_lineage_store(runtime: TarelRuntime | None) -> FileRuntimeLineageStore:
    return FileRuntimeLineageStore() if runtime is None else runtime.runtime_lineage_store()


def _analysis_cache(runtime: TarelRuntime | None) -> FileLineageAnalysisCache:
    return FileLineageAnalysisCache() if runtime is None else runtime.lineage_analysis_cache()


def build_lineage_use_case(
    name: str,
    *,
    source_path: Path,
    runtime: TarelRuntime | None = None,
) -> LineageChangeResult:
    store = _lineage_store(runtime)
    source = load_lineage_input(source_path)
    if store.exists(name):
        document = store.load(name)
        if document.source_revision == source.revision:
            return LineageChangeResult(document, store.path(name))
        refreshed, report = refresh_lineage(document, source)
        report_path = _lineage_change_store(runtime).save(name, report)
        return LineageChangeResult(
            refreshed,
            store.save(refreshed),
            report,
            report_path,
        )
    document = build_lineage(name, source)
    return LineageChangeResult(document, store.save(document))


def load_lineage_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> LineageDocument:
    return _lineage_store(runtime).load(name)


def list_lineages_use_case(*, runtime: TarelRuntime | None = None) -> tuple[str, ...]:
    return _lineage_store(runtime).list()


def import_runtime_lineage_use_case(
    name: str,
    observed: RuntimeLineageInput,
    *,
    runtime: TarelRuntime | None = None,
) -> RuntimeLineageImportResult:
    validate_runtime_lineage_input(observed)
    graph = _graph_store(runtime).load(observed.graph_name)
    current_revision = graph_revision(graph)
    if observed.graph_revision != current_revision:
        raise LineageFailure(
            "runtime_graph_revision_mismatch",
            "Runtime lineage graph revision does not match the persisted graph.",
        )
    events = tuple(_runtime_event(graph, event) for event in observed.events)
    document = RuntimeLineageDocument(
        name=name,
        run_id=observed.run_id,
        graph_name=graph.name,
        graph_revision=current_revision,
        events=events,
        contract_version=runtime_lineage_document_version(observed.contract_version),
    )
    return RuntimeLineageImportResult(document, _runtime_lineage_store(runtime).create(document))


def load_runtime_lineage_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> RuntimeLineageDocument:
    return _runtime_lineage_store(runtime).load(name)


def list_runtime_lineages_use_case(*, runtime: TarelRuntime | None = None) -> tuple[str, ...]:
    return _runtime_lineage_store(runtime).list()


def trace_runtime_lineage_use_case(
    name: str,
    call_id: str,
    *,
    runtime: TarelRuntime | None = None,
) -> RuntimeLineageTrace:
    document = _runtime_lineage_store(runtime).load(name)
    events = {item.call_id: item for item in document.events}
    start = events.get(call_id)
    if start is None:
        raise LineageFailure(
            "runtime_call_not_found",
            f"Runtime call not found in {name}: {call_id}",
        )
    if start.status not in {"accepted", "succeeded"}:
        raise LineageFailure(
            "runtime_call_not_evidence",
            "A failed runtime call cannot be used as an evidence trace endpoint.",
        )
    reached: set[str] = set()
    dependencies: set[tuple[str, str]] = set()

    def visit(event: RuntimeEvent) -> None:
        if event.call_id in reached:
            return
        reached.add(event.call_id)
        if isinstance(event, (RuntimeFederatedQuery, RuntimePythonAnalysis)):
            for source_call_id in event.consumes:
                dependencies.add((source_call_id, event.call_id))
                visit(events[source_call_id])

    visit(start)
    selected = tuple(sorted((events[item] for item in reached), key=lambda item: item.sequence))
    origins = {
        reference.node_id: reference
        for event in selected
        if isinstance(event, (RuntimeSQLAttempt, RuntimeMongoAttempt))
        for reference in event.inputs
    }
    return RuntimeLineageTrace(
        runtime_lineage=document.name,
        start_call_id=start.call_id,
        graph_name=document.graph_name,
        graph_revision=document.graph_revision,
        calls=tuple(
            RuntimeTraceCall(
                call_id=event.call_id,
                sequence=event.sequence,
                kind=(
                    "python_analysis"
                    if isinstance(event, RuntimePythonAnalysis)
                    else "federated_query"
                    if isinstance(event, RuntimeFederatedQuery)
                    else "mongo_query"
                    if isinstance(event, RuntimeMongoAttempt)
                    else "sql_query"
                ),
                status=event.status,
            )
            for event in selected
        ),
        dependencies=tuple(
            RuntimeDependency(source_call_id=source, target_call_id=target)
            for source, target in sorted(
                dependencies,
                key=lambda item: (
                    events[item[0]].sequence,
                    events[item[1]].sequence,
                    item,
                ),
            )
        ),
        origins=tuple(sorted(origins.values(), key=lambda item: item.reference.casefold())),
    )


def _runtime_event(graph: GraphDocument, event: RuntimeEventInput) -> RuntimeEvent:
    if isinstance(event, RuntimePythonAnalysisInput):
        return RuntimePythonAnalysis(
            sequence=event.sequence,
            call_id=event.call_id,
            status=event.status,
            code_sha256=event.code_sha256,
            consumes=event.consumes,
            executor=event.executor,
            input_frames=event.input_frames,
            analysis=event.analysis,
            result=event.result,
            error_code=event.error_code,
        )
    if isinstance(event, RuntimeFederatedQueryInput):
        return RuntimeFederatedQuery(
            sequence=event.sequence,
            call_id=event.call_id,
            status=event.status,
            statement_sha256=event.statement_sha256,
            consumes=event.consumes,
            result=event.result,
            error_code=event.error_code,
            executor=event.executor,
            input_frames=event.input_frames,
            analysis=event.analysis,
        )
    if isinstance(event, RuntimeMongoAttemptInput):
        return RuntimeMongoAttempt(
            sequence=event.sequence,
            call_id=event.call_id,
            status=event.status,
            source=event.source,
            operation=event.operation,
            request_sha256=event.request_sha256,
            inputs=tuple(
                _runtime_input_reference(graph, node_id) for node_id in event.inputs
            ),
            result=event.result,
            error_code=event.error_code,
        )
    return RuntimeSQLAttempt(
        sequence=event.sequence,
        call_id=event.call_id,
        status=event.status,
        source=event.source,
        dialect=event.dialect,
        statement_sha256=event.statement_sha256,
        inputs=tuple(_runtime_input_reference(graph, node_id) for node_id in event.inputs),
        duration_ms=event.duration_ms,
        result=event.result,
        error_code=event.error_code,
    )


def _runtime_input_reference(graph: GraphDocument, node_id: str) -> RuntimeInputReference:
    nodes = graph.node_by_id()
    node = nodes.get(node_id)
    if node is None or node.type not in {"field", "table", "view"}:
        raise LineageFailure(
            "runtime_input_not_found",
            f"Runtime input is not a table, view, or field in graph {graph.name}: {node_id}",
        )
    return RuntimeInputReference(
        node_id=node.id,
        reference=_runtime_graph_reference(graph, node, nodes),
        kind=node.type,
    )


def _runtime_graph_reference(
    graph: GraphDocument,
    node: GraphNode,
    nodes: dict[str, GraphNode],
) -> str:
    if node.type != "field":
        return f"{graph.catalog}.{node.label}"
    parent_id = node.metadata.get("object_id")
    parent = nodes.get(parent_id) if isinstance(parent_id, str) else None
    if parent is None or parent.type not in {"table", "view"}:
        raise LineageFailure(
            "invalid_runtime_lineage_graph",
            f"Runtime field input has no table or view parent: {node.id}",
        )
    return f"{graph.catalog}.{parent.label}.{node.label}"


def add_manual_job_use_case(
    name: str,
    *,
    kind: str,
    job_name: str,
    qualified_name: str,
    language: str,
    source_reference: str,
    description: str,
    expected_revision: str | None = None,
    runtime: TarelRuntime | None = None,
) -> ManualJobResult:
    store = _lineage_store(runtime)
    document = store.load(name) if store.exists(name) else create_manual_lineage(name)
    _require_expected_revision(document, expected_revision)
    updated, definition = add_manual_job(
        document,
        kind=kind,
        name=job_name,
        qualified_name=qualified_name,
        language=language,
        source_reference=source_reference,
        description=description,
    )
    return ManualJobResult(updated, definition, store.save(updated))


def add_manual_hop_use_case(
    name: str,
    *,
    job_reference: str,
    source: str,
    target: str,
    operation: str,
    role: str,
    evidence_reference: str,
    reason: str,
    line_start: int = 1,
    line_end: int = 1,
    expected_revision: str | None = None,
    runtime: TarelRuntime | None = None,
) -> ManualHopResult:
    store = _lineage_store(runtime)
    document = store.load(name)
    _require_expected_revision(document, expected_revision)
    updated, item = add_manual_hop(
        document,
        job_reference=job_reference,
        source=source,
        target=target,
        operation=operation,
        role=role,
        evidence_reference=evidence_reference,
        reason=reason,
        line_start=line_start,
        line_end=line_end,
    )
    return ManualHopResult(updated, item, store.save(updated))


def next_lineage_task_use_case(
    name: str,
    *,
    source_path: Path,
    runtime: TarelRuntime | None = None,
) -> LineageTask | None:
    document = _lineage_store(runtime).load(name)
    source = load_lineage_input(source_path)
    tasks = plan_lineage_tasks(document, source)
    return tasks[0] if tasks else None


def apply_lineage_proposal_use_case(
    name: str,
    *,
    source_path: Path,
    payload: dict[str, Any],
    runtime: TarelRuntime | None = None,
) -> LineageChangeResult:
    store = _lineage_store(runtime)
    document = store.load(name)
    source = load_lineage_input(source_path)
    updated = apply_lineage_proposal(document, source, payload)
    return LineageChangeResult(updated, store.save(updated))


def list_lineage_items_use_case(
    name: str,
    *,
    states: frozenset[str] | None = None,
    runtime: TarelRuntime | None = None,
) -> tuple[LineageReviewItem, ...]:
    document = _lineage_store(runtime).load(name)
    return list_lineage_items(document, states=states)


def decide_lineage_item_use_case(
    name: str,
    claim_id: str,
    *,
    decision: str,
    reason: str,
    expected_revision: str | None = None,
    runtime: TarelRuntime | None = None,
) -> LineageReviewResult:
    store = _lineage_store(runtime)
    current = store.load(name)
    _require_expected_revision(current, expected_revision)
    document, item = decide_lineage_item(
        current,
        claim_id,
        decision=decision,
        reason=reason,
    )
    return LineageReviewResult(document, item, store.save(document))


def run_lineage_provider_use_case(
    name: str,
    *,
    source_path: Path,
    provider_name: str,
    model: str | None = None,
    timeout: float = 180.0,
    retry: int = 1,
    limit: int | None = None,
    definition_references: tuple[str, ...] = (),
    review_passes: int = 1,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
    progress: Callable[[str], None] | None = None,
    runtime: TarelRuntime | None = None,
) -> LineageProviderRunResult:
    if (
        retry < 0
        or review_passes < 0
        or (limit is not None and limit < 1)
        or (max_output_tokens is not None and max_output_tokens < 1)
    ):
        raise LineageFailure("invalid_lineage_run", "Retry and limit values are invalid.")
    store = _lineage_store(runtime)
    document = store.load(name)
    source = load_lineage_input(source_path)
    tasks = plan_lineage_tasks(document, source)
    selected = _select_lineage_tasks(document, tasks, definition_references)
    selected = selected[:limit] if limit is not None else selected
    provider = load_provider(provider_name, timeout=timeout)
    cache = _analysis_cache(runtime)
    applied = 0
    cache_hits = 0
    provider_requests = 0
    path = store.path(name)
    definitions = source.definition_by_id()
    effective_model = model or provider.default_model
    for task_number, task in enumerate(selected, 1):
        _report(progress, f"definition {task_number}/{len(selected)}: {task.definition_name}")
        request = replace(
            task.request,
            model=model,
            max_output_tokens=(
                max_output_tokens
                if max_output_tokens is not None
                else task.request.max_output_tokens
            ),
            reasoning_effort=(
                reasoning_effort if reasoning_effort is not None else task.request.reasoning_effort
            ),
        )
        definition = definitions[task.definition_id]
        identity = LineageAnalysisCacheIdentity(
            content_hash=definition.content_hash,
            definition_kind=definition.kind,
            language=definition.language,
            analyzer_version=lineage_analyzer_version(),
            provider=provider.name,
            model=effective_model,
            review_passes=review_passes,
            max_output_tokens=request.max_output_tokens,
            reasoning_effort=request.reasoning_effort,
        )
        try:
            cached = cache.load(identity)
            if cached is not None:
                _report(progress, "  cache hit")
                candidate = _apply_cached_workfile(document, source, task, cached)
                response = cached
                cache_hits += 1
            else:
                _report(progress, "  extraction pass")
                response, candidate, requests = _generate_valid_workfile(
                    document,
                    source,
                    task,
                    provider,
                    request,
                    retry=retry,
                )
                provider_requests += requests
                for review_number in range(1, review_passes + 1):
                    _report(progress, f"  audit pass {review_number}/{review_passes}")
                    audit = replace(
                        request,
                        messages=(
                            *task.request.messages,
                            Message(
                                "user",
                                "AUDIT PASS: Re-read the complete source and inspect the draft "
                                "workfile below. Return a corrected complete workfile. For every "
                                "persistent write, check every FROM, JOIN, APPLY, EXISTS, and NOT "
                                "EXISTS source, trace temporary intermediates backwards, verify "
                                "source roles, remove non-object observations, and account for "
                                "every coverage marker.\n\nDRAFT WORKFILE:\n"
                                + json.dumps(response, ensure_ascii=False, sort_keys=True),
                            ),
                        ),
                    )
                    response, candidate, requests = _generate_valid_workfile(
                        document,
                        source,
                        task,
                        provider,
                        audit,
                        retry=retry,
                    )
                    provider_requests += requests
                cache.save(identity, response)
        except (LineageFailure, ProviderFailure) as exc:
            document = record_lineage_analysis_failure(
                document,
                task.definition_id,
                code=exc.code,
                provider=provider.name,
                model=effective_model,
            )
            path = store.save(document)
            _report(progress, f"  failed [{exc.code}]")
            raise
        document = candidate
        path = store.save(document)
        applied += 1
        _report(progress, "  saved")
    return LineageProviderRunResult(
        document=document,
        path=path,
        provider=provider.name,
        model=effective_model,
        planned=len(selected),
        applied=applied,
        cache_hits=cache_hits,
        provider_requests=provider_requests,
    )


def _select_lineage_tasks(
    document: LineageDocument,
    tasks: tuple[LineageTask, ...],
    references: tuple[str, ...],
) -> tuple[LineageTask, ...]:
    if not references:
        return tasks
    if len(references) != len(set(references)):
        raise LineageFailure(
            "duplicate_lineage_definition",
            "Lineage definition filters must not contain duplicates.",
        )
    selected_ids: set[str] = set()
    for reference in references:
        normalized = reference.casefold()
        matches = [
            item
            for item in document.definitions
            if normalized
            in {
                item.id.casefold(),
                item.external_id.casefold(),
                item.name.casefold(),
                item.qualified_name.casefold(),
            }
        ]
        if len(matches) != 1:
            code = "lineage_definition_not_found" if not matches else "ambiguous_lineage_definition"
            raise LineageFailure(code, f"Could not resolve one lineage definition: {reference}")
        selected_ids.add(matches[0].id)
    return tuple(item for item in tasks if item.definition_id in selected_ids)


def _generate_valid_workfile(
    document: LineageDocument,
    source: LineageInput,
    task: LineageTask,
    provider: StructuredProvider,
    request: StructuredRequest,
    *,
    retry: int,
) -> tuple[dict[str, object], LineageDocument, int]:
    current_request = request
    for attempt in range(retry + 1):
        response = provider.generate_structured(current_request)
        try:
            candidate = apply_lineage_proposal(
                document,
                source,
                {
                    "analysis": response,
                    "definition_id": task.definition_id,
                    "task_id": task.id,
                },
            )
        except LineageFailure as exc:
            if attempt == retry:
                raise
            current_request = replace(
                current_request,
                messages=(
                    *current_request.messages,
                    Message(
                        "user",
                        f"VALIDATION ERROR [{exc.code}]: {exc}. Return a corrected complete "
                        "analysis for the same source and account for every coverage marker.",
                    ),
                ),
            )
            continue
        return response, candidate, attempt + 1
    raise AssertionError("unreachable provider retry loop")


def _apply_cached_workfile(
    document: LineageDocument,
    source: LineageInput,
    task: LineageTask,
    analysis: dict[str, object],
) -> LineageDocument:
    try:
        return apply_lineage_proposal(
            document,
            source,
            {
                "analysis": analysis,
                "definition_id": task.definition_id,
                "task_id": task.id,
            },
        )
    except LineageFailure as exc:
        raise LineageFailure(
            "invalid_lineage_analysis_cache",
            "Cached lineage analysis no longer passes deterministic validation.",
        ) from exc


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def process_lineage_view_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> tuple[ProcessStep, ...]:
    return process_view(_lineage_store(runtime).load(name))


def table_lineage_view_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> tuple[TableLineage, ...]:
    return table_lineage(_lineage_store(runtime).load(name))


def lineage_status_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> LineageStatus:
    return lineage_status(_lineage_store(runtime).load(name))


def find_lineage_references_use_case(
    query: str,
    *,
    lineage_names: tuple[str, ...],
    graph_names: tuple[str, ...] = (),
    limit: int = 20,
    mode: str = "lexical",
    model_path: Path | None = None,
    n_threads: int | None = None,
    runtime: TarelRuntime | None = None,
) -> tuple[LineageReference, ...]:
    documents = _load_lineage_documents(lineage_names, runtime=runtime)
    graph_store = _graph_store(runtime)
    graphs = tuple(graph_store.load(name) for name in graph_names)
    embedder = None
    if mode in {"vector", "hybrid"}:
        embedder = LlamaCppEmbedding(resolve_model_path(model_path), n_threads=n_threads)
    return find_lineage_references(
        documents,
        graphs,
        query,
        limit=limit,
        mode=mode,
        embedder=embedder,
    )


def trace_upstream_use_case(
    reference: str,
    *,
    lineage_names: tuple[str, ...],
    graph_names: tuple[str, ...] = (),
    max_hops: int = 12,
    states: frozenset[str] | None = None,
    runtime: TarelRuntime | None = None,
) -> UpstreamTrace:
    documents = _load_lineage_documents(lineage_names, runtime=runtime)
    graph_store = _graph_store(runtime)
    graphs = tuple(graph_store.load(name) for name in graph_names)
    selected = states if states is not None else None
    if selected is None:
        return trace_upstream(documents, graphs, reference, max_hops=max_hops)
    return trace_upstream(documents, graphs, reference, max_hops=max_hops, states=selected)


def _require_expected_revision(
    document: LineageDocument,
    expected_revision: str | None,
) -> None:
    if expected_revision is not None and lineage_revision(document) != expected_revision:
        raise LineageFailure(
            "stale_lineage",
            "The lineage document changed after it was loaded. Reload before saving.",
        )


def _load_lineage_documents(
    names: tuple[str, ...],
    *,
    runtime: TarelRuntime | None = None,
) -> tuple[LineageDocument, ...]:
    store = _lineage_store(runtime)
    if not names:
        raise LineageFailure("lineage_not_found", "No local lineage documents are available.")
    return tuple(store.load(name) for name in names)
