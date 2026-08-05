import json
import tomllib
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.contracts import GraphAnnotation
from tarel.graph.store import FileGraphStore
from tarel.search import SearchFailure, search_graph


class SearchTests(TestCase):
    def test_object_search_combines_object_and_field_evidence(self) -> None:
        graph = _sales_graph()

        results = search_graph(graph, "internet sales amount currency", limit=5)

        self.assertEqual(results.hits[0].label, "sales.FactInternetSales")
        self.assertEqual(
            results.hits[0].matched_terms,
            ("amount", "currency", "internet", "sale"),
        )
        self.assertEqual(results.hits[0].fields[0].label, "SalesAmount")
        self.assertIn("object_name:internet", results.hits[0].reasons)

    def test_annotations_and_synonyms_are_searchable(self) -> None:
        graph = _sales_graph()
        currency = next(node for node in graph.nodes if node.label == "sales.DimCurrency")
        annotated = replace(
            graph,
            nodes=tuple(
                replace(
                    node,
                    annotation=GraphAnnotation(
                        description="Currencies used for reporting.",
                        synonyms=("FX lookup",),
                    ),
                )
                if node.id == currency.id
                else node
                for node in graph.nodes
            ),
        )

        results = search_graph(annotated, "fx lookup")

        self.assertEqual(results.hits[0].label, "sales.DimCurrency")
        self.assertEqual(results.hits[0].matched_terms, ("fx", "lookup"))

    def test_namespace_filter_and_limits_are_explicit(self) -> None:
        graph = _sales_graph()

        self.assertEqual(search_graph(graph, "sales", namespace="missing").hits, ())
        with self.assertRaisesRegex(SearchFailure, "between 1 and 100"):
            search_graph(graph, "sales", limit=0)
        with self.assertRaisesRegex(SearchFailure, "meaningful term"):
            search_graph(graph, "which and the")

    def test_cli_returns_the_same_structured_search_result(self) -> None:
        graph = _sales_graph()
        with TemporaryDirectory() as temporary_directory:
            store = FileGraphStore(Path(temporary_directory))
            store.save(graph)
            output = StringIO()
            with (
                patch("tarel.application.FileGraphStore", return_value=store),
                redirect_stdout(output),
            ):
                exit_code = main(
                    ["search", "sales_demo", "internet sales amount", "--format", "json"]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["hits"][0]["label"], "sales.FactInternetSales")

    def test_adventureworks_fixture_has_unique_reviewable_cases(self) -> None:
        fixture_path = Path(__file__).parent / "fixtures" / "adventureworks_search.toml"
        fixture = tomllib.loads(fixture_path.read_text(encoding="utf-8"))
        cases = fixture["cases"]

        self.assertEqual(len(cases), 10)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        for case in cases:
            self.assertTrue(case["query"])
            self.assertTrue(case["primary_object"])
            self.assertTrue(case["required_fields"])
            self.assertTrue(case["relationships"])


def _sales_graph():
    return build_graph_from_catalog(
        "sales_demo",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="SalesDemo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="sales",
                    name="FactInternetSales",
                    kind="table",
                    fields=(
                        CatalogField("CurrencyKey", 1, "integer", False),
                        CatalogField("SalesAmount", 2, "decimal", False),
                    ),
                ),
                CatalogObject(
                    namespace="sales",
                    name="DimCurrency",
                    kind="table",
                    fields=(CatalogField("CurrencyName", 1, "varchar", False),),
                ),
            ),
        ),
    )
