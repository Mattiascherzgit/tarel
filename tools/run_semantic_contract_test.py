"""Run the experimental semantic-import contract against three local real fixtures."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tarel.runtime import TarelRuntime
from tarel.semantics.application import import_semantic_use_case
from tarel.semantics.contracts import SemanticImportDocument
from tarel.ui.presentation import browser_graph


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--examples", required=True, type=Path)
    parser.add_argument("--state-root", default=Path(".tarel"), type=Path)
    parser.add_argument("--graph", default="tpcds-semantic-demo")
    parser.add_argument(
        "--output",
        default=Path(".tarel/test-results/semantic-contract-3/report.json"),
        type=Path,
    )
    args = parser.parse_args()

    runtime = TarelRuntime.local(args.state_root)
    fixtures = (
        (
            "contract-test-ossie",
            "ossie",
            args.examples / "01-ossie/tpcds_semantic_model.yaml",
        ),
        ("contract-test-sml", "sml", args.examples / "02-sml"),
        ("contract-test-cube", "cube", args.examples / "08-cube"),
    )
    documents = tuple(
        import_semantic_use_case(
            name,
            graph_name=args.graph,
            source_path=source,
            format_name=format_name,
            replace_existing=True,
            runtime=runtime,
        ).document
        for name, format_name, source in fixtures
    )
    graph = runtime.graph_store().load(args.graph)
    browser_payload = browser_graph(graph, semantic_imports=documents, editable=True)
    browser_text = json.dumps(browser_payload, ensure_ascii=False, sort_keys=True)
    if _contains_key(browser_payload, "snapshot"):
        raise SystemExit("Semantic snapshot metadata leaked into browser payload.")
    for document in documents:
        for content in _snapshot_contents(document):
            if content and content in browser_text:
                raise SystemExit(
                    f"Raw semantic snapshot leaked into browser payload: {document.name}"
                )

    report = {
        "browser_projection": {
            "imports": len(browser_payload["semantic_imports"]),
            "raw_snapshots_excluded": True,
            "semantic_models": len(browser_payload["semantic_models"]),
        },
        "contract_versions": sorted({item.contract_version for item in documents}),
        "formats": [_summary(item) for item in documents],
        "graph": args.graph,
        "status": "passed" if all(item.complete for item in documents) else "incomplete",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


def _summary(document: SemanticImportDocument) -> dict[str, object]:
    datasets = [item for model in document.models for item in model.datasets]
    fields = [item for dataset in datasets for item in dataset.fields]
    metrics = [item for model in document.models for item in model.metrics]
    relationships = [item for model in document.models for item in model.relationships]
    return {
        "bound_datasets": sum(item.graph_node_id is not None for item in datasets),
        "bound_fields": sum(item.graph_node_id is not None for item in fields),
        "bound_relationships": sum(
            item.graph_edge_id is not None for item in relationships
        ),
        "complete": document.complete,
        "datasets": len(datasets),
        "diagnostic_codes": sorted({item.code for item in document.diagnostics}),
        "diagnostics": len(document.diagnostics),
        "fields": len(fields),
        "format": document.format_name,
        "format_version": document.format_version,
        "metrics": len(metrics),
        "models": len(document.models),
        "name": document.name,
        "relationships": len(relationships),
        "source_media_type": document.snapshot.media_type,
        "source_sha256": document.snapshot.sha256,
    }


def _snapshot_contents(document: SemanticImportDocument) -> tuple[str, ...]:
    content = document.snapshot.content
    if document.snapshot.media_type != "application/vnd.tarel.semantic-source-bundle+json":
        return (content,)
    bundle = json.loads(content)
    return (content, *(item["content"] for item in bundle["files"]))


def _contains_key(value: object, needle: str) -> bool:
    if isinstance(value, dict):
        return needle in value or any(_contains_key(item, needle) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, needle) for item in value)
    return False


if __name__ == "__main__":
    main()
