"""Provider-neutral coding-agent tasks for write-centred lineage extraction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from tarel.lineage.contracts import LineageDocument, LineageFailure
from tarel.lineage.coverage import write_markers
from tarel.lineage.source import LineageInput, SourceDefinition
from tarel.providers.contracts import Message, StructuredRequest

_TASK_VERSION = "tarel.lineage-task.v0.2"
_ANALYZER_VERSION = "tarel.lineage-analyzer.v0.1"
_LINEAGE_MAX_OUTPUT_TOKENS = 24_000


@dataclass(frozen=True, slots=True)
class LineageTask:
    id: str
    lineage_name: str
    definition_id: str
    definition_name: str
    request: StructuredRequest

    def to_dict(self) -> dict[str, object]:
        return {
            "definition_id": self.definition_id,
            "definition_name": self.definition_name,
            "id": self.id,
            "lineage_name": self.lineage_name,
            "messages": [item.to_dict() for item in self.request.messages],
            "response_schema": self.request.schema,
            "schema_name": self.request.schema_name,
            "submission_template": {
                "analysis": "<response matching response_schema>",
                "definition_id": self.definition_id,
                "task_id": self.id,
            },
        }


def plan_lineage_tasks(
    document: LineageDocument,
    source: LineageInput,
) -> tuple[LineageTask, ...]:
    require_current_source(document, source)
    analyzed = {item.definition_id: item.definition_revision for item in document.analyses}
    by_external = source.definition_by_external_id()
    ordered: dict[str, SourceDefinition] = {}
    for step in source.steps:
        definition = by_external[step.definition_external_id]
        ordered.setdefault(definition.id, definition)
    for definition in sorted(
        source.definitions,
        key=lambda item: (item.qualified_name.casefold(), item.id),
    ):
        ordered.setdefault(definition.id, definition)
    return tuple(
        lineage_task(document, definition)
        for definition in ordered.values()
        if analyzed.get(definition.id) != definition.revision
    )


def lineage_task(
    document: LineageDocument,
    definition: SourceDefinition,
) -> LineageTask:
    numbered = "\n".join(
        f"{number:04d}: {line}" for number, line in enumerate(definition.content.splitlines(), 1)
    )
    coverage = (
        "\n".join(
            f"- {item.operation}@{item.line}: {item.text}"
            for item in write_markers(definition.content)
        )
        or "- none"
    )
    schema = lineage_analysis_schema()
    messages = (
        Message(
            "system",
            "Read the complete supplied procedure before answering. Analyse every persistent "
            "INSERT, UPDATE, DELETE, MERGE, TRUNCATE, and SELECT INTO as a separate write unit. "
            "For each write, trace only the physical source objects that feed that exact write. "
            "Follow temporary tables and CTEs backwards and record their names in via, but do "
            "not use temporary objects as final physical sources. Persistent audit and control "
            "writes are still write units. Put local intermediate writes, unresolved writes, "
            "or dynamic SQL in excluded_writes with a reason. Every potential write listed by "
            "the coverage guard must appear exactly once in writes or excluded_writes. Put "
            "procedure calls and persistent physical-object reads unrelated to a modeled write "
            "in observations; omit result SELECTs that only read local intermediates. Examine "
            "every physical table in FROM, JOIN, APPLY, EXISTS, and NOT EXISTS and include it in "
            "each write it actually influences. Use business_data for copied facts or attributes, "
            "lookup for enrichment, filter for eligibility checks, deduplication for target "
            "existence checks, control for load state, and audit for logging inputs. Every "
            "Start every write-unit and excluded-write line_start exactly on its listed "
            "coverage-marker line. Every target and source requires exact line evidence. Never "
            "invent qualifiers, runtime "
            "behavior, transitive dependencies, or column lineage. For excluded writes, target "
            "must still be the exact token present at the coverage line, such as #Stage or an "
            "update alias such as sl. Never use none, unknown, n/a, or another placeholder as a "
            "target.",
        ),
        Message(
            "user",
            f"LINEAGE: {document.name}\n"
            f"DEFINITION: {definition.qualified_name}\n"
            f"LANGUAGE: {definition.language}\n"
            f"SOURCE REFERENCE: {definition.source_reference}\n\n"
            f"POTENTIAL WRITE STATEMENTS (coverage only, not lineage):\n{coverage}\n\n"
            f"SOURCE:\n{numbered}",
        ),
    )
    request = StructuredRequest(
        messages=messages,
        schema_name="TarelLineageAnalysis",
        schema=schema,
        max_output_tokens=_LINEAGE_MAX_OUTPUT_TOKENS,
        reasoning_effort="high",
    )
    context = {
        "definition_id": definition.id,
        "definition_revision": definition.revision,
        "lineage": document.name,
        "messages": [item.to_dict() for item in messages],
        "schema": schema,
        "version": _TASK_VERSION,
    }
    raw = json.dumps(context, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    task_id = f"lineage-task:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"
    return LineageTask(
        task_id,
        document.name,
        definition.id,
        definition.qualified_name,
        request,
    )


def lineage_analysis_schema() -> dict[str, object]:
    evidence = {
        "additionalProperties": False,
        "properties": {
            "line_end": {"minimum": 1, "type": "integer"},
            "line_start": {"minimum": 1, "type": "integer"},
            "reason": {"minLength": 1, "type": "string"},
            "target": {"minLength": 1, "type": "string"},
        },
        "required": ["line_end", "line_start", "reason", "target"],
        "type": "object",
    }
    observation = {
        **evidence,
        "properties": {
            **evidence["properties"],
            "operation": {"enum": ["call", "read"], "type": "string"},
        },
        "required": [*evidence["required"], "operation"],
    }
    source = {
        **evidence,
        "properties": {
            **evidence["properties"],
            "role": {
                "enum": [
                    "audit",
                    "business_data",
                    "control",
                    "deduplication",
                    "filter",
                    "lookup",
                    "unknown",
                ],
                "type": "string",
            },
            "via": {"items": {"minLength": 1, "type": "string"}, "type": "array"},
        },
        "required": [*evidence["required"], "role", "via"],
    }
    write = {
        **evidence,
        "properties": {
            **evidence["properties"],
            "operation": {
                "enum": ["delete", "insert", "merge", "select_into", "truncate", "update"],
                "type": "string",
            },
            "sources": {"items": source, "type": "array"},
            "warnings": {"items": {"type": "string"}, "type": "array"},
        },
        "required": [*evidence["required"], "operation", "sources", "warnings"],
    }
    excluded = {
        **evidence,
        "properties": {
            **evidence["properties"],
            "disposition": {
                "enum": ["dynamic_sql", "local_intermediate", "unresolved"],
                "type": "string",
            },
            "operation": {
                "enum": ["delete", "insert", "merge", "select_into", "truncate", "update"],
                "type": "string",
            },
        },
        "required": [*evidence["required"], "disposition", "operation"],
    }
    return {
        "additionalProperties": False,
        "properties": {
            "excluded_writes": {"items": excluded, "type": "array"},
            "observations": {"items": observation, "type": "array"},
            "summary": {"minLength": 1, "type": "string"},
            "warnings": {"items": {"type": "string"}, "type": "array"},
            "writes": {"items": write, "type": "array"},
        },
        "required": ["excluded_writes", "observations", "summary", "warnings", "writes"],
        "type": "object",
    }


def lineage_analyzer_version() -> str:
    """Version the prompt, schema, and deterministic validation used by the cache."""
    return _ANALYZER_VERSION


def require_current_source(document: LineageDocument, source: LineageInput) -> None:
    if document.source_revision != source.revision:
        raise LineageFailure(
            "lineage_source_changed",
            "Lineage input changed. Run `tarel lineage build` with the current source first.",
        )
