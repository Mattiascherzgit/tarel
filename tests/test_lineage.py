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
from tarel.lineage.contracts import LineageDocument, LineageFailure
from tarel.lineage.core import apply_lineage_proposal, build_lineage, process_view, table_lineage
from tarel.lineage.coverage import write_markers
from tarel.lineage.review import decide_lineage_item
from tarel.lineage.source import (
    LineageInput,
    SourceDefinition,
    SourceStep,
    load_lineage_input,
)
from tarel.lineage.store import FileLineageStore
from tarel.lineage.tasks import lineage_task, plan_lineage_tasks

_FIXTURE = Path(__file__).parent / "fixtures/lineage/adventureworks/sales_refresh.json"


class LineageTests(TestCase):
    def test_input_contract_is_strict_and_revision_is_deterministic(self) -> None:
        first = load_lineage_input(_FIXTURE)
        second = load_lineage_input(_FIXTURE)

        self.assertEqual(first.revision, second.revision)
        self.assertEqual(len(first.definitions), 2)
        with TemporaryDirectory() as temporary_directory:
            payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
            payload["source"]["unsupported"] = True
            invalid = Path(temporary_directory) / "invalid.json"
            invalid.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(LineageFailure, "unsupported"):
                load_lineage_input(invalid)

    def test_coverage_guard_finds_writes_but_ignores_comments_and_strings(self) -> None:
        source = """CREATE PROCEDURE demo AS
-- INSERT dbo.NotReal
SELECT 'UPDATE dbo.NotReal';
SELECT value
INTO #Stage
FROM dbo.Source;
INSERT dbo.Target SELECT value FROM #Stage;
UPDATE target_alias SET value = 1 FROM #Stage AS target_alias;
"""

        markers = write_markers(source)

        self.assertEqual(
            [(item.operation, item.line) for item in markers],
            [("select_into", 5), ("insert", 7), ("update", 8)],
        )

    def test_build_round_trip_does_not_persist_source_code(self) -> None:
        source = load_lineage_input(_FIXTURE)
        document = build_lineage("aw-sales", source)

        serialized = json.dumps(document.to_dict(), sort_keys=True)
        self.assertNotIn("CREATE PROCEDURE", serialized)
        self.assertEqual(LineageDocument.from_dict(document.to_dict()), document)
        with TemporaryDirectory() as temporary_directory:
            store = FileLineageStore(Path(temporary_directory))
            path = store.save(document)
            self.assertEqual(store.load("aw-sales"), document)
            self.assertNotIn("CREATE PROCEDURE", path.read_text(encoding="utf-8"))

        analyzed = apply_lineage_proposal(
            document,
            source,
            _proposal(document, source.definitions[0]),
        ).to_dict()
        analyzed["analyses"][0]["definition_revision"] = "0" * 64
        with self.assertRaisesRegex(LineageFailure, "does not match"):
            LineageDocument.from_dict(analyzed)

    def test_process_view_preserves_observed_workflow_order(self) -> None:
        document = build_lineage("aw-sales", load_lineage_input(_FIXTURE))

        steps = process_view(document)

        self.assertEqual(
            [item.name for item in steps],
            ["Stage Internet Sales", "Load Annual Product Sales"],
        )
        self.assertEqual(steps[0].depends_on, ())
        self.assertEqual(steps[1].depends_on, ("Stage Internet Sales",))

    def test_orchestrator_exporters_share_the_same_kernel_path(self) -> None:
        source = load_lineage_input(_FIXTURE)

        for source_kind in ("airflow", "sqlserver-agent"):
            exported = replace(source, source_kind=source_kind)
            document = build_lineage(f"aw-{source_kind}", exported)

            self.assertEqual(document.source_kind, source_kind)
            self.assertEqual(
                [item.name for item in process_view(document)],
                ["Stage Internet Sales", "Load Annual Product Sales"],
            )
            self.assertEqual(len(plan_lineage_tasks(document, exported)), 2)

    def test_apply_requires_evidence_and_complete_write_coverage(self) -> None:
        source = load_lineage_input(_FIXTURE)
        document = build_lineage("aw-sales", source)
        definition = source.definitions[0]

        updated = apply_lineage_proposal(document, source, _proposal(document, definition))

        self.assertEqual(len(updated.write_units), 1)
        self.assertEqual(len(updated.write_units[0].sources), 2)
        incomplete = _proposal(document, definition)
        incomplete["analysis"]["writes"] = []
        with self.assertRaisesRegex(LineageFailure, "did not classify"):
            apply_lineage_proposal(document, source, incomplete)

        invalid = _proposal(document, definition)
        invalid["analysis"]["writes"][0]["sources"][0]["target"] = "other.FactInternetSales"
        with self.assertRaisesRegex(LineageFailure, "not present"):
            apply_lineage_proposal(document, source, invalid)

        broad = _proposal(document, definition)
        broad["analysis"]["writes"][0]["line_start"] -= 1
        with self.assertRaisesRegex(LineageFailure, "did not classify"):
            apply_lineage_proposal(document, source, broad)

    def test_changed_source_is_explicitly_rejected(self) -> None:
        source = load_lineage_input(_FIXTURE)
        document = build_lineage("aw-sales", source)
        first = source.definitions[0]
        changed_definition = SourceDefinition(
            external_id=first.external_id,
            kind=first.kind,
            name=first.name,
            qualified_name=first.qualified_name,
            language=first.language,
            content=f"{first.content}\n-- changed",
            source_reference=first.source_reference,
        )
        changed = LineageInput(
            source_kind=source.source_kind,
            source_name=source.source_name,
            source_reference=source.source_reference,
            workflow_external_id=source.workflow_external_id,
            workflow_name=source.workflow_name,
            definitions=(changed_definition, *source.definitions[1:]),
            steps=source.steps,
        )

        with self.assertRaisesRegex(LineageFailure, "Refresh semantics"):
            plan_lineage_tasks(document, changed)

    def test_explicit_write_units_do_not_cross_unrelated_reads_and_writes(self) -> None:
        source = _two_write_source()
        document = build_lineage("two-writes", source)
        proposal = _two_write_proposal(document, source.definitions[0])
        duplicate_source = dict(proposal["analysis"]["writes"][0]["sources"][0])
        duplicate_source["line_start"] = 1
        proposal["analysis"]["writes"][0]["sources"].append(duplicate_source)

        document = apply_lineage_proposal(document, source, proposal)
        rows = table_lineage(document)

        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {(item.source, item.target) for item in rows},
            {("dbo.SourceA", "dbo.TargetX"), ("dbo.SourceB", "dbo.TargetY")},
        )
        unit = document.write_units[0]
        reviewed, _ = decide_lineage_item(
            document,
            unit.id,
            decision="validate",
            reason="Checked against the complete statement.",
        )
        self.assertTrue(
            all(
                item.state == "validated"
                for item in table_lineage(reviewed)
                if item.target == unit.target
            )
        )
        rejected, _ = decide_lineage_item(
            reviewed,
            unit.id,
            decision="reject",
            reason="Rejected during human review.",
        )
        self.assertFalse(any(item.target == unit.target for item in table_lineage(rejected)))
        with self.assertRaisesRegex(LineageFailure, "human-reviewed"):
            apply_lineage_proposal(reviewed, source, proposal)

    def test_provider_run_retries_with_coverage_feedback(self) -> None:
        source = load_lineage_input(_FIXTURE)
        definition = source.definitions[0]
        document = build_lineage("aw-sales", source)
        valid = _proposal(document, definition)["analysis"]
        provider = _CorrectingProvider(
            [
                {
                    "excluded_writes": [],
                    "observations": [],
                    "summary": "Incomplete first attempt.",
                    "warnings": [],
                    "writes": [],
                },
                valid,
            ]
        )
        previous_directory = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                store = FileLineageStore()
                store.save(document)
                with patch("tarel.lineage.application.load_provider", return_value=provider):
                    result = _run_cli(
                        [
                            "lineage",
                            "analyze",
                            "aw-sales",
                            "--source",
                            str(_FIXTURE),
                            "--provider",
                            "openrouter",
                            "--limit",
                            "1",
                            "--retry",
                            "1",
                            "--review-passes",
                            "0",
                            "--format",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous_directory)

        self.assertEqual(result[0], 0)
        self.assertEqual(json.loads(result[1])["applied"], 1)
        self.assertIn("incomplete_write_coverage", provider.requests[1].messages[-1].content)
        self.assertEqual(provider.requests[0].max_output_tokens, 24_000)
        self.assertEqual(provider.requests[0].reasoning_effort, "high")
        self.assertIn("definition 1/1", result[2])
        self.assertIn("saved", result[2])

    def test_cli_vertical_slice(self) -> None:
        previous_directory = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                build_output = _run_cli(
                    ["lineage", "build", "aw-sales", "--source", str(_FIXTURE), "--format", "json"]
                )
                self.assertEqual(build_output[0], 0)
                next_output = _run_cli(["lineage", "next", "aw-sales", "--source", str(_FIXTURE)])
                task = json.loads(next_output[1])
                source = load_lineage_input(_FIXTURE)
                definition = source.definition_by_id()[task["definition_id"]]
                proposal = _proposal_from_task(task, definition)
                proposal_path = Path(temporary_directory) / "proposal.json"
                proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

                applied = _run_cli(
                    [
                        "lineage",
                        "apply",
                        "aw-sales",
                        "--source",
                        str(_FIXTURE),
                        "--input",
                        str(proposal_path),
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(len(json.loads(applied[1])["lineage"]["write_units"]), 1)
                reviewed = _run_cli(
                    ["lineage", "review", "aw-sales", "--state", "draft", "--format", "json"]
                )
                items = json.loads(reviewed[1])["items"]
                self.assertEqual(len(items), 1)
                decision = _run_cli(
                    [
                        "lineage",
                        "review",
                        "aw-sales",
                        items[0]["id"],
                        "--decision",
                        "validate",
                        "--reason",
                        "Checked against the procedure.",
                        "--format",
                        "json",
                    ]
                )
                self.assertEqual(json.loads(decision[1])["item"]["state"], "validated")
                tables = _run_cli(
                    ["lineage", "show", "aw-sales", "--view", "tables", "--format", "json"]
                )
                self.assertEqual(len(json.loads(tables[1])["tables"]), 2)
            finally:
                os.chdir(previous_directory)


def _proposal(document: LineageDocument, definition: SourceDefinition) -> dict[str, object]:
    return _proposal_from_task(lineage_task(document, definition).to_dict(), definition)


def _proposal_from_task(
    task: dict[str, object],
    definition: SourceDefinition,
) -> dict[str, object]:
    lines = definition.content.splitlines()
    if definition.name == "usp_StageInternetSales":
        target = "tarel_demo.InternetSalesStage"
        target_marker = "INSERT INTO"
        sources = [
            ("dbo.FactInternetSales", "FactInternetSales", "business_data"),
            ("dbo.DimDate", "DimDate", "lookup"),
        ]
    else:
        target = "tarel_demo.AnnualProductSales"
        target_marker = "INSERT INTO"
        sources = [
            ("tarel_demo.InternetSalesStage", "FROM [tarel_demo]", "business_data"),
            ("dbo.DimDate", "DimDate", "lookup"),
            ("dbo.DimProduct", "DimProduct", "lookup"),
        ]
    target_line = _line(lines, target_marker)
    write_sources = [
        {
            "line_end": _line(lines, marker),
            "line_start": _line(lines, marker),
            "reason": "This source directly feeds the write statement.",
            "role": role,
            "target": source,
            "via": [],
        }
        for source, marker, role in sources
    ]
    return {
        "analysis": {
            "excluded_writes": [],
            "observations": [],
            "summary": "One explicit persistent write and its physical sources.",
            "warnings": [],
            "writes": [
                {
                    "line_end": target_line,
                    "line_start": target_line,
                    "operation": "insert",
                    "reason": "The INSERT statement directly names the persistent target.",
                    "sources": write_sources,
                    "target": target,
                    "warnings": [],
                }
            ],
        },
        "definition_id": definition.id,
        "task_id": task["id"],
    }


def _two_write_source() -> LineageInput:
    definition = SourceDefinition(
        external_id="two-writes",
        kind="procedure",
        name="TwoWrites",
        qualified_name="demo.TwoWrites",
        language="tsql",
        content=(
            "CREATE PROCEDURE demo.TwoWrites AS\n"
            "INSERT dbo.TargetX SELECT * FROM dbo.SourceA;\n"
            "INSERT dbo.TargetY SELECT * FROM dbo.SourceB;"
        ),
        source_reference="demo:TwoWrites",
    )
    return LineageInput(
        source_kind="test",
        source_name="Two writes",
        source_reference="tests:test",
        workflow_external_id="two-writes",
        workflow_name="Two writes",
        definitions=(definition,),
        steps=(SourceStep("two-writes", "Two writes", definition.external_id, ()),),
    )


def _two_write_proposal(
    document: LineageDocument,
    definition: SourceDefinition,
) -> dict[str, object]:
    task = lineage_task(document, definition)
    writes = []
    for line, target, source in (
        (2, "dbo.TargetX", "dbo.SourceA"),
        (3, "dbo.TargetY", "dbo.SourceB"),
    ):
        writes.append(
            {
                "line_end": line,
                "line_start": line,
                "operation": "insert",
                "reason": "The statement names this write target.",
                "sources": [
                    {
                        "line_end": line,
                        "line_start": line,
                        "reason": "The same statement names this physical source.",
                        "role": "business_data",
                        "target": source,
                        "via": [],
                    }
                ],
                "target": target,
                "warnings": [],
            }
        )
    return {
        "analysis": {
            "excluded_writes": [],
            "observations": [],
            "summary": "Two independent writes.",
            "warnings": [],
            "writes": writes,
        },
        "definition_id": definition.id,
        "task_id": task.id,
    }


def _line(lines: list[str], marker: str) -> int:
    return next(index for index, line in enumerate(lines, 1) if marker in line)


def _run_cli(arguments: list[str]) -> tuple[int, str, str]:
    output = StringIO()
    errors = StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        exit_code = main(arguments)
    return exit_code, output.getvalue(), errors.getvalue()


class _CorrectingProvider:
    name = "openrouter"
    default_model = "deepseek/deepseek-v4-flash"

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.requests = []

    def generate_structured(self, request):
        self.requests.append(request)
        return self.responses.pop(0)
