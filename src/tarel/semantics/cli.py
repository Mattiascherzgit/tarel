"""Thin CLI adapter for semantic-model imports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tarel.semantics.application import (
    edit_semantic_source_use_case,
    import_semantic_use_case,
    list_semantic_imports_use_case,
    load_semantic_import_use_case,
    read_semantic_patch,
)


def add_semantic_commands(
    subcommands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    semantic = subcommands.add_parser(
        "semantic",
        help="Import and inspect external semantic-layer models.",
    )
    commands = semantic.add_subparsers(dest="semantic_command")

    import_command = commands.add_parser(
        "import",
        help="Import a semantic model and bind supported constructs to one TAREL graph.",
    )
    import_command.add_argument("name")
    import_command.add_argument("--graph", required=True)
    import_command.add_argument("--source", required=True, type=Path)
    import_command.add_argument(
        "--format",
        dest="semantic_format",
        choices=("ossie", "apache-ossie", "sml", "cube", "cube-yaml"),
        default="ossie",
    )
    import_command.add_argument("--replace", action="store_true")
    _output_format(import_command)

    list_command = commands.add_parser("list", help="List saved semantic imports.")
    list_command.add_argument("--graph")
    _output_format(list_command)

    show = commands.add_parser("show", help="Show one normalized semantic import.")
    show.add_argument("name")
    show.add_argument("--include-source", action="store_true")
    _output_format(show)

    edit = commands.add_parser(
        "edit",
        help="Overlay description or synonym corrections without changing the source snapshot.",
    )
    edit.add_argument("name")
    edit.add_argument("target_id")
    edit.add_argument("--input", required=True, help="Patch JSON file or '-' for stdin.")
    edit.add_argument("--reason", required=True)
    edit.add_argument("--revision")
    _output_format(edit)


def dispatch_semantic(args: argparse.Namespace) -> int | None:
    if args.command != "semantic":
        return None
    if args.semantic_command == "import":
        result = import_semantic_use_case(
            args.name,
            graph_name=args.graph,
            source_path=args.source,
            format_name=args.semantic_format,
            replace_existing=args.replace,
        )
        payload = {
            **result.document.summary_dict(),
            "changed": result.changed,
            "path": str(result.path),
        }
        _render(payload, output_format=args.output_format)
        return 0 if result.document.complete else 1
    if args.semantic_command == "list":
        documents = list_semantic_imports_use_case(graph_name=args.graph)
        payload = {
            "count": len(documents),
            "imports": [item.summary_dict() for item in documents],
        }
        _render(payload, output_format=args.output_format)
        return 0
    if args.semantic_command == "show":
        document = load_semantic_import_use_case(args.name)
        _render(
            document.to_dict(include_source_content=args.include_source),
            output_format=args.output_format,
        )
        return 0 if document.complete else 1
    if args.semantic_command == "edit":
        result = edit_semantic_source_use_case(
            args.name,
            args.target_id,
            read_semantic_patch(args.input),
            reason=args.reason,
            expected_revision=args.revision,
        )
        payload = {
            **result.document.summary_dict(),
            "changed": result.changed,
            "path": str(result.path),
            "target_id": args.target_id,
        }
        _render(payload, output_format=args.output_format)
        return 0
    return 0


def _output_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output",
        dest="output_format",
        choices=("text", "json"),
        default="text",
    )


def _render(payload: dict[str, object], *, output_format: str) -> None:
    if output_format == "json":
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if "imports" in payload:
        for item in payload["imports"]:
            if isinstance(item, dict):
                print(f"{item['name']}\t{item['graph_name']}\t{item['format_name']}")
        return
    if "name" in payload:
        print(f"Semantic import: {payload['name']}")
    if "graph_name" in payload:
        print(f"Graph: {payload['graph_name']}")
    if "format_name" in payload:
        print(f"Format: {payload['format_name']} {payload.get('format_version', '')}".rstrip())
    if "complete" in payload:
        print(f"Status: {'complete' if payload['complete'] else 'incomplete'}")
    if "diagnostics" in payload and isinstance(payload["diagnostics"], int):
        print(f"Diagnostics: {payload['diagnostics']}")
    if "changed" in payload:
        print(f"Changed: {'yes' if payload['changed'] else 'no'}")
    if "path" in payload:
        print(f"Path: {payload['path']}")
    if "contract_version" in payload:
        print(json.dumps(payload, indent=2, sort_keys=True))
