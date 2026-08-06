"""Lineage use cases shared by the CLI and a future SDK surface."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from tarel.lineage.contracts import LineageDocument, LineageFailure
from tarel.lineage.core import (
    ProcessStep,
    TableLineage,
    apply_lineage_proposal,
    build_lineage,
    process_view,
    table_lineage,
)
from tarel.lineage.review import LineageReviewItem, decide_lineage_item, list_lineage_items
from tarel.lineage.source import LineageInput, load_lineage_input
from tarel.lineage.store import FileLineageStore
from tarel.lineage.tasks import LineageTask, plan_lineage_tasks
from tarel.providers.contracts import Message, StructuredProvider, StructuredRequest
from tarel.providers.host import load_provider


@dataclass(frozen=True, slots=True)
class LineageChangeResult:
    document: LineageDocument
    path: Path


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


def build_lineage_use_case(name: str, *, source_path: Path) -> LineageChangeResult:
    store = FileLineageStore()
    source = load_lineage_input(source_path)
    if store.exists(name):
        document = store.load(name)
        if document.source_revision != source.revision:
            raise LineageFailure(
                "lineage_source_changed",
                "Lineage input changed. Refresh semantics are not implemented yet.",
            )
        return LineageChangeResult(document, store.path(name))
    document = build_lineage(name, source)
    return LineageChangeResult(document, store.save(document))


def load_lineage_use_case(name: str) -> LineageDocument:
    return FileLineageStore().load(name)


def next_lineage_task_use_case(
    name: str,
    *,
    source_path: Path,
) -> LineageTask | None:
    document = FileLineageStore().load(name)
    source = load_lineage_input(source_path)
    tasks = plan_lineage_tasks(document, source)
    return tasks[0] if tasks else None


def apply_lineage_proposal_use_case(
    name: str,
    *,
    source_path: Path,
    payload: dict[str, Any],
) -> LineageChangeResult:
    store = FileLineageStore()
    document = store.load(name)
    source = load_lineage_input(source_path)
    updated = apply_lineage_proposal(document, source, payload)
    return LineageChangeResult(updated, store.save(updated))


def list_lineage_items_use_case(
    name: str,
    *,
    states: frozenset[str] | None = None,
) -> tuple[LineageReviewItem, ...]:
    return list_lineage_items(FileLineageStore().load(name), states=states)


def decide_lineage_item_use_case(
    name: str,
    claim_id: str,
    *,
    decision: str,
    reason: str,
) -> LineageReviewResult:
    store = FileLineageStore()
    document, item = decide_lineage_item(
        store.load(name),
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
    review_passes: int = 1,
    max_output_tokens: int | None = None,
    reasoning_effort: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> LineageProviderRunResult:
    if (
        retry < 0
        or review_passes < 0
        or (limit is not None and limit < 1)
        or (max_output_tokens is not None and max_output_tokens < 1)
    ):
        raise LineageFailure("invalid_lineage_run", "Retry and limit values are invalid.")
    store = FileLineageStore()
    document = store.load(name)
    source = load_lineage_input(source_path)
    tasks = plan_lineage_tasks(document, source)
    selected = tasks[:limit] if limit is not None else tasks
    provider = load_provider(provider_name, timeout=timeout)
    applied = 0
    path = store.path(name)
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
        _report(progress, "  extraction pass")
        response, candidate = _generate_valid_workfile(
            document,
            source,
            task,
            provider,
            request,
            retry=retry,
        )
        for review_number in range(1, review_passes + 1):
            _report(progress, f"  audit pass {review_number}/{review_passes}")
            audit = replace(
                request,
                messages=(
                    *task.request.messages,
                    Message(
                        "user",
                        "AUDIT PASS: Re-read the complete source and inspect the draft workfile "
                        "below. Return a corrected complete workfile. For every persistent write, "
                        "check every FROM, JOIN, APPLY, EXISTS, and NOT EXISTS source, trace "
                        "temporary intermediates backwards, verify source roles, remove non-object "
                        "observations, and account for every coverage marker.\n\nDRAFT WORKFILE:\n"
                        + json.dumps(response, ensure_ascii=False, sort_keys=True),
                    ),
                ),
            )
            response, candidate = _generate_valid_workfile(
                document,
                source,
                task,
                provider,
                audit,
                retry=retry,
            )
        document = candidate
        path = store.save(document)
        applied += 1
        _report(progress, "  saved")
    return LineageProviderRunResult(
        document=document,
        path=path,
        provider=provider.name,
        model=model or provider.default_model,
        planned=len(selected),
        applied=applied,
    )


def _generate_valid_workfile(
    document: LineageDocument,
    source: LineageInput,
    task: LineageTask,
    provider: StructuredProvider,
    request: StructuredRequest,
    *,
    retry: int,
) -> tuple[dict[str, object], LineageDocument]:
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
        return response, candidate
    raise AssertionError("unreachable provider retry loop")


def _report(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        progress(message)


def process_lineage_view_use_case(name: str) -> tuple[ProcessStep, ...]:
    return process_view(FileLineageStore().load(name))


def table_lineage_view_use_case(name: str) -> tuple[TableLineage, ...]:
    return table_lineage(FileLineageStore().load(name))
