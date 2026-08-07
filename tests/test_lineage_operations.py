import json
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.lineage.application import (
    build_lineage_use_case,
    run_lineage_provider_use_case,
)
from tarel.lineage.contracts import LineageDocument, LineageFailure
from tarel.lineage.core import apply_lineage_proposal, build_lineage
from tarel.lineage.refresh import LineageRefreshReport, refresh_lineage
from tarel.lineage.review import decide_lineage_item, list_lineage_items
from tarel.lineage.source import LineageInput, SourceDefinition, SourceStep
from tarel.lineage.status import lineage_status
from tarel.lineage.store import FileLineageStore
from tarel.lineage.tasks import lineage_task, plan_lineage_tasks


class LineageOperationsTests(TestCase):
    def test_refresh_preserves_reviewed_write_as_explicitly_stale_knowledge(self) -> None:
        source = _source()
        document = build_lineage("orders", source)
        document = apply_lineage_proposal(document, source, _proposal(document, source))
        unit = document.write_units[0]
        document, _ = decide_lineage_item(
            document,
            unit.id,
            decision="validate",
            reason="Checked against the full definition.",
        )
        changed_source = _source(source.definitions[0].content + "\n-- source revision two")

        refreshed, report = refresh_lineage(document, changed_source)

        self.assertEqual(refreshed.source_revision, changed_source.revision)
        self.assertEqual(refreshed.analyses, ())
        self.assertEqual(refreshed.write_units[0].state, "review_required")
        self.assertEqual(refreshed.write_units[0].reviews[-1].decision, "validate")
        self.assertEqual(report.review_required_write_units, 1)
        self.assertIn("definition_content_changed", {item.kind for item in report.changes})
        self.assertEqual(
            {item.item_type for item in report.stale_items},
            {"analysis", "write_unit"},
        )
        stale_write = next(
            item for item in report.stale_items if item.item_type == "write_unit"
        )
        self.assertEqual(stale_write.item["state"], "validated")
        self.assertEqual(stale_write.item["definition_id"], document.definitions[0].id)
        self.assertEqual(LineageRefreshReport.from_dict(report.to_dict()), report)
        self.assertEqual(len(plan_lineage_tasks(refreshed, changed_source)), 1)
        self.assertEqual(
            [item.state for item in list_lineage_items(refreshed)],
            ["review_required"],
        )
        reapplied = apply_lineage_proposal(
            refreshed,
            changed_source,
            _proposal(refreshed, changed_source),
        )
        self.assertEqual(reapplied.write_units[0].state, "draft")
        self.assertEqual(reapplied.write_units[0].reviews, ())
        self.assertEqual(len(reapplied.analyses), 1)

    def test_build_use_case_refreshes_and_persists_revision_bound_report(self) -> None:
        previous_directory = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                first_path = Path("first.json")
                second_path = Path("second.json")
                _write_source(first_path, _source())
                _write_source(
                    second_path,
                    _source(_source().definitions[0].content + "\n-- changed"),
                )
                first = build_lineage_use_case("orders", source_path=first_path)
                self.assertIsNone(first.report)
                output = _run_cli(
                    [
                        "lineage",
                        "build",
                        "orders",
                        "--source",
                        str(second_path),
                        "--format",
                        "json",
                    ]
                )
                payload = json.loads(output[1])
                report_exists = Path(payload["change_report_path"]).is_file()
                third = build_lineage_use_case("orders", source_path=second_path)
            finally:
                os.chdir(previous_directory)

        self.assertEqual(output[0], 0)
        self.assertIn("change_report", payload)
        self.assertTrue(report_exists)
        self.assertIn(
            "definition_content_changed",
            {item["kind"] for item in payload["change_report"]["changes"]},
        )
        self.assertIsNone(third.report)

    def test_refresh_archives_removed_knowledge_and_invalidates_language_changes(self) -> None:
        source = _two_definition_source()
        _, order_report = refresh_lineage(
            build_lineage("order-change", source),
            replace(source, steps=tuple(reversed(source.steps))),
        )
        self.assertIn("step_order_changed", {item.kind for item in order_report.changes})
        document = build_lineage("orders", source)
        document = apply_lineage_proposal(document, source, _proposal(document, source))
        document, _ = decide_lineage_item(
            document,
            document.write_units[0].id,
            decision="validate",
            reason="Checked before removal.",
        )
        remaining = replace(
            source,
            definitions=(source.definitions[1],),
            steps=(source.steps[1],),
        )

        refreshed, report = refresh_lineage(document, remaining)

        self.assertEqual(refreshed.write_units, ())
        self.assertEqual(report.removed_definitions, 1)
        removed = [item for item in report.stale_items if item.item_type == "write_unit"]
        self.assertEqual(len(removed), 1)
        self.assertFalse(removed[0].present)
        self.assertEqual(removed[0].previous_state, "validated")

        original = _source()
        analyzed = apply_lineage_proposal(
            build_lineage("language", original),
            original,
            _proposal(build_lineage("language", original), original),
        )
        changed_language = replace(
            original,
            definitions=(replace(original.definitions[0], language="postgresql"),),
        )
        language_refresh, language_report = refresh_lineage(analyzed, changed_language)
        self.assertEqual(language_refresh.analyses, ())
        self.assertEqual(language_refresh.write_units[0].state, "review_required")
        self.assertIn(
            "definition_language_changed",
            {item.kind for item in language_report.changes},
        )

    def test_validated_analysis_cache_is_reused_and_model_bound(self) -> None:
        source = _source()
        previous_directory = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                source_path = Path("source.json")
                _write_source(source_path, source)
                store = FileLineageStore()
                for name in ("first", "second", "third"):
                    store.save(build_lineage(name, source))
                analysis = _proposal(build_lineage("first", source), source)["analysis"]
                provider = _Provider([analysis])
                with patch("tarel.lineage.application.load_provider", return_value=provider):
                    first = run_lineage_provider_use_case(
                        "first",
                        source_path=source_path,
                        provider_name="openrouter",
                        retry=0,
                        review_passes=0,
                    )
                    second = run_lineage_provider_use_case(
                        "second",
                        source_path=source_path,
                        provider_name="openrouter",
                        retry=0,
                        review_passes=0,
                    )

                self.assertEqual(first.cache_hits, 0)
                self.assertEqual(first.provider_requests, 1)
                self.assertEqual(second.cache_hits, 1)
                self.assertEqual(second.provider_requests, 0)
                self.assertEqual(len(provider.requests), 1)
                cache_files = tuple(Path(".tarel/lineage-analysis-cache").glob("*.json"))
                self.assertEqual(len(cache_files), 1)
                self.assertNotIn("CREATE PROCEDURE", cache_files[0].read_text())

                other_model_provider = _Provider([analysis])
                with patch(
                    "tarel.lineage.application.load_provider",
                    return_value=other_model_provider,
                ):
                    third = run_lineage_provider_use_case(
                        "third",
                        source_path=source_path,
                        provider_name="openrouter",
                        model="another/model",
                        retry=0,
                        review_passes=0,
                    )
            finally:
                os.chdir(previous_directory)

        self.assertEqual(third.cache_hits, 0)
        self.assertEqual(third.provider_requests, 1)
        self.assertEqual(len(other_model_provider.requests), 1)

    def test_failed_analysis_is_persisted_and_visible_in_status(self) -> None:
        source = _source()
        previous_directory = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                source_path = Path("source.json")
                _write_source(source_path, source)
                FileLineageStore().save(build_lineage("orders", source))
                provider = _Provider(
                    [
                        {
                            "excluded_writes": [],
                            "observations": [],
                            "summary": "Incomplete.",
                            "warnings": [],
                            "writes": [],
                        }
                    ]
                )
                with (
                    patch("tarel.lineage.application.load_provider", return_value=provider),
                    self.assertRaisesRegex(LineageFailure, "did not classify"),
                ):
                    run_lineage_provider_use_case(
                        "orders",
                        source_path=source_path,
                        provider_name="openrouter",
                        retry=0,
                        review_passes=0,
                    )
                stored = FileLineageStore().load("orders")
                status = lineage_status(stored)
                output = _run_cli(
                    [
                        "lineage",
                        "show",
                        "orders",
                        "--view",
                        "status",
                        "--format",
                        "json",
                    ]
                )
                raw = FileLineageStore().path("orders").read_text()
            finally:
                os.chdir(previous_directory)

        self.assertEqual(status.analyses_failed, 1)
        self.assertEqual(status.analyses_pending, 0)
        self.assertEqual(status.definitions[0].failure_code, "incomplete_write_coverage")
        self.assertEqual(json.loads(output[1])["analysis_coverage"]["failed"], 1)
        self.assertNotIn("did not classify", raw)

    def test_version_two_documents_migrate_without_analysis_failures(self) -> None:
        payload = build_lineage("orders", _source()).to_dict()
        payload["contract_version"] = "tarel.lineage.v0.2"
        payload.pop("analysis_failures")

        migrated = LineageDocument.from_dict(payload)

        self.assertEqual(migrated.analysis_failures, ())
        self.assertEqual(migrated.contract_version, "tarel.lineage.v0.3")


