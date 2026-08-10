import json
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogResult,
)
from tarel.context import ContextFailure, compile_context, compile_context_prefix
from tarel.context_caching import split_context_packet
from tarel.context_output import canonical_hash
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.store import FileGraphStore
from tarel.relationships.core import add_manual_relationship, relationship_pair


class ContextTests(TestCase):
    def test_search_seed_expands_over_bounded_declared_join_paths(self) -> None:
        graph = _context_graph(include_geography_fk=True)

        result = compile_context(
            graph,
            "sales city",
            seed_limit=1,
            max_objects=3,
            max_joins=2,
            max_hops=2,
            max_fields_per_object=2,
        )

        self.assertEqual(
            [item.label for item in result.objects],
            ["sales.FactSales", "sales.DimCustomer", "sales.DimGeography"],
        )
        self.assertEqual([item.distance for item in result.objects], [0, 1, 2])
        self.assertEqual(len(result.joins), 2)
        self.assertEqual(
            result.paths[-1].objects,
            ("sales.FactSales", "sales.DimCustomer", "sales.DimGeography"),
        )
        fact = result.objects[0]
        self.assertEqual({field.name for field in fact.fields}, {"CustomerKey", "SalesAmount"})
        customer_key = next(field for field in fact.fields if field.name == "CustomerKey")
        self.assertIn("join", customer_key.reasons)

    def test_only_validated_candidates_are_available_for_expansion(self) -> None:
        graph = _context_graph(include_geography_fk=False)
        pair = relationship_pair(
            graph,
            "sales.DimCustomer.GeographyKey",
            "sales.DimGeography.GeographyKey",
        )
        draft, _edge = add_manual_relationship(
            graph,
            pair=pair,
            reason="Candidate supplied by a data owner.",
            validated=False,
        )
        validated, _edge = add_manual_relationship(
            graph,
            pair=pair,
            reason="Confirmed by a data owner.",
            validated=True,
        )

        draft_result = compile_context(draft, "sales city", seed_limit=1, max_hops=2)
        validated_result = compile_context(validated, "sales city", seed_limit=1, max_hops=2)

        self.assertNotIn("sales.DimGeography", [item.label for item in draft_result.objects])
        self.assertIn("sales.DimGeography", [item.label for item in validated_result.objects])
        self.assertIn("validated_candidate", [join.kind for join in validated_result.joins])

    def test_invalid_budget_is_not_silently_adjusted(self) -> None:
        with self.assertRaisesRegex(ContextFailure, "cannot exceed"):
            compile_context(_context_graph(), "sales", seed_limit=4, max_objects=3)
        with self.assertRaisesRegex(ContextFailure, "character budget"):
            compile_context(_context_graph(), "sales", max_characters=999)

    def test_cli_uses_the_same_context_compiler(self) -> None:
        graph = _context_graph(include_geography_fk=True)
        with TemporaryDirectory() as temporary_directory:
            store = FileGraphStore(Path(temporary_directory))
            store.save(graph)
            output = StringIO()
            with (
                patch("tarel.application.FileGraphStore", return_value=store),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "context",
                        "context_demo",
                        "sales city",
                        "--seed-limit",
                        "1",
                        "--max-objects",
                        "3",
                        "--format",
                        "json",
                    ]
                )

        rendered = output.getvalue()
        payload = json.loads(rendered)
        self.assertEqual(exit_code, 0)
        self.assertLess(rendered.index('"stable"'), rendered.index('"dynamic"'))
        self.assertLess(rendered.index('"dynamic"'), rendered.index('"identity"'))
        self.assertEqual(payload["contract_version"], "tarel.context.v0.2")
        self.assertEqual(payload["stable"]["scope"]["mode"], "retrieval")
        self.assertIn(
            "sales.FactSales",
            [item["label"] for item in payload["stable"]["objects"]],
        )
        selected_id = payload["dynamic"]["selection"][0]["id"]
        fact_id = next(
            item["id"]
            for item in payload["stable"]["objects"]
            if item["label"] == "sales.FactSales"
        )
        self.assertEqual(selected_id, fact_id)
        self.assertEqual(len(payload["stable"]["joins"]), 2)
        self.assertEqual(payload["dynamic"]["query"], "sales city")

    def test_context_contract_is_deterministic_and_separates_query_state(self) -> None:
        graph = _context_graph(include_geography_fk=True)

        first = compile_context(graph, "sales city", seed_limit=1, max_objects=3)
        second = compile_context(graph, "sales city", seed_limit=1, max_objects=3)
        payload = first.to_dict()

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.graph_revision), 64)
        self.assertEqual(payload["stable"]["graph"]["revision"], first.graph_revision)
        self.assertNotIn("query", payload["stable"])
        self.assertNotIn("search_score", payload["stable"]["objects"][0])
        self.assertEqual(payload["dynamic"]["query"], "sales city")
        self.assertIn("search_score", payload["dynamic"]["selection"][0])
        self.assertEqual(payload["identity"]["stable_hash"], canonical_hash(payload["stable"]))
        self.assertEqual(payload["identity"]["dynamic_hash"], canonical_hash(payload["dynamic"]))
        self.assertEqual(payload["identity"]["packet_hash"], first.packet_hash)
        self.assertEqual(first.context_characters, len(first.canonical_json()))
        self.assertLess(
            first.canonical_json().index('"stable"'),
            first.canonical_json().index('"dynamic"'),
        )
        self.assertLess(
            first.canonical_json().index('"dynamic"'),
            first.canonical_json().index('"identity"'),
        )

    def test_query_only_change_preserves_stable_identity(self) -> None:
        first = compile_context(
            _context_graph(include_geography_fk=True),
            "sales city",
            namespace="sales",
            seed_limit=1,
            max_objects=3,
        )
        changed = replace(first, query="show annual sales")

        self.assertEqual(first.scope.to_dict(), {"mode": "retrieval", "namespace": "sales"})
        self.assertEqual(first.stable_hash, changed.stable_hash)
        self.assertNotEqual(first.dynamic_hash, changed.dynamic_hash)
        self.assertNotEqual(first.packet_hash, changed.packet_hash)

    def test_scope_prefix_is_query_independent_and_split_blocks_are_hash_bound(self) -> None:
        graph = _context_graph(include_geography_fk=True)
        prefix = compile_context_prefix(
            graph,
            namespace="sales",
            max_objects=10,
            max_joins=10,
        )
        repeated_prefix = compile_context_prefix(
            graph,
            namespace="sales",
            max_objects=10,
            max_joins=10,
        )
        packet = compile_context(graph, "sales city", seed_limit=1, max_objects=3)
        parts = split_context_packet(packet)

        self.assertEqual(prefix.query, "")
        self.assertEqual(prefix.canonical_json(), repeated_prefix.canonical_json())
        self.assertEqual(prefix.retrieval_mode, "scope")
        self.assertEqual(prefix.scope.mode, "graph_prefix")
        self.assertEqual(
            [item.label for item in prefix.objects],
            ["sales.DimCustomer", "sales.DimGeography", "sales.FactSales"],
        )
        self.assertEqual(len(prefix.joins), 2)
        self.assertEqual(parts.cache_key, packet.stable_hash)
        self.assertEqual(json.loads(parts.stable_json)["stable_hash"], packet.stable_hash)
        self.assertEqual(json.loads(parts.dynamic_json)["packet_hash"], packet.packet_hash)

    def test_cli_compiles_a_query_independent_prefix(self) -> None:
        graph = _context_graph(include_geography_fk=True)
        with TemporaryDirectory() as temporary_directory:
            store = FileGraphStore(Path(temporary_directory))
            store.save(graph)
            output = StringIO()
            with (
                patch("tarel.application.FileGraphStore", return_value=store),
                redirect_stdout(output),
            ):
                exit_code = main(
                    [
                        "context",
                        "prefix",
                        "context_demo",
                        "--namespace",
                        "sales",
                        "--format",
                        "json",
                    ]
                )

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["stable"]["scope"]["mode"], "graph_prefix")
        self.assertEqual(payload["dynamic"]["query"], "")
        self.assertEqual(payload["dynamic"]["retrieval"]["mode"], "scope")

    def test_packet_has_no_volatile_runtime_metadata(self) -> None:
        result = compile_context(_context_graph(), "sales")
        rendered = result.canonical_json().lower()

        for forbidden in ("timestamp", "elapsed", "duration", "temporarydirectory", "/tmp/"):
            self.assertNotIn(forbidden, rendered)

    def test_character_budget_prunes_deterministically_and_reports_omissions(self) -> None:
        result = compile_context(
            _context_graph(include_geography_fk=True),
            "sales city",
            seed_limit=1,
            max_objects=3,
            max_characters=1_300,
        )

        canonical = json.dumps(
            result.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        stable = json.dumps(
            result.stable_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertLessEqual(result.context_characters, 1_300)
        self.assertEqual(result.context_characters, len(canonical))
        self.assertEqual(result.stable_characters, len(stable))
        self.assertIn("character_budget", result.omissions.reasons)
        self.assertGreater(
            result.omissions.fields + result.omissions.joins + result.omissions.objects,
            0,
        )

    def test_text_output_places_stable_context_before_the_question(self) -> None:
        graph = _context_graph(include_geography_fk=True)
        with TemporaryDirectory() as temporary_directory:
            store = FileGraphStore(Path(temporary_directory))
            store.save(graph)
            output = StringIO()
            with (
                patch("tarel.application.FileGraphStore", return_value=store),
                redirect_stdout(output),
            ):
                exit_code = main(
                    ["context", "context_demo", "sales city", "--seed-limit", "1"]
                )

        text = output.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertLess(text.index("## Stable objects"), text.index("Question: sales city"))
        self.assertIn("Revision: ", text)
        self.assertIn("Omissions: ", text)


def _context_graph(*, include_geography_fk: bool = False):
    relationships = [
        CatalogRelationship(
            name="FK_FactSales_DimCustomer",
            from_namespace="sales",
            from_object="FactSales",
            from_fields=("CustomerKey",),
            to_namespace="sales",
            to_object="DimCustomer",
            to_fields=("CustomerKey",),
        )
    ]
    if include_geography_fk:
        relationships.append(
            CatalogRelationship(
                name="FK_DimCustomer_DimGeography",
                from_namespace="sales",
                from_object="DimCustomer",
                from_fields=("GeographyKey",),
                to_namespace="sales",
                to_object="DimGeography",
                to_fields=("GeographyKey",),
            )
        )
    return build_graph_from_catalog(
        "context_demo",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="ContextDemo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="sales",
                    name="FactSales",
                    kind="table",
                    fields=(
                        CatalogField("CustomerKey", 1, "integer", False),
                        CatalogField("SalesAmount", 2, "decimal", False),
                    ),
                ),
                CatalogObject(
                    namespace="sales",
                    name="DimCustomer",
                    kind="table",
                    fields=(
                        CatalogField("CustomerKey", 1, "integer", False, is_primary_key=True),
                        CatalogField("GeographyKey", 2, "integer", False),
                        CatalogField("CustomerName", 3, "varchar", False),
                    ),
                    primary_key=("CustomerKey",),
                ),
                CatalogObject(
                    namespace="sales",
                    name="DimGeography",
                    kind="table",
                    fields=(
                        CatalogField("GeographyKey", 1, "integer", False, is_primary_key=True),
                        CatalogField("City", 2, "varchar", False),
                    ),
                    primary_key=("GeographyKey",),
                ),
            ),
            relationships=tuple(relationships),
        ),
    )
