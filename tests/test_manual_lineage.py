import json
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.lineage.application import (
    add_manual_hop_use_case,
    add_manual_job_use_case,
    decide_lineage_item_use_case,
    table_lineage_view_use_case,
    trace_upstream_use_case,
)
from tarel.lineage.contracts import LineageFailure
from tarel.lineage.manual import add_manual_job, create_manual_lineage
from tarel.lineage.revision import lineage_revision
from tarel.lineage.store import FileLineageStore


class ManualLineageTests(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.store = FileLineageStore(Path(self.temporary.name) / "lineage")
        self.stack = ExitStack()
        self.stack.enter_context(
            patch("tarel.lineage.application.FileLineageStore", return_value=self.store)
        )

    def tearDown(self) -> None:
        self.stack.close()
        self.temporary.cleanup()

    def test_job_hop_review_and_upstream_trace_share_one_application_path(self) -> None:
        job = add_manual_job_use_case(
            "sales-manual",
            kind="procedure",
            job_name="LoadFactSales",
            qualified_name="etl.LoadFactSales",
            language="tsql",
            source_reference="runbook:sales-load",
            description="Loads reviewed sales rows into the fact table.",
        )
        before_hop = lineage_revision(job.document)
        hop = add_manual_hop_use_case(
            "sales-manual",
            job_reference="etl.LoadFactSales",
            source="mart.StageSales",
            target="mart.FactSales",
            operation="insert",
            role="business_data",
            evidence_reference="runbook:sales-load",
            reason="The warehouse owner confirmed this source-to-target write.",
            line_start=12,
            line_end=18,
            expected_revision=before_hop,
        )

        self.assertEqual(hop.item.state, "draft")
        self.assertEqual(hop.item.sources[0].target, "mart.StageSales")
        table_hop = table_lineage_view_use_case("sales-manual")[0]
        self.assertEqual(
            (table_hop.source, table_hop.target),
            ("mart.StageSales", "mart.FactSales"),
        )
        self.assertEqual(table_hop.via_definition, "etl.LoadFactSales")

        trace = trace_upstream_use_case(
            "mart.FactSales",
            lineage_names=("sales-manual",),
        )
        self.assertEqual(len(trace.hops), 1)
        self.assertEqual(trace.hops[0].source.reference, "mart.StageSales")
        self.assertEqual([item.reference for item in trace.origins], ["mart.StageSales"])

        reviewed = decide_lineage_item_use_case(
            "sales-manual",
            hop.item.id,
            decision="validate",
            reason="Checked against the production runbook.",
            expected_revision=lineage_revision(hop.document),
        )
        self.assertEqual(reviewed.item.state, "validated")
        self.assertEqual(reviewed.item.reviews[-1].source, "human")

    def test_manual_overlay_rejects_duplicates_and_stale_browser_writes(self) -> None:
        first = add_manual_job_use_case(
            "manual",
            kind="script",
            job_name="publish.py",
            qualified_name="scripts.publish_sales",
            language="python",
            source_reference="repo:scripts/publish.py",
            description="Publishes the curated sales extract.",
        )
        with self.assertRaises(LineageFailure) as duplicate:
            add_manual_job_use_case(
                "manual",
                kind="script",
                job_name="publish.py",
                qualified_name="scripts.publish_sales",
                language="python",
                source_reference="repo:scripts/publish.py",
                description="Duplicate.",
            )
        self.assertEqual(duplicate.exception.code, "manual_job_exists")

        add_manual_hop_use_case(
            "manual",
            job_reference="scripts.publish_sales",
            source="mart.FactSales",
            target="exports.SalesExtract",
            operation="insert",
            role="business_data",
            evidence_reference="repo:scripts/publish.py",
            reason="Reviewed assignment in the publishing script.",
        )
        with self.assertRaises(LineageFailure) as stale:
            add_manual_hop_use_case(
                "manual",
                job_reference="scripts.publish_sales",
                source="mart.DimDate",
                target="exports.DateExtract",
                operation="insert",
                role="lookup",
                evidence_reference="repo:scripts/publish.py",
                reason="Reviewed assignment in the publishing script.",
                expected_revision=lineage_revision(first.document),
            )
        self.assertEqual(stale.exception.code, "stale_lineage")

    def test_cli_adds_a_job_and_hop_as_json(self) -> None:
        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "lineage",
                    "add-job",
                    "manual",
                    "--kind",
                    "procedure",
                    "--job-name",
                    "LoadSales",
                    "--qualified-name",
                    "etl.LoadSales",
                    "--language",
                    "tsql",
                    "--source-reference",
                    "runbook:load-sales",
                    "--description",
                    "Loads sales.",
                    "--format",
                    "json",
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout.getvalue())["definition"]["qualified_name"],
            "etl.LoadSales",
        )

        stdout = StringIO()
        with redirect_stdout(stdout):
            exit_code = main(
                [
                    "lineage",
                    "add-hop",
                    "manual",
                    "--job",
                    "etl.LoadSales",
                    "--source",
                    "stage.Sales",
                    "--target",
                    "mart.Sales",
                    "--operation",
                    "merge",
                    "--evidence-reference",
                    "runbook:load-sales",
                    "--reason",
                    "Confirmed by owner.",
                    "--format",
                    "json",
                ]
            )
        payload = json.loads(stdout.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["item"]["state"], "draft")
        self.assertEqual(payload["item"]["target"], "mart.Sales")

    def test_cli_reports_unknown_manual_job_without_a_traceback(self) -> None:
        add_manual_job_use_case(
            "manual",
            kind="script",
            job_name="existing",
            qualified_name="scripts.existing",
            language="python",
            source_reference="repo:existing.py",
            description="Existing script.",
        )
        errors = StringIO()
        with redirect_stderr(errors):
            exit_code = main(
                [
                    "lineage",
                    "add-hop",
                    "manual",
                    "--job",
                    "scripts.missing",
                    "--source",
                    "stage.Sales",
                    "--target",
                    "mart.Sales",
                    "--operation",
                    "insert",
                    "--evidence-reference",
                    "human:note",
                    "--reason",
                    "Entered manually.",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("manual_job_not_found", errors.getvalue())

    def test_imported_documents_cannot_receive_manual_jobs(self) -> None:
        imported = replace(create_manual_lineage("imported"), source_kind="sql_server_agent")

        with self.assertRaises(LineageFailure) as raised:
            add_manual_job(
                imported,
                kind="procedure",
                name="LoadSales",
                qualified_name="etl.LoadSales",
                language="tsql",
                source_reference="sql-agent:nightly",
                description="Imported job.",
            )

        self.assertEqual(raised.exception.code, "manual_overlay_required")

    def test_manual_job_revision_is_independent_of_entry_order(self) -> None:
        def add(document, qualified_name):
            return add_manual_job(
                document,
                kind="script",
                name=qualified_name.rsplit(".", 1)[-1],
                qualified_name=qualified_name,
                language="python",
                source_reference=f"repo:{qualified_name}",
                description=f"Runs {qualified_name}.",
            )[0]

        forward = add(add(create_manual_lineage("ordered"), "jobs.alpha"), "jobs.beta")
        reverse = add(add(create_manual_lineage("ordered"), "jobs.beta"), "jobs.alpha")

        self.assertEqual(forward.to_dict(), reverse.to_dict())
        self.assertEqual(lineage_revision(forward), lineage_revision(reverse))