def _source(content: str | None = None) -> LineageInput:
    definition = SourceDefinition(
        external_id="load-orders",
        kind="procedure",
        name="LoadOrders",
        qualified_name="etl.LoadOrders",
        language="tsql",
        content=(
            content
            or "CREATE PROCEDURE etl.LoadOrders AS\n"
            "INSERT dbo.TargetOrders SELECT * FROM dbo.SourceOrders;"
        ),
        source_reference="sqlserver:demo:etl.LoadOrders",
    )
    return LineageInput(
        source_kind="test",
        source_name="Orders demo",
        source_reference="test:orders",
        workflow_external_id="orders",
        workflow_name="Orders",
        definitions=(definition,),
        steps=(SourceStep("load-orders", "Load orders", definition.external_id, ()),),
    )


def _two_definition_source() -> LineageInput:
    source = _source()
    second = SourceDefinition(
        external_id="finalize-orders",
        kind="procedure",
        name="FinalizeOrders",
        qualified_name="etl.FinalizeOrders",
        language="tsql",
        content="CREATE PROCEDURE etl.FinalizeOrders AS\nSELECT 1;",
        source_reference="sqlserver:demo:etl.FinalizeOrders",
    )
    return replace(
        source,
        definitions=(*source.definitions, second),
        steps=(
            *source.steps,
            SourceStep("finalize-orders", "Finalize orders", second.external_id, ()),
        ),
    )


