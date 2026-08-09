import json
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.application import (
    build_focus_use_case,
    plan_focus_annotations_use_case,
)
from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.focus.contracts import FocusDocument, FocusFailure
from tarel.focus.store import FileFocusStore
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.contracts import GraphAnnotation
from tarel.graph.store import FileGraphStore
from tarel.lineage.contracts import LineageClaim, LineageEvidence
from tarel.lineage.core import build_lineage
from tarel.lineage.source import (
    LineageInput,
    SourceDefinition,
    SourceMaterialization,
    SourceStep,
)
from tarel.lineage.store import FileLineageStore


class FocusTests(TestCase):
    def test_focus_round_trip_and_annotation_plan_follow_the_upstream_slice(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                graph, lineage = _fixture()
                FileGraphStore().save(graph)
                FileLineageStore().save(lineage)

                result = build_focus_use_case(
                    "sales-report",
                    seed="Demo.mart.Sales",
                    lineage_names=("dbt-sales",),
                    graph_names=("demo",),
                )
                loaded = FileFocusStore().load("sales-report")
                tasks = plan_focus_annotations_use_case("sales-report")
            finally:
                os.chdir(previous)

        self.assertEqual(FocusDocument.from_dict(result.focus.to_dict()), result.focus)
        self.assertEqual(loaded, result.focus)
        self.assertEqual(
            [(item.reference, item.depth) for item in result.focus.members],
            [
                ("Demo.mart.Sales", 0),
                ("dbt.model.sales", 1),
                ("Demo.raw.Sales", 2),
            ],
        )
        self.assertEqual(
            [item.relation for item in result.focus.hops],
            ["materializes_as", "reads_from"],
        )
        self.assertEqual(
            [(item.graph_name, item.target_label) for item in tasks],
            [("demo", "mart.Sales"), ("demo", "raw.Sales")],
        )

    def test_focus_ignores_annotation_edits_but_fails_on_structural_change(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                graph, lineage = _fixture()
                graph_store = FileGraphStore()
                graph_store.save(graph)
                FileLineageStore().save(lineage)
                build_focus_use_case(
                    "sales-report",
                    seed="Demo.mart.Sales",
                    lineage_names=("dbt-sales",),
                    graph_names=("demo",),
                )
                first = next(item for item in graph.nodes if item.label == "raw.Sales")
                graph_store.save(
                    replace(
                        graph,
                        nodes=tuple(
                            replace(
                                item,
                                annotation=GraphAnnotation(
                                    description="New semantic proposal."
                                ),
                            )
                            if item.id == first.id
                            else item
                            for item in graph.nodes
                        ),
                    )
                )
                self.assertTrue(plan_focus_annotations_use_case("sales-report"))
                annotated = graph_store.load("demo")
                raw_sales = next(
                    item for item in annotated.nodes if item.label == "raw.Sales"
                )
                graph_store.save(
                    replace(
                        annotated,
                        nodes=tuple(
                            replace(item, label="raw.RenamedSales")
                            if item.id == raw_sales.id
                            else item
                            for item in annotated.nodes
                        ),
                    )
                )
                with self.assertRaises(FocusFailure) as raised:
                    plan_focus_annotations_use_case("sales-report")
            finally:
                os.chdir(previous)

        self.assertEqual(raised.exception.code, "focus_stale")

    def test_cli_build_show_and_list(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory() as temporary_directory:
            os.chdir(temporary_directory)
            try:
                graph, lineage = _fixture()
                FileGraphStore().save(graph)
                FileLineageStore().save(lineage)
                built = _run_cli(
                    [
                        "focus",
                        "build",
                        "sales-report",
                        "--seed",
                        "Demo.mart.Sales",
                        "--lineage",
                        "dbt-sales",
                        "--graph",
                        "demo",
                        "--format",
                        "json",
                    ]
                )
                shown = _run_cli(["focus", "show", "sales-report", "--format", "json"])
                listed = _run_cli(["focus", "list", "--format", "json"])
                plan = _run_cli(
                    ["annotation", "plan", "--focus", "sales-report", "--format", "json"]
                )
            finally:
                os.chdir(previous)

        self.assertEqual(built[0], 0)
        self.assertEqual(json.loads(shown[1])["seed"], "Demo.mart.Sales")
        self.assertEqual(json.loads(listed[1]), {"focuses": ["sales-report"]})
        self.assertEqual(
            [item["target"] for item in json.loads(plan[1])["tasks"]],
            ["mart.Sales", "raw.Sales"],
        )


def _fixture():
    graph = build_graph_from_catalog(
        "demo",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="Demo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="raw",
                    name="Sales",
                    kind="table",
                    fields=(CatalogField("SaleId", 1, "integer", False),),
                ),
                CatalogObject(
                    namespace="mart",
                    name="Sales",
                    kind="table",
                    fields=(CatalogField("SaleId", 1, "integer", False),),
                ),
                CatalogObject(
                    namespace="archive",
                    name="UnusedSales",
                    kind="table",
                    fields=(CatalogField("SaleId", 1, "integer", False),),
                ),
            ),
        ),
    )
    definition = SourceDefinition(
        external_id="model.sales",
        kind="query",
        name="sales",
        qualified_name="dbt.model.sales",
        language="sql",
        content="select * from Demo.raw.Sales",
        source_reference="dbt/models/sales.sql",
    )
    source = LineageInput(
        source_kind="dbt",
        source_name="Demo dbt",
        source_reference="dbt/target/manifest.json",
        workflow_external_id="dbt-demo",
        workflow_name="dbt demo",
        definitions=(definition,),
        steps=(SourceStep("model.sales", "sales", definition.external_id, ()),),
        materializations=(
            SourceMaterialization(
                definition_external_id=definition.external_id,
                target="Demo.mart.Sales",
                mode="table",
                source_reference="dbt/target/manifest.json:model.sales",
            ),
        ),
    )
    lineage = build_lineage("dbt-sales", source)
    persisted = lineage.definitions[0]
    read = LineageClaim(
        id="read-raw-sales",
        definition_id=persisted.id,
        operation="read",
        target="Demo.raw.Sales",
        state="draft",
        evidence=LineageEvidence(
            source="provider_analysis",
            reference="dbt/models/sales.sql:1-1",
            reason="The query reads the named source.",
            line_start=1,
            line_end=1,
        ),
    )
    return graph, replace(lineage, claims=(read,))


def _run_cli(arguments: list[str]) -> tuple[int, str, str]:
    output = StringIO()
    errors = StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        code = main(arguments)
    return code, output.getvalue(), errors.getvalue()
