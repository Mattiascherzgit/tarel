"""Thin CLI adapter for entity-resolution candidates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tarel.entity_resolution.application import (
    decide_entity_resolution_candidate_use_case,
    find_entity_resolution_candidates_use_case,
    import_entity_resolution_candidate_use_case,
    list_entity_resolution_candidates_use_case,
    load_entity_resolution_candidate_use_case,
)
from tarel.entity_resolution.contracts import (
    ENTITY_RESOLUTION_MODES,
    EntityResolutionCandidate,
    EntityResolutionFailure,
)


def add_entity_resolution_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    entity = subcommands.add_parser(
        "entity",
        help="Import and retrieve auditable entity-resolution hypotheses.",
    )
    commands = entity.add_subparsers(dest="entity_command")

    import_command = commands.add_parser(
        "import",
        help="Import one sanitized, graph-bound candidate JSON document.",
    )
    import_command.add_argument("--source", required=True, help="JSON path or '-' for stdin.")
    _output_format(import_command)

    list_command = commands.add_parser(
        "list",
        help="List stored candidates, including rejected review history.",
    )
    list_command.add_argument("--graph")
    list_command.add_argument(
        "--state",
        action="append",
        choices=("candidate", "reviewed", "rejected"),
        dest="states",
    )
    _output_format(list_command)

    show = commands.add_parser("show", help="Show one stored candidate.")
    show.add_argument("candidate_id")
    _output_format(show)

    find = commands.add_parser(
        "find",
        help="Offer current confirmed rules or clearly labelled hypotheses.",
    )
    find.add_argument("graph")
    find.add_argument("--source-field")
    find.add_argument("--target-field")
    find.add_argument(
        "--mode",
        choices=tuple(sorted(ENTITY_RESOLUTION_MODES)),
        default="confirmed_then_candidates",
    )
    _output_format(find)

    review = commands.add_parser(
        "review",
        help="Record one explicit human approval or rejection.",
    )
    review.add_argument("candidate_id")
    review.add_argument("--decision", choices=("approve", "reject"), required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--revision")
    _output_format(review)


def dispatch_entity_resolution(args: argparse.Namespace) -> int | None:
    if args.command != "entity":
        return None
    if args.entity_command == "import":
        candidate = EntityResolutionCandidate.from_dict(_read_json(args.source))
        result = import_entity_resolution_candidate_use_case(candidate)
        _render(
            {
                "candidate": result.candidate.to_dict(),
                "changed": result.changed,
                "path": str(result.path),
            },
            output_format=args.output_format,
        )
        return 0
    if args.entity_command == "list":
        candidates = list_entity_resolution_candidates_use_case(graph_name=args.graph)
        if args.states:
            states = frozenset(args.states)
            candidates = tuple(item for item in candidates if item.state in states)
        _render(
            {
                "candidates": [item.to_dict() for item in candidates],
                "count": len(candidates),
            },
            output_format=args.output_format,
        )
        return 0
    if args.entity_command == "show":
        candidate = load_entity_resolution_candidate_use_case(args.candidate_id)
        _render(candidate.to_dict(), output_format=args.output_format)
        return 0
    if args.entity_command == "find":
        matches = find_entity_resolution_candidates_use_case(
            args.graph,
            source=args.source_field,
            target=args.target_field,
            mode=args.mode,
        )
        _render(
            {
                "count": len(matches),
                "graph": args.graph,
                "matches": [item.to_dict() for item in matches],
                "mode": args.mode,
            },
            output_format=args.output_format,
        )
        return 0
    if args.entity_command == "review":
        result = decide_entity_resolution_candidate_use_case(
            args.candidate_id,
            decision=args.decision,
            reason=args.reason,
            expected_revision=args.revision,
        )
        _render(
            {"candidate": result.candidate.to_dict(), "path": str(result.path)},
            output_format=args.output_format,
        )
        return 0
    return 0


def _read_json(path_value: str) -> dict[str, object]:
    try:
        raw = sys.stdin.read() if path_value == "-" else Path(path_value).read_text("utf-8")
        payload = json.loads(raw)
    except FileNotFoundError as exc:
        raise EntityResolutionFailure(
            "entity_resolution_source_not_found",
            f"Entity-resolution source not found: {path_value}",
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "Could not read entity-resolution candidate JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise EntityResolutionFailure(
            "invalid_entity_resolution",
            "Entity-resolution candidate JSON root must be an object.",
        )
    return payload


def _output_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=("text", "json"),
        default="text",
    )


def _render(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return
    matches = payload.get("matches")
    if isinstance(matches, list):
        print(f"Entity-resolution matches: {len(matches)}")
        for item in matches:
            if not isinstance(item, dict):
                continue
            candidate = item.get("candidate")
            if not isinstance(candidate, dict):
                continue
            evidence = candidate.get("evidence")
            if not isinstance(evidence, dict):
                continue
            print(
                f"- {item.get('source')} -> {item.get('target')} "
                f"[{candidate.get('state')}; {item.get('usage')}]"
            )
            print(
                f"  Evidence: {evidence.get('level')}; "
                f"evaluated={evidence.get('evaluated_count')}; "
                f"coverage={float(evidence.get('coverage', 0)):.1%}; "
                f"collisions={float(evidence.get('collision_rate', 0)):.1%}; "
                f"confidence={float(evidence.get('confidence', 0)):.1%}"
            )
        return
    candidates = payload.get("candidates")
    if isinstance(candidates, list):
        print(f"Entity-resolution candidates: {len(candidates)}")
        for item in candidates:
            if isinstance(item, dict):
                print(f"- {item.get('id')} [{item.get('state')}]")
        return
    candidate = payload.get("candidate")
    if isinstance(candidate, dict):
        print(f"Entity-resolution candidate: {candidate.get('id')}")
        print(f"State: {candidate.get('state')}")
        if "changed" in payload:
            print(f"Changed: {'yes' if payload['changed'] else 'no'}")
        if "path" in payload:
            print(f"Path: {payload['path']}")
        return
    if "contract_version" in payload:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
