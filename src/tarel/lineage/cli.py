"""Thin command-line adapter for write-centred static lineage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from tarel.lineage.application import (
    LineageChangeResult,
    add_manual_hop_use_case,
    add_manual_job_use_case,
    apply_lineage_proposal_use_case,
    build_lineage_use_case,
    decide_lineage_item_use_case,
    find_lineage_references_use_case,
    lineage_status_use_case,
    list_lineage_items_use_case,
    load_lineage_use_case,
    next_lineage_task_use_case,
    process_lineage_view_use_case,
    run_lineage_provider_use_case,
    table_lineage_view_use_case,
    trace_upstream_use_case,
)
from tarel.lineage.contracts import LineageFailure
from tarel.lineage.revision import lineage_revision
from tarel.lineage.status import lineage_status


def add_lineage_commands(subcommands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    lineage = subcommands.add_parser(
        "lineage",
        help="Build and review direct process and table lineage.",
    )
    commands = lineage.add_subparsers(dest="lineage_command")

    build = commands.add_parser(
        "build",
        help="Create or refresh lineage from a canonical input file.",
    )
    build.add_argument("name")
    build.add_argument("--source", required=True, type=Path)
    _format(build)

    show = commands.add_parser(
        "show",
        help="Show the document, process, table, or coverage projection.",
    )
    show.add_argument("name")
    show.add_argument(
        "--view",
        choices=("document", "process", "status", "tables"),
        default="document",
    )
    _format(show)

    next_task = commands.add_parser(
        "next",
        help="Return the next source-analysis task for the current coding agent.",
    )
    next_task.add_argument("name")
    next_task.add_argument("--source", required=True, type=Path)

    apply = commands.add_parser("apply", help="Apply one coding-agent workfile as draft lineage.")
    apply.add_argument("name")
    apply.add_argument("--source", required=True, type=Path)
    apply.add_argument("--input", required=True, help="Proposal JSON file or '-' for stdin.")
    _format(apply)

    analyze = commands.add_parser(
        "analyze",
        help="Send complete definitions to an optional provider and apply draft workfiles.",
    )
    analyze.add_argument("name")
    analyze.add_argument("--source", required=True, type=Path)
    analyze.add_argument("--provider", required=True)
    analyze.add_argument("--model")
    analyze.add_argument("--timeout", type=float, default=180.0)
    analyze.add_argument("--retry", type=int, default=1)
    analyze.add_argument("--review-passes", type=int, default=1)
    analyze.add_argument("--limit", type=int)
    analyze.add_argument("--max-output-tokens", type=int)
    analyze.add_argument(
        "--reasoning-effort",
        choices=("none", "minimal", "low", "medium", "high", "xhigh"),
    )
    _format(analyze)

    review = commands.add_parser("review", help="List or decide draft lineage items.")
    review.add_argument("name")
    review.add_argument("item_id", nargs="?")
    review.add_argument("--decision", choices=("validate", "reject"))
    review.add_argument("--reason")
    review.add_argument(
        "--state",
        action="append",
        choices=("draft", "review_required", "validated", "rejected"),
    )
    _format(review)

    find = commands.add_parser(
        "find",
        help="Find report, semantic, procedure, table, or field references.",
    )
    find.add_argument("query")
    find.add_argument("--lineage", action="append", dest="lineages", required=True)
    find.add_argument("--graph", action="append", dest="graphs")
    find.add_argument("--limit", type=int, default=20)
    find.add_argument(
        "--mode",
        choices=("lexical", "bm25", "vector", "hybrid"),
        default="lexical",
    )
    find.add_argument("--model", type=Path, dest="model_path")
    find.add_argument("--threads", type=int, dest="n_threads")
    _format(find)

    upstream = commands.add_parser(
        "upstream",
        help="Trace one reference backwards through all selected lineage documents.",
    )
    upstream.add_argument("reference")
    upstream.add_argument("--lineage", action="append", dest="lineages", required=True)
    upstream.add_argument("--graph", action="append", dest="graphs")
    upstream.add_argument("--max-hops", type=int, default=12)
    upstream.add_argument(
        "--state",
        action="append",
        choices=("draft", "review_required", "validated"),
    )
    _format(upstream)

    add_job = commands.add_parser(
        "add-job",
        help="Add a human-authored procedure or script to a manual lineage overlay.",
    )
    add_job.add_argument("name", help="Manual lineage overlay name; created if missing.")
    add_job.add_argument("--kind", required=True, choices=("procedure", "script"))
    add_job.add_argument("--job-name", required=True)
    add_job.add_argument("--qualified-name", required=True)
    add_job.add_argument("--language", required=True)
    add_job.add_argument("--source-reference", required=True)
    add_job.add_argument("--description", required=True)
    _format(add_job)

    add_hop = commands.add_parser(
        "add-hop",
        help="Add a human-authored source-to-target hop through a manual job.",
    )
    add_hop.add_argument("name", help="Existing manual lineage overlay name.")
    add_hop.add_argument("--job", required=True, help="Job ID or qualified name.")
    add_hop.add_argument("--source", required=True)
    add_hop.add_argument("--target", required=True)
    add_hop.add_argument(
        "--operation",
        required=True,
        choices=("delete", "insert", "merge", "select_into", "truncate", "update"),
    )
    add_hop.add_argument(
        "--role",
        default="business_data",
        choices=(
            "audit",
            "business_data",
            "control",
            "deduplication",
            "filter",
            "lookup",
            "unknown",
        ),
    )
    add_hop.add_argument("--evidence-reference", required=True)
    add_hop.add_argument("--reason", required=True)
    add_hop.add_argument("--line-start", type=int, default=1)
    add_hop.add_argument("--line-end", type=int, default=1)
    _format(add_hop)


def dispatch_lineage(args: argparse.Namespace) -> int | None:
    if args.command != "lineage":
        return None
    command = args.lineage_command
    if command == "build":
        result = build_lineage_use_case(args.name, source_path=args.source)
        payload = _document_change_payload(result)
        _render_document_change(payload, output_format=args.format)
        return 0
    if command == "show":
        if args.view == "document":
            payload: object = load_lineage_use_case(args.name).to_dict()
        elif args.view == "process":
            payload = {
                "process": [item.to_dict() for item in process_lineage_view_use_case(args.name)]
            }
        elif args.view == "tables":
            payload = {
                "tables": [item.to_dict() for item in table_lineage_view_use_case(args.name)]
            }
        else:
            payload = lineage_status_use_case(args.name).to_dict()
        _render_view(payload, args.view, output_format=args.format)
        return 0
    if command == "next":
        task = next_lineage_task_use_case(args.name, source_path=args.source)
        payload = {"status": "complete"} if task is None else task.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    if command == "apply":
        result = apply_lineage_proposal_use_case(
            args.name,
            source_path=args.source,
            payload=_read_json_object(args.input),
        )
        payload = _document_change_payload(result)
        _render_document_change(payload, output_format=args.format)
        return 0
    if command == "analyze":
        print(
            f"warning: complete source definitions will be sent to provider profile "
            f"{args.provider}",
            file=sys.stderr,
        )
        result = run_lineage_provider_use_case(
            args.name,
            source_path=args.source,
            provider_name=args.provider,
            model=args.model,
            timeout=args.timeout,
            retry=args.retry,
            limit=args.limit,
            review_passes=args.review_passes,
            max_output_tokens=args.max_output_tokens,
            reasoning_effort=args.reasoning_effort,
            progress=lambda message: print(message, file=sys.stderr),
        )
        payload = {
            "applied": result.applied,
            "cache_hits": result.cache_hits,
            "lineage": result.document.name,
            "model": result.model,
            "path": str(result.path),
            "planned": result.planned,
            "provider": result.provider,
            "provider_requests": result.provider_requests,
            "write_units": len(result.document.write_units),
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"Analyzed {result.applied}/{result.planned} definitions.")
            print(f"Provider: {result.provider}; model: {result.model}")
            print(
                f"Cache hits: {result.cache_hits}; provider requests: "
                f"{result.provider_requests}"
            )
            print(f"Write units: {len(result.document.write_units)}")
            print(f"Path: {result.path}")
        return 0
    if command == "review":
        return _review(args)
    if command == "find":
        references = find_lineage_references_use_case(
            args.query,
            lineage_names=tuple(args.lineages),
            graph_names=tuple(args.graphs or ()),
            limit=args.limit,
            mode=args.mode,
            model_path=args.model_path,
            n_threads=args.n_threads,
        )
        payload = {
            "mode": args.mode,
            "query": args.query,
            "references": [item.to_dict() for item in references],
        }
        _render_lineage_find(payload, output_format=args.format)
        return 0
    if command == "upstream":
        trace = trace_upstream_use_case(
            args.reference,
            lineage_names=tuple(args.lineages),
            graph_names=tuple(args.graphs or ()),
            max_hops=args.max_hops,
            states=frozenset(args.state) if args.state else None,
        )
        _render_upstream_trace(trace.to_dict(), output_format=args.format)
        return 0
    if command == "add-job":
        result = add_manual_job_use_case(
            args.name,
            kind=args.kind,
            job_name=args.job_name,
            qualified_name=args.qualified_name,
            language=args.language,
            source_reference=args.source_reference,
            description=args.description,
        )
        _render_view(
            {
                "definition": result.definition.to_dict(),
                "lineage": result.document.name,
                "path": str(result.path),
                "revision": lineage_revision(result.document),
            },
            "manual job",
            output_format=args.format,
        )
        return 0
    if command == "add-hop":
        result = add_manual_hop_use_case(
            args.name,
            job_reference=args.job,
            source=args.source,
            target=args.target,
            operation=args.operation,
            role=args.role,
            evidence_reference=args.evidence_reference,
            reason=args.reason,
            line_start=args.line_start,
            line_end=args.line_end,
        )
        _render_view(
            {
                "item": result.item.to_dict(),
                "lineage": result.document.name,
                "path": str(result.path),
                "revision": lineage_revision(result.document),
            },
            "manual hop",
            output_format=args.format,
        )
        return 0
    return 0


def _review(args: argparse.Namespace) -> int:
    deciding = args.item_id is not None or args.decision is not None or args.reason is not None
    if deciding:
        if not args.item_id or not args.decision or not args.reason or args.state:
            raise LineageFailure(
                "invalid_lineage_review",
                "A decision requires ITEM_ID, --decision, and --reason without --state.",
            )
        result = decide_lineage_item_use_case(
            args.name,
            args.item_id,
            decision=args.decision,
            reason=args.reason,
        )
        payload: object = {"item": result.item.to_dict(), "path": str(result.path)}
    else:
        states = frozenset(args.state) if args.state else None
        payload = {
            "items": [
                item.to_dict() for item in list_lineage_items_use_case(args.name, states=states)
            ]
        }
    _render_view(payload, "review", output_format=args.format)
    return 0


def _read_json_object(location: str) -> dict[str, Any]:
    try:
        raw = sys.stdin.read() if location == "-" else Path(location).read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise LineageFailure("invalid_lineage_proposal", "Could not read proposal JSON.") from exc
    if not isinstance(payload, dict):
        raise LineageFailure("invalid_lineage_proposal", "Proposal JSON must be an object.")
    return payload


def _format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=("text", "json"), default="text")


def _document_change_payload(result: LineageChangeResult) -> dict[str, object]:
    payload = {
        "lineage": result.document.to_dict(),
        "path": str(result.path),
        "status": lineage_status(result.document).to_dict(),
    }
    if result.report is not None:
        payload["change_report"] = result.report.to_dict()
        payload["change_report_path"] = str(result.report_path)
    return payload


def _render_document_change(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    lineage = payload["lineage"]
    if isinstance(lineage, dict):
        print(f"Lineage: {lineage['name']}")
        print(f"Definitions: {len(lineage['definitions'])}")
        print(f"Steps: {len(lineage['steps'])}")
        print(f"Claims: {len(lineage['claims'])}")
        print(f"Write units: {len(lineage['write_units'])}")
    status = payload.get("status")
    if isinstance(status, dict):
        coverage = status.get("analysis_coverage")
        if isinstance(coverage, dict):
            print(
                "Analysis: "
                f"{coverage['complete']} complete, {coverage['failed']} failed, "
                f"{coverage['pending']} pending"
            )
    report = payload.get("change_report")
    if isinstance(report, dict):
        print(
            f"Refresh: {len(report['changes'])} changes; "
            f"{len(report['stale_items'])} stale items"
        )
        print(f"Change report: {payload['change_report_path']}")
    print(f"Path: {payload['path']}")


def _render_view(payload: object, view: str, *, output_format: str) -> None:
    if output_format == "json" or view == "document":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not isinstance(payload, dict):
        return
    if view == "manual job":
        definition = payload.get("definition")
        if isinstance(definition, dict):
            print(f"Added manual job: {definition['qualified_name']}")
            print(f"Lineage: {payload['lineage']}; revision: {payload['revision']}")
        return
    if view == "manual hop":
        item = payload.get("item")
        if isinstance(item, dict):
            source = item["sources"][0]["target"]
            print(f"Added manual hop: {source} -> {item['target']} [{item['state']}]")
            print(f"Lineage: {payload['lineage']}; revision: {payload['revision']}")
        return
    if view == "status":
        _render_status(payload)
        return
    rows = payload.get(view)
    if not isinstance(rows, list):
        rows = payload.get("items")
    if not isinstance(rows, list):
        item = payload.get("item")
        rows = [item] if isinstance(item, dict) else []
    for row in rows:
        if view == "process":
            dependencies = ", ".join(row["depends_on"]) or "-"
            print(f"- {row['name']}: {row['definition']} (after: {dependencies})")
        elif view == "tables":
            print(
                f"- {row['source']} -> {row['target']} via {row['via_definition']} [{row['state']}]"
            )
        else:
            print(f"- {row['id']}: {row['operation']} {row['target']} [{row['state']}]")


def _render_status(payload: dict[str, object]) -> None:
    coverage = payload.get("analysis_coverage")
    if isinstance(coverage, dict):
        print(f"Lineage: {payload['lineage']}")
        print(
            "Analysis: "
            f"{coverage['complete']} complete, {coverage['failed']} failed, "
            f"{coverage['pending']} pending / {coverage['total']} total"
        )
    definitions = payload.get("definitions")
    if not isinstance(definitions, list):
        return
    for item in definitions:
        if not isinstance(item, dict):
            continue
        claims = item["claims"]
        writes = item["write_units"]
        failure = item.get("failure")
        failure_text = (
            f" [{failure['code']}]" if isinstance(failure, dict) else ""
        )
        print(
            f"- {item['definition_name']}: {item['analysis_state']}{failure_text}; "
            f"observations={claims['total']}; writes={writes['total']} "
            f"(draft={writes['draft']}, review_required={writes['review_required']}, "
            f"validated={writes['validated']}, rejected={writes['rejected']})"
        )


def _render_lineage_find(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    references = payload.get("references")
    if not isinstance(references, list):
        return
    print(f"Mode: {payload.get('mode', 'lexical')}")
    for item in references:
        if isinstance(item, dict):
            print(f"- {item['reference']} [{item['kind']}; {item['source']}]")


def _render_upstream_trace(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    start = payload.get("start")
    if isinstance(start, dict):
        print(f"Start: {start['reference']} [{start['kind']}]")
    hops = payload.get("hops")
    if isinstance(hops, list):
        for hop in hops:
            if not isinstance(hop, dict):
                continue
            target = hop.get("target")
            if not isinstance(target, dict):
                continue
            indent = "  " * max(int(hop.get("depth", 1)) - 1, 0)
            detail = f" via {hop['via_definition']}" if hop.get("via_definition") else ""
            role = f"; role={hop['role']}" if hop.get("role") else ""
            granularity = (
                f"; granularity={hop['granularity_change']}"
                if hop.get("granularity_change")
                else ""
            )
            source = hop.get("source")
            if not isinstance(source, dict):
                continue
            print(
                f"{indent}<- {source['reference']} "
                f"({hop['relation']}{detail}; {hop['state']}{role}{granularity})"
            )
            description = source.get("description")
            description_kind = source.get("description_kind")
            annotation_state = source.get("annotation_state")
            if description:
                state = f" [{annotation_state}]" if annotation_state else ""
                label = {
                    "analysis_summary": "analysis",
                    "semantic_annotation": "semantic",
                    "technical_metadata": "technical",
                }.get(description_kind, "description")
                print(f"{indent}   {label}: {description}{state}")
            reviews = hop.get("reviews")
            if isinstance(reviews, list) and reviews:
                review = reviews[-1]
                if isinstance(review, dict):
                    print(
                        f"{indent}   review: {review['decision']} by "
                        f"{review['source']} - {review['reason']}"
                    )
    origins = payload.get("origins")
    if isinstance(origins, list) and origins:
        print(f"Origin view: {len(origins)} unique origins")
        for origin in origins:
            if isinstance(origin, dict):
                print(f"- {origin['reference']} [{origin['kind']}]")
                roles, procedures = _origin_lineage_details(origin, hops)
                if roles:
                    print(f"  roles: {', '.join(roles)}")
                if procedures:
                    print(f"  procedures: {', '.join(procedures)}")
    warnings = payload.get("warnings")
    if isinstance(warnings, list):
        for warning in warnings:
            print(f"warning: {warning}")


def _origin_lineage_details(
    origin: dict[str, object],
    hops: object,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(hops, list) or not isinstance(origin.get("id"), str):
        return (), ()
    downstream: dict[str, list[dict[str, object]]] = {}
    for hop in hops:
        if not isinstance(hop, dict):
            continue
        source = hop.get("source")
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            continue
        downstream.setdefault(source["id"], []).append(hop)

    queue = [origin["id"]]
    seen = set(queue)
    roles: set[str] = set()
    procedures: set[str] = set()
    while queue:
        source_id = queue.pop(0)
        for hop in downstream.get(source_id, []):
            role = hop.get("role")
            if source_id == origin["id"] and isinstance(role, str) and role:
                roles.add(role)
            procedure = hop.get("via_definition")
            if isinstance(procedure, str) and procedure:
                procedures.add(procedure)
            target = hop.get("target")
            if not isinstance(target, dict):
                continue
            target_id = target.get("id")
            if isinstance(target_id, str) and target_id not in seen:
                seen.add(target_id)
                queue.append(target_id)
    return tuple(sorted(roles)), tuple(sorted(procedures))