def _proposal(document: LineageDocument, source: LineageInput) -> dict[str, object]:
    definition = source.definitions[0]
    return {
        "analysis": {
            "excluded_writes": [],
            "observations": [],
            "summary": "Loads persistent orders from the physical source table.",
            "warnings": [],
            "writes": [
                {
                    "line_end": 2,
                    "line_start": 2,
                    "operation": "insert",
                    "reason": "The INSERT names the persistent target.",
                    "sources": [
                        {
                            "line_end": 2,
                            "line_start": 2,
                            "reason": "The FROM clause names the physical source.",
                            "role": "business_data",
                            "target": "dbo.SourceOrders",
                            "via": [],
                        }
                    ],
                    "target": "dbo.TargetOrders",
                    "warnings": [],
                }
            ],
        },
        "definition_id": definition.id,
        "task_id": lineage_task(document, definition).id,
    }


def _write_source(path: Path, source: LineageInput) -> None:
    definition = source.definitions[0]
    step = source.steps[0]
    payload = {
        "definitions": [
            {
                "content": definition.content,
                "external_id": definition.external_id,
                "kind": definition.kind,
                "language": definition.language,
                "name": definition.name,
                "qualified_name": definition.qualified_name,
                "source_reference": definition.source_reference,
            }
        ],
        "format_version": "tarel.lineage-input.v0.1",
        "source": {
            "kind": source.source_kind,
            "name": source.source_name,
            "reference": source.source_reference,
        },
        "workflow": {
            "external_id": source.workflow_external_id,
            "name": source.workflow_name,
            "steps": [
                {
                    "definition_id": step.definition_external_id,
                    "depends_on": list(step.depends_on_external_ids),
                    "external_id": step.external_id,
                    "name": step.name,
                }
            ],
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _run_cli(arguments: list[str]) -> tuple[int, str, str]:
    output = StringIO()
    errors = StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        exit_code = main(arguments)
    return exit_code, output.getvalue(), errors.getvalue()


class _Provider:
    name = "openrouter"
    default_model = "test/model"

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests = []

    def generate_structured(self, request):
        self.requests.append(request)
        return self.responses.pop(0)
