import json
import os
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.demo import create_retail_demo
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.contracts import GraphAnnotation
from tarel.lineage.contracts import LineageClaim, LineageEvidence, LineageFailure
from tarel.lineage.core import build_lineage
from tarel.lineage.revision import lineage_revision
from tarel.lineage.source import (
    LineageInput,
    SourceDefinition,
    SourceMaterialization,
    SourceStep,
)
from tarel.sdk import Tarel, WorkspaceScope
from tarel.workspaces.contracts import WorkspaceFailure
from tarel.workspaces.core import create_workspace, define_system


class SDKTests(TestCase):
    def test_graph_build_uses_the_selected_state_root(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            demo = create_retail_demo(path=root / "retail.sqlite")
            sdk = Tarel(root / "embedded-state")

            result = sdk.graph.build(
                "retail-sdk",
                connector="sqlite",
                config=demo.config_path,
                namespace="main",
            )

        self.assertEqual(result.graph.name, "retail-sdk")
        self.assertEqual(result.path, root / "embedded-state/graphs/retail-sdk/graph.json")
        self.assertEqual(len(result.graph.nodes), 70)

    def test_explicit_root_is_isolated_and_matches_cli_context(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            project = Path(temporary_directory)
            state = project / ".tarel"
            sdk = Tarel(state)
            graph, _lineage = _fixture()
            sdk.runtime.graph_store().save(graph)
            workspace = define_system(
                create_workspace("demo-workspace"),
                "sales",
                graph_names=("demo",),
                graphs={"demo": graph},
            )
            sdk.runtime.workspace_store().save(workspace)

            sdk_search = sdk.search.graph("demo", "mart sales")
            sdk_context = sdk.context.graph("demo", "mart sales")
            workspace_search = sdk.search.workspace("demo-workspace", "mart sales")
            workspace_context = sdk.context.workspace("demo-workspace", "mart sales")

            output = StringIO()
            os.chdir(project)
            try:
                with redirect_stdout(output):
                    exit_code = main(["context", "demo", "mart sales", "--format", "json"])
            finally:
                os.chdir(previous)

            other = Tarel(project / "other-state")
            graph_names = sdk.graph.list()
            other_graph_names = other.graph.list()

        self.assertEqual(exit_code, 0)
        self.assertEqual(Path.cwd(), previous)
        self.assertEqual(json.loads(output.getvalue()), sdk_context.to_dict())
        self.assertEqual(sdk_search.hits[0].label, "mart.Sales")
        self.assertEqual(workspace_search.hits[0].label, "demo:mart.Sales")
        self.assertEqual(workspace_context.scope.workspace, "demo-workspace")
        self.assertEqual(graph_names, ("demo",))
        self.assertEqual(other_graph_names, ())

    def test_lineage_focus_and_annotation_review_share_one_runtime(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            graph, lineage = _fixture()
            sdk.runtime.graph_store().save(graph)
            sdk.runtime.lineage_store().save(lineage)

            found = sdk.lineage.find(
                "sales model",
                lineages=("dbt-sales",),
                graphs=("demo",),
            )
            trace = sdk.lineage.upstream(
                "Demo.mart.Sales",
                lineages=("dbt-sales",),
                graphs=("demo",),
            )
            focus = sdk.focus.build(
                "sales-report",
                seed="Demo.mart.Sales",
                lineages=("dbt-sales",),
                graphs=("demo",),
            )
            loaded_focus = sdk.focus.load("sales-report")
            focus_names = sdk.focus.list()
            reviews = sdk.annotation.reviews("demo")
            decided = sdk.annotation.decide(
                "demo",
                "mart.Sales",
                state="validated",
                reason="The data owner approved the proposed meaning.",
            )

            loaded_annotation = sdk.annotation.show("demo", "mart.Sales")

        self.assertIn("dbt.model.sales", {item.reference for item in found})
        self.assertEqual(
            tuple(item.reference for item in trace.origins),
            ("Demo.raw.Sales",),
        )
        self.assertEqual(focus.focus, loaded_focus)
        self.assertEqual(focus_names, ("sales-report",))
        self.assertEqual(reviews[0].reference, "mart.Sales")
        self.assertEqual(decided.record.node.annotation.state, "validated")
        self.assertEqual(loaded_annotation.node.annotation.state, "validated")

    def test_workspace_lineage_search_trace_and_canvas_projection_share_scope(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = Tarel(root / "state")
            graph, lineage = _fixture()
            sdk.runtime.graph_store().save(graph)
            sdk.runtime.lineage_store().save(lineage)
            sdk.workspace.create("enterprise")
            sdk.workspace.define_system(
                "enterprise",
                "analytics",
                graphs=("demo",),
            )
            sdk.workspace.define_area(
                "enterprise",
                "analytics",
                "marts",
                schemas=("demo:mart",),
            )
            sdk.workspace.define_zone(
                "enterprise",
                "analytics",
                "reporting",
                objects=("demo:mart.Sales",),
            )
            selection = WorkspaceScope(
                systems=("analytics",),
                zones=("reporting",),
            )
            model = root / "embedding.gguf"
            model.write_bytes(b"test-model")

            searches = {
                mode: sdk.lineage.find_workspace(
                    "enterprise",
                    "sales model",
                    lineages=("dbt-sales",),
                    selection=selection,
                    mode=mode,
                    model_path=model if mode in {"vector", "hybrid"} else None,
                )
                for mode in ("lexical", "bm25")
            }
            with patch(
                "tarel.lineage.application.LlamaCppEmbedding",
                return_value=_Embedding(),
            ):
                for mode in ("vector", "hybrid"):
                    searches[mode] = sdk.lineage.find_workspace(
                        "enterprise",
                        "sales model",
                        lineages=("dbt-sales",),
                        selection=selection,
                        mode=mode,
                        model_path=model,
                    )

            trace = sdk.lineage.upstream_workspace(
                "enterprise",
                "Demo.mart.Sales",
                lineages=("dbt-sales",),
                selection=selection,
            )
            payload = sdk.view.workspace(
                "enterprise",
                lineages=("dbt-sales",),
                selection=selection,
            )

        self.assertEqual(set(searches), {"lexical", "bm25", "vector", "hybrid"})
        self.assertTrue(all(items for items in searches.values()))
        self.assertEqual(tuple(item.reference for item in trace.origins), ("Demo.raw.Sales",))
        self.assertEqual(payload["view_modes"], ["space", "lineage"])
        self.assertEqual([item["label"] for item in payload["objects"]], ["mart.Sales"])
        self.assertEqual(payload["scope"]["selection"]["zones"], ["reporting"])
        self.assertTrue(payload["lineage_flows"]["nodes"])
        self.assertTrue(payload["lineage_flows"]["edges"])

    def test_lineage_build_writes_only_to_the_selected_root(self) -> None:
        source = Path(__file__).parent / "fixtures/lineage/adventureworks/sales_refresh.json"
        with TemporaryDirectory() as temporary_directory:
            state = Path(temporary_directory) / "embedded-state"
            sdk = Tarel(state)

            result = sdk.lineage.build("sdk-sales-refresh", source=source)
            lineage_names = sdk.lineage.list()
            next_task = sdk.lineage.next("sdk-sales-refresh", source=source)

        self.assertEqual(result.document.name, "sdk-sales-refresh")
        self.assertEqual(result.path, state / "lineage/sdk-sales-refresh/lineage.json")
        self.assertEqual(lineage_names, ("sdk-sales-refresh",))
        self.assertIsNotNone(next_task)

    def test_workspace_relationship_context_and_model_management_share_the_runtime(
        self,
    ) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = Tarel(root / "state")
            graph, _lineage = _fixture()
            sdk.runtime.graph_store().save(graph)

            created = sdk.workspace.create(
                "enterprise",
                description="Embedded application workspace.",
            )
            sdk.workspace.define_system(
                "enterprise",
                "analytics",
                graphs=("demo",),
            )
            sdk.workspace.define_area(
                "enterprise",
                "analytics",
                "sales",
                schemas=("demo:raw", "demo:mart"),
            )
            sdk.workspace.define_zone(
                "enterprise",
                "analytics",
                "reporting",
                objects=("demo:mart.Sales",),
            )
            zone = sdk.workspace.zone("enterprise", "analytics", "reporting")
            selection = WorkspaceScope(
                systems=("analytics",),
                graphs=("demo",),
                areas=("sales",),
                schemas=("demo:mart",),
                zones=("reporting",),
            )
            resolved_scope = sdk.workspace.scope("enterprise", selection=selection)
            scoped_search = sdk.search.workspace(
                "enterprise",
                "mart sales",
                selection=selection,
            )

            relationship = sdk.relationship.add(
                "demo",
                source="raw.Sales.SaleId",
                target="mart.Sales.SaleId",
                reason="The curated mart preserves the raw sales identifier.",
            )
            candidates = sdk.relationship.list("demo")
            reviewed = sdk.relationship.decide(
                "demo",
                relationship.edge.id,
                state="validated",
                reason="Verified in the transformation query.",
            )
            workspace_relationship = sdk.workspace.add_relationship(
                "enterprise",
                source="demo:raw.Sales.SaleId",
                target="demo:mart.Sales.SaleId",
                reason="The workspace records the cross-schema business join.",
                validated=True,
            )
            stored_workspace_relationships = sdk.workspace.relationships("enterprise")

            packet = sdk.context.graph("demo", "mart sales")
            cached_zone = sdk.context.prefix_workspace(
                "enterprise",
                selection=selection,
            )
            packet_parts = sdk.context.split(packet)
            left = root / "left.json"
            right = root / "right.json"
            left.write_text(packet.canonical_json(), encoding="utf-8")
            right.write_text(packet.canonical_json(), encoding="utf-8")
            comparison = sdk.context.diff(left, right)
            impact = sdk.context.impact(left, graph="demo")
            model_status = sdk.model.status(model_path=root / "missing.gguf")

        self.assertEqual(created.path, root / "state/workspaces/enterprise/workspace.json")
        self.assertEqual([item.label for item in zone.objects], ["mart.Sales"])
        self.assertEqual([item.label for item in resolved_scope.objects], ["mart.Sales"])
        self.assertEqual([item.label for item in scoped_search.hits], ["demo:mart.Sales"])
        self.assertEqual(candidates[0].id, relationship.edge.id)
        self.assertEqual(reviewed.edge.metadata["state"], "validated")
        self.assertEqual(workspace_relationship.relationship.state, "validated")
        self.assertEqual(
            stored_workspace_relationships[0].id,
            workspace_relationship.relationship.id,
        )
        self.assertTrue(comparison.identical)
        self.assertEqual(impact.status, "current")
        self.assertFalse(model_status["exists"])
        self.assertEqual(cached_zone.scope.mode, "workspace_prefix")
        self.assertEqual([item.label for item in cached_zone.objects], ["demo:mart.Sales"])
        self.assertEqual(packet_parts.cache_key, packet.stable_hash)
        self.assertIn('"query":"mart sales"', packet_parts.dynamic_json)

    def test_workspace_scope_object_cannot_be_mixed_with_individual_filters(self) -> None:
        sdk = Tarel("/tmp/tarel-sdk-scope-conflict")

        with self.assertRaises(WorkspaceFailure) as raised:
            sdk.context.prefix_workspace(
                "enterprise",
                selection=WorkspaceScope(zones=("revenue",)),
                systems=("analytics",),
            )

        self.assertEqual(raised.exception.code, "conflicting_workspace_scope")

    def test_manual_lineage_sdk_supports_human_overlays_and_revision_checks(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            job = sdk.lineage.add_job(
                "manual-sales",
                kind="procedure",
                job_name="LoadSales",
                qualified_name="etl.LoadSales",
                language="tsql",
                source_reference="runbook:load-sales",
                description="Loads reviewed sales data.",
            )
            hop = sdk.lineage.add_hop(
                "manual-sales",
                job="etl.LoadSales",
                source="stage.Sales",
                target="mart.Sales",
                operation="merge",
                evidence_reference="runbook:load-sales",
                reason="Confirmed by the data owner.",
                expected_revision=lineage_revision(job.document),
            )
            trace = sdk.lineage.upstream(
                "mart.Sales",
                lineages=("manual-sales",),
            )

        self.assertEqual(hop.item.target, "mart.Sales")
        self.assertEqual(hop.item.sources[0].target, "stage.Sales")
        self.assertEqual(tuple(item.reference for item in trace.origins), ("stage.Sales",))

    def test_vector_index_and_search_use_the_selected_state_root(self) -> None:
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            root = Path(temporary_directory)
            model = root / "embedding.gguf"
            model.write_bytes(b"test-model")
            sdk = Tarel(root / "state")
            graph, _lineage = _fixture()
            sdk.runtime.graph_store().save(graph)
            embedding = _Embedding()
            progress: list[tuple[int, int, str]] = []
            with patch("tarel.application.LlamaCppEmbedding", return_value=embedding):
                built = sdk.index.build(
                    "demo",
                    model_path=model,
                    batch_size=1,
                    progress=lambda completed, total, phase: progress.append(
                        (completed, total, phase)
                    ),
                )
                results = sdk.search.graph(
                    "demo",
                    "curated reporting",
                    mode="vector",
                    model_path=model,
                )
                status = sdk.index.status("demo")

        self.assertEqual(built.path, root / "state/indexes/demo/index.sqlite")
        self.assertEqual(results.hits[0].label, "mart.Sales")
        self.assertTrue(status["current"])
        self.assertEqual(progress[0], (0, 4, "embedding"))
        self.assertEqual(progress[-2:], [(4, 4, "writing"), (4, 4, "ready")])

    def test_grounding_bundle_maps_heterogeneous_sources_and_lineage(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            graph, lineage = _fixture()
            crm = _crm_fixture()
            sdk.runtime.graph_store().save(graph)
            sdk.runtime.graph_store().save(crm)
            sdk.runtime.lineage_store().save(lineage)
            sdk.workspace.create("enterprise")
            sdk.workspace.define_system(
                "enterprise",
                "analytics",
                graphs=("demo", "crm"),
            )

            first = sdk.grounding.context(
                "Show curated sales and customer context",
                workspace="enterprise",
                lineages=("dbt-sales",),
                trace="Demo.mart.Sales",
                seed_limit=4,
                max_objects=4,
            )
            second = sdk.grounding.context(
                "Show curated sales and customer context",
                workspace="enterprise",
                lineages=("dbt-sales",),
                trace="Demo.mart.Sales",
                seed_limit=4,
                max_objects=4,
            )
            found = sdk.grounding.find(
                "customer",
                workspace="enterprise",
                limit=2,
            )
            described = sdk.grounding.describe("demo", "mart.Sales")
            traced = sdk.grounding.upstream(
                "Demo.mart.Sales",
                workspace="enterprise",
                lineages=("dbt-sales",),
            )

        self.assertEqual(first.contract_version, "tarel.grounding.v0.1")
        self.assertEqual(first.context.contract_version, "tarel.context.v0.2")
        self.assertEqual(
            {(item.graph, item.dialect) for item in first.sources},
            {("crm", "postgresql"), ("demo", "ansi")},
        )
        self.assertTrue(all(item.read_only for item in first.sources))
        self.assertTrue(all(item.object_ids for item in first.sources))
        self.assertEqual(first.lineages[0].name, "dbt-sales")
        self.assertIn("dbt.model.sales", {item.reference for item in first.lineage_matches})
        self.assertEqual(tuple(item.reference for item in first.trace.origins), ("Demo.raw.Sales",))
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(first.stable_prompt(), second.stable_prompt())
        self.assertNotIn("Show curated sales", first.stable_prompt())
        self.assertIn("Show curated sales", first.dynamic_prompt())
        self.assertIn("evidence=The query reads the named source.", first.dynamic_prompt())
        self.assertNotIn("source_reference", first.canonical_json())
        self.assertNotIn("dbt/models/sales.sql", first.canonical_json())
        self.assertNotIn(str(Path(temporary_directory)), first.canonical_json())
        self.assertEqual(found.context.objects[0].label, "crm:public.Customers")
        self.assertEqual(described.reference, "mart.Sales")
        self.assertEqual(described.source.dialect, "ansi")
        self.assertEqual([item.label for item in described.fields], ["SaleId"])
        self.assertEqual(tuple(item.reference for item in traced.origins), ("Demo.raw.Sales",))

    def test_grounding_requires_exactly_one_graph_or_workspace(self) -> None:
        sdk = Tarel("/tmp/tarel-sdk-grounding-scope")

        with self.assertRaises(WorkspaceFailure) as missing:
            sdk.grounding.context("sales")
        with self.assertRaises(WorkspaceFailure) as conflicting:
            sdk.grounding.context("sales", graph="demo", workspace="enterprise")

        self.assertEqual(missing.exception.code, "invalid_grounding_scope")
        self.assertEqual(conflicting.exception.code, "invalid_grounding_scope")

    def test_grounding_trace_requires_explicit_lineage_documents(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            graph, _lineage = _fixture()
            sdk.runtime.graph_store().save(graph)

            with self.assertRaises(LineageFailure) as raised:
                sdk.grounding.context(
                    "sales",
                    graph="demo",
                    trace="Demo.mart.Sales",
                )

        self.assertEqual(raised.exception.code, "missing_lineage_scope")

    def test_grounding_cli_and_sdk_share_the_same_contract(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            project = Path(temporary_directory)
            sdk = Tarel(project / ".tarel")
            graph, lineage = _fixture()
            sdk.runtime.graph_store().save(graph)
            sdk.runtime.lineage_store().save(lineage)
            expected = sdk.grounding.context(
                "sales model",
                graph="demo",
                lineages=("dbt-sales",),
                trace="Demo.mart.Sales",
            )
            output = StringIO()
            os.chdir(project)
            try:
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "grounding",
                            "demo",
                            "sales model",
                            "--lineage",
                            "dbt-sales",
                            "--trace",
                            "Demo.mart.Sales",
                            "--format",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous)

        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(output.getvalue()), expected.to_dict())


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
            ),
        ),
    )
    graph = replace(
        graph,
        nodes=tuple(
            replace(
                item,
                annotation=GraphAnnotation(
                    description="Curated sales facts for analytical reporting.",
                    role="fact",
                ),
            )
            if item.label == "mart.Sales"
            else item
            for item in graph.nodes
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


def _crm_fixture():
    graph = build_graph_from_catalog(
        "crm",
        CatalogResult(
            connector="postgres",
            source_type="database",
            catalog="CRM",
            dialect="postgresql",
            objects=(
                CatalogObject(
                    namespace="public",
                    name="Customers",
                    kind="table",
                    fields=(CatalogField("CustomerId", 1, "bigint", False),),
                ),
            ),
        ),
    )
    return replace(
        graph,
        nodes=tuple(
            replace(
                item,
                annotation=GraphAnnotation(
                    description="Customer master data used to enrich analytical sales.",
                    role="dimension",
                ),
            )
            if item.label == "public.Customers"
            else item
            for item in graph.nodes
        ),
    )


class _Embedding:
    model_id = "test-embedding"

    def embed_documents(
        self,
        texts: tuple[str, ...],
        *,
        batch_size: int,
    ) -> tuple[tuple[float, ...], ...]:
        del batch_size
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        lowered = text.casefold()
        return (1.0, 0.0) if "mart" in lowered or "curated" in lowered else (0.0, 1.0)
