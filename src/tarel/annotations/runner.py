"""Parallel provider calls with serial graph updates and checkpoints."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import replace

from tarel.annotations.apply import apply_annotation_proposal
from tarel.annotations.contracts import (
    AnnotationFailure,
    AnnotationProposalEnvelope,
    AnnotationRunResult,
    AnnotationTask,
    ObjectAnnotationProposal,
)
from tarel.graph.contracts import GraphDocument
from tarel.providers.contracts import (
    Message,
    ProviderFailure,
    StructuredProvider,
    StructuredRequest,
)


def run_annotation_batch(
    graph: GraphDocument,
    tasks: tuple[AnnotationTask, ...],
    provider: StructuredProvider,
    *,
    workers: int,
    retry: int,
    retry_backoff: float,
    skip_errors: bool,
    max_errors: int | None,
    model: str | None,
    after_annotation: Callable[[GraphDocument], None] | None = None,
    progress: Callable[[int, int, str, str], None] | None = None,
) -> tuple[GraphDocument, AnnotationRunResult]:
    if workers < 1:
        raise AnnotationFailure("invalid_batch", "workers must be at least 1.")
    if retry < 0 or retry_backoff < 0:
        raise AnnotationFailure("invalid_batch", "retry values cannot be negative.")

    current_graph = graph
    annotated = 0
    failed = 0
    futures: dict[Future[dict[str, object]], tuple[int, AnnotationTask]] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for index, task in enumerate(tasks, start=1):
            request = task.request
            if model is not None:
                request = replace(request, model=model)
            future = executor.submit(
                _generate_validated_with_retry,
                graph,
                task,
                provider,
                request,
                retry,
                retry_backoff,
            )
            futures[future] = (index, task)
            if progress:
                progress(index, len(tasks), task.target_label, "queued")

        for future in as_completed(futures):
            index, task = futures[future]
            try:
                raw = future.result()
                _reject_protected_values(raw, task.protected_values)
                envelope = AnnotationProposalEnvelope(
                    task_id=task.id,
                    target_id=task.target_id,
                    annotation=ObjectAnnotationProposal.from_dict(raw),
                    context_documents=task.context_documents,
                )
                current_graph = apply_annotation_proposal(
                    current_graph,
                    envelope,
                    source="provider",
                    provider=provider.name,
                    model=model or provider.default_model,
                    context_documents=task.context_documents,
                )
            except (AnnotationFailure, ProviderFailure) as exc:
                failed += 1
                if progress:
                    progress(index, len(tasks), task.target_label, "failed")
                if not skip_errors or (max_errors is not None and failed >= max_errors):
                    for pending in futures:
                        pending.cancel()
                    raise AnnotationFailure(
                        "batch_failed",
                        f"Annotation failed for {task.target_label}: {exc}",
                    ) from exc
                continue
            annotated += 1
            if after_annotation:
                after_annotation(current_graph)
            if progress:
                progress(index, len(tasks), task.target_label, "saved")

    return current_graph, AnnotationRunResult(
        planned=len(tasks),
        annotated=annotated,
        failed=failed,
    )


def _generate_validated_with_retry(
    graph: GraphDocument,
    task: AnnotationTask,
    provider: StructuredProvider,
    request: StructuredRequest,
    retry: int,
    retry_backoff: float,
) -> dict[str, object]:
    current_request = request
    last_error: AnnotationFailure | ProviderFailure | None = None
    for attempt in range(retry + 1):
        try:
            raw = provider.generate_structured(current_request)
        except ProviderFailure as exc:
            last_error = exc
        else:
            try:
                _validate_generated_proposal(graph, task, raw)
                return raw
            except AnnotationFailure as exc:
                last_error = exc
                current_request = _correction_request(
                    request,
                    raw,
                    exc,
                    protected_values=task.protected_values,
                )
        if attempt < retry and retry_backoff:
            time.sleep(retry_backoff * (attempt + 1))
    assert last_error is not None
    raise last_error


def _validate_generated_proposal(
    graph: GraphDocument,
    task: AnnotationTask,
    raw: dict[str, object],
) -> None:
    _reject_protected_values(raw, task.protected_values)
    envelope = AnnotationProposalEnvelope(
        task_id=task.id,
        target_id=task.target_id,
        annotation=ObjectAnnotationProposal.from_dict(raw),
        context_documents=task.context_documents,
    )
    apply_annotation_proposal(
        graph,
        envelope,
        source="provider-validation",
        context_documents=task.context_documents,
    )


def _correction_request(
    original: StructuredRequest,
    raw: dict[str, object],
    error: AnnotationFailure,
    *,
    protected_values: tuple[str, ...],
) -> StructuredRequest:
    if error.code == "sample_value_echoed":
        messages = [original.messages[0]]
        previous = _redact_protected_response(raw, protected_values)
    else:
        messages = list(original.messages)
        previous = raw
    messages.append(
        Message(
            role="assistant",
            content=json.dumps(
                previous,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
    messages.append(
        Message(
            role="user",
            content=(
                "Your previous annotation failed local validation. Correct it and return the "
                "complete structured result again. Do not omit any supplied field. "
                f"Validation code: {error.code}. Validation message: {error}"
            ),
        )
    )
    return replace(original, messages=tuple(messages))


def _redact_protected_response(value: object, protected_values: tuple[str, ...]) -> object:
    if isinstance(value, str):
        candidate = value.strip()
        if any(
            candidate == protected or (len(protected) >= 4 and protected in value)
            for protected in protected_values
        ):
            return "[redacted: repeated sample value]"
        return value
    if isinstance(value, dict):
        return {
            key: _redact_protected_response(nested, protected_values)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_redact_protected_response(nested, protected_values) for nested in value]
    return value


def _reject_protected_values(value: object, protected_values: tuple[str, ...]) -> None:
    if not protected_values:
        return
    for text in _response_strings(value):
        candidate = text.strip()
        for protected in protected_values:
            if candidate == protected or (len(protected) >= 4 and protected in text):
                raise AnnotationFailure(
                    "sample_value_echoed",
                    "Provider response repeated a protected sample value and was not persisted.",
                )


def _response_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(text for nested in value.values() for text in _response_strings(nested))
    if isinstance(value, list):
        return tuple(text for nested in value for text in _response_strings(nested))
    return ()
