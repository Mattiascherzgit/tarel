import json
import threading
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.focus.contracts import (
    FocusDocument,
    FocusFailure,
    FocusHop,
    FocusMember,
    FocusSource,
)
from tarel.focus.core import focus_graph_revision
from tarel.focus.store import FileFocusStore
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.contracts import (
    AnnotationEvidence,
    AnnotationProvenance,
    GraphAnnotation,
    GraphEdge,
)
from tarel.graph.store import FileGraphStore
from tarel.lineage.manual import add_manual_hop, add_manual_job, create_manual_lineage
from tarel.lineage.store import FileLineageStore
from tarel.ui.presentation import (
    browser_focus_catalog,
    browser_focus_selection,
    browser_graph,
)
from tarel.ui.server import TarelUIBackend, UIConfig, UIFailure, _Server
from tarel.workspaces.store import FileWorkspaceStore


class UIPresentationTests(TestCase):
    def test_focus_projection_combines_overlapping_report_paths(self) -> None:
        graph = _graph()
        first = _focus("executive-sales", graph, include_dimension=True)
        second = _focus("regional-sales", graph, include_dimension=True)

        catalog = browser_focus_catalog((second, first))
        selection = browser_focus_selection((second, first))

        self.assertEqual(
            [item["name"] for item in catalog],
            ["executive-sales", "regional-sales"],
        )
        self.assertEqual(len(selection["object_ids"]), 2)
        self.assertEqual(len(selection["edges"]), 1)
        self.assertEqual(
            selection["edges"][0]["focuses"],
            ["executive-sales", "regional-sales"],
        )
        fact = next(
            item for item in selection["members"] if item["reference"] == "DemoDW.mart.FactSales"
        )
        self.assertEqual(fact["focuses"], ["executive-sales", "regional-sales"])

    def test_browser_projection_nests_fields_and_keeps_only_object_relationships(self) -> None:
        graph = _graph()

        payload = browser_graph(graph, editable=True, lineage_names=("sales-etl",))

        self.assertEqual(payload["graph"], "sales")
        self.assertTrue(payload["editable"])
        self.assertEqual(payload["lineages"], ["sales-etl"])
        self.assertEqual(
            [item["label"] for item in payload["objects"]],
            ["mart.DimDate", "mart.FactSales"],
        )
        fact = next(item for item in payload["objects"] if item["label"] == "mart.FactSales")
        self.assertEqual([item["label"] for item in fact["fields"]], ["DateKey", "SalesAmount"])
        self.assertEqual(fact["annotation"]["description"], "Sales by transaction line.")
        field_annotation = fact["fields"][0]["annotation"]
        self.assertEqual(field_annotation["description"], "Meaning of DateKey.")
        self.assertEqual(field_annotation["confidence"], 0.9)
        self.assertEqual(
            field_annotation["confidence_reason"],
            "Names and schema structure are explicit.",
        )
        self.assertEqual(field_annotation["evidence"][0]["source"], "object_name")
        self.assertEqual(field_annotation["provenance"]["source"], "agent")
        self.assertEqual(field_annotation["synonyms"], [])
        self.assertEqual(field_annotation["warnings"], [])
        self.assertEqual(len(payload["edges"]), 1)

    def test_browser_assets_render_expandable_complete_field_annotations(self) -> None:
        static = Path(__file__).parents[1] / "src/tarel/ui/static"
        application = (static / "app.js").read_text(encoding="utf-8")
        styles = (static / "styles.css").read_text(encoding="utf-8")

        for marker in (
            "fieldAnnotationCard",
            "Confidence reason",
            "Human review",
            "Context documents",
            "fieldEvidence",
            "Imported source semantics",
        ):
            self.assertIn(marker, application)
        self.assertIn(".field-card", styles)
        self.assertIn(".field-evidence", styles)

    def test_cli_reports_an_invalid_ui_port_without_a_traceback(self) -> None:
        errors = StringIO()
        with redirect_stderr(errors):
            exit_code = main(["ui", "sales", "--port", "70000", "--no-open"])

        self.assertEqual(exit_code, 2)
        self.assertIn("error [invalid_port]", errors.getvalue())

    def test_cli_accepts_multiple_initial_focuses(self) -> None:
        with patch("tarel.ui.server.run_ui", return_value=0) as run:
            exit_code = main(
                [
                    "ui",
                    "sales",
                    "--focus",
                    "executive-sales",
                    "--focus",
                    "regional-sales",
                    "--no-open",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            run.call_args.kwargs["focuses"],
            ("executive-sales", "regional-sales"),
        )

    def test_browser_projection_includes_manual_jobs_and_reviewable_hops(self) -> None:
        document, definition = add_manual_job(
            create_manual_lineage("sales-manual"),
            kind="procedure",
            name="LoadSales",
            qualified_name="etl.LoadSales",
            language="tsql",
            source_reference="runbook:load-sales",
            description="Loads sales into the mart.",
        )
        document, hop = add_manual_hop(
            document,
            job_reference=definition.id,
            source="mart.StageSales",
            target="mart.FactSales",
            operation="insert",
            role="business_data",
            evidence_reference="runbook:load-sales",
            reason="Confirmed by the warehouse owner.",
        )

        payload = browser_graph(_graph(), lineage_documents=(document,), editable=True)

        self.assertEqual(payload["lineages"], ["sales-manual"])
        manual = payload["lineage_documents"][0]
        self.assertTrue(manual["manual"])
        self.assertEqual(manual["jobs"][0]["description"], "Loads sales into the mart.")
        self.assertEqual(manual["hops"][0]["item_id"], hop.id)
        self.assertEqual(manual["hops"][0]["state"], "draft")
        flows = payload["lineage_flows"]
        self.assertEqual(len(flows["edges"]), 2)
        self.assertEqual(
            {item["relation"] for item in flows["edges"]},
            {"business_data", "insert"},
        )
        self.assertTrue(any(item["kind"] == "procedure" for item in flows["nodes"]))


class UIBackendTests(TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        root = Path(self.temporary.name)
        self.graph_store = FileGraphStore(root / "graphs")
        self.workspace_store = FileWorkspaceStore(root / "workspaces")
        self.lineage_store = FileLineageStore(root / "lineage")
        self.focus_store = FileFocusStore(root / "focus")
        self.graph_store.save(_graph())
        self.stack = ExitStack()
        self.stack.enter_context(
            patch("tarel.application.FileGraphStore", return_value=self.graph_store)
        )
        self.stack.enter_context(
            patch("tarel.application.FileWorkspaceStore", return_value=self.workspace_store)
        )
        self.stack.enter_context(
            patch("tarel.application.FileFocusStore", return_value=self.focus_store)
        )
        self.stack.enter_context(
            patch("tarel.lineage.application.FileLineageStore", return_value=self.lineage_store)
        )
        self.stack.enter_context(
            patch("tarel.lineage.application.FileGraphStore", return_value=self.graph_store)
        )
        self.backend = TarelUIBackend(UIConfig("sales", editable=True))

    def tearDown(self) -> None:
        self.stack.close()
        self.temporary.cleanup()

    def test_edit_validate_and_stale_revision_protection(self) -> None:
        before = self.backend.bootstrap()
        edited = self.backend.mutate(
            "/api/annotation/edit",
            {
                "patch": {
                    "description": "Reviewed internet sales transaction lines.",
                    "grain": "One row per internet sales transaction line.",
                },
                "reason": "Confirmed with the warehouse owner.",
                "reference": "mart.FactSales",
                "revision": before["revision"],
            },
        )
        decided = self.backend.mutate(
            "/api/annotation/decision",
            {
                "include_fields": True,
                "reason": "Description and field meanings approved.",
                "reference": "mart.FactSales",
                "revision": edited["revision"],
                "state": "validated",
            },
        )

        self.assertEqual(decided["records"][0]["annotation"]["state"], "validated")
        stored = self.graph_store.load("sales")
        reviewed = [node for node in stored.nodes if node.annotation is not None]
        self.assertTrue(all(node.annotation.state == "validated" for node in reviewed))
        self.assertEqual(
            next(
                node for node in stored.nodes if node.label == "mart.FactSales"
            ).annotation.description,
            "Reviewed internet sales transaction lines.",
        )

        with self.assertRaises(UIFailure) as raised:
            self.backend.mutate(
                "/api/annotation/decision",
                {
                    "include_fields": False,
                    "reason": "Stale browser tab.",
                    "reference": "mart.FactSales",
                    "revision": before["revision"],
                    "state": "rejected",
                },
            )
        self.assertEqual(raised.exception.status, 409)

    def test_hundreds_of_focuses_can_be_listed_but_only_selected_paths_are_sent(self) -> None:
        graph = self.graph_store.load("sales")
        for number in range(200):
            self.focus_store.save(
                _focus(f"report-{number:03d}", graph, include_dimension=number % 2 == 0)
            )

        payload = self.backend.bootstrap()
        selected = self.backend.mutate(
            "/api/focus/select",
            {"focuses": ["report-003", "report-004"]},
        )

        self.assertEqual(len(payload["focuses"]), 200)
        self.assertIsNone(payload["focus_selection"])
        self.assertEqual(selected["focuses"], ["report-003", "report-004"])
        self.assertEqual(len(selected["object_ids"]), 2)
        self.assertLess(len(json.dumps(selected)), len(json.dumps(payload)))

    def test_stale_focus_is_visible_but_cannot_be_selected(self) -> None:
        graph = self.graph_store.load("sales")
        self.focus_store.save(_focus("stale-report", graph, include_dimension=True))
        self.graph_store.save(
            replace(
                graph,
                edges=(
                    *graph.edges,
                    GraphEdge(
                        id="new-structural-edge",
                        source_id=graph.edges[0].source_id,
                        target_id=graph.edges[0].target_id,
                        type="foreign_key",
                        metadata={},
                    ),
                ),
            )
        )

        catalog = self.backend.bootstrap()["focuses"]
        with self.assertRaises(FocusFailure) as raised:
            self.backend.mutate("/api/focus/select", {"focuses": ["stale-report"]})

        self.assertFalse(catalog[0]["current"])
        self.assertIn("stale", catalog[0]["stale_reason"])
        self.assertEqual(raised.exception.code, "focus_stale")

    def test_zone_creation_bootstraps_workspace_and_supports_later_drag_add(self) -> None:
        created = self.backend.mutate(
            "/api/zone/save",
            {
                "area": "warehouse",
                "description": "Curated sales objects.",
                "members": ["mart.FactSales"],
                "system": "analytics",
                "workspace": "demo",
                "zone": "sales",
            },
        )
        updated = self.backend.mutate(
            "/api/zone/save",
            {
                "area": "warehouse",
                "description": "Curated sales objects.",
                "members": ["mart.FactSales", "mart.DimDate"],
                "system": "analytics",
                "workspace": "demo",
                "workspace_revision": created["workspace_revision"],
                "zone": "sales",
            },
        )

        zone = updated["workspace"]["systems"][0]["zones"][0]
        self.assertEqual(len(zone["members"]), 2)
        bootstrap = self.backend.bootstrap()
        self.assertEqual(bootstrap["workspaces"][0]["systems"][0]["zones"][0]["name"], "sales")

    def test_http_adapter_serves_assets_and_blocks_missing_session_token(self) -> None:
        server = _Server(("127.0.0.1", 0), self.backend, "test-token")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(f"{base}/", timeout=3) as response:
                html = response.read().decode("utf-8")
                self.assertIn('content="test-token"', html)
                self.assertIn("Show path on canvas", html)
                self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
            with urlopen(f"{base}/api/bootstrap", timeout=3) as response:
                payload = json.load(response)
                self.assertEqual(payload["graph"], "sales")
            request = Request(
                f"{base}/api/annotation/decision",
                data=b"{}",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(HTTPError) as raised:
                urlopen(request, timeout=3)
            self.assertEqual(raised.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_workspace_bootstrap_qualifies_ids_across_multiple_graphs(self) -> None:
        self.graph_store.save(replace(_graph(), name="sales-copy"))
        errors = StringIO()
        output = StringIO()
        with redirect_stderr(errors), redirect_stdout(output):
            self.assertEqual(main(["workspace", "create", "enterprise"]), 0)
            self.assertEqual(
                main(
                    [
                        "workspace",
                        "system",
                        "define",
                        "enterprise",
                        "analytics",
                        "--graph",
                        "sales",
                        "--graph",
                        "sales-copy",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "workspace",
                        "relationship",
                        "add",
                        "enterprise",
                        "--from",
                        "sales:mart.FactSales.DateKey",
                        "--to",
                        "sales-copy:mart.DimDate.DateKey",
                        "--reason",
                        "Shared date dimension across graph snapshots.",
                        "--validated",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "workspace",
                        "area",
                        "define",
                        "enterprise",
                        "analytics",
                        "warehouse",
                        "--schema",
                        "sales:mart",
                        "--schema",
                        "sales-copy:mart",
                    ]
                ),
                0,
            )
        self.assertEqual(errors.getvalue(), "")

        payload = TarelUIBackend(UIConfig(workspace="enterprise")).bootstrap()

        self.assertEqual([item["name"] for item in payload["graphs"]], ["sales", "sales-copy"])
        self.assertEqual(len(payload["objects"]), 4)
        self.assertEqual(len({item["id"] for item in payload["objects"]}), 4)
        self.assertTrue(all("::object:" in item["id"] for item in payload["objects"]))
        self.assertEqual(payload["scope"]["workspace"], "enterprise")
        self.assertEqual(len(payload["revision"]), 64)
        cross_graph = [item for item in payload["edges"] if item["graph"] is None]
        self.assertEqual(len(cross_graph), 1)
        self.assertEqual(cross_graph[0]["metadata"]["state"], "validated")

    def test_create_manual_job_hop_and_review_it_from_the_ui(self) -> None:
        job_result = self.backend.mutate(
            "/api/manual/job",
            {
                "description": "Loads staged sales into the fact table.",
                "job_name": "LoadFactSales",
                "kind": "procedure",
                "language": "tsql",
                "lineage": "sales-manual",
                "qualified_name": "etl.LoadFactSales",
                "revision": None,
                "source_reference": "runbook:sales-load",
            },
        )
        self.assertEqual(job_result["lineage"]["jobs"][0]["qualified_name"], "etl.LoadFactSales")
        self.assertEqual(self.backend.bootstrap()["lineages"], ["sales-manual"])

        hop_result = self.backend.mutate(
            "/api/manual/hop",
            {
                "evidence_reference": "runbook:sales-load",
                "job": "etl.LoadFactSales",
                "line_end": 24,
                "line_start": 18,
                "lineage": "sales-manual",
                "operation": "insert",
                "reason": "Confirmed against the reviewed loading runbook.",
                "revision": job_result["lineage"]["revision"],
                "role": "business_data",
                "source": "mart.DimDate",
                "target": "mart.FactSales",
            },
        )
        self.assertEqual(hop_result["item"]["state"], "draft")

        decided = self.backend.mutate(
            "/api/lineage/decision",
            {
                "decision": "validate",
                "item_id": hop_result["item"]["id"],
                "lineage": "sales-manual",
                "reason": "Reviewed with the data owner.",
                "revision": hop_result["revision"],
            },
        )
        self.assertEqual(decided["item"]["state"], "validated")
        bootstrap = self.backend.bootstrap()
        self.assertEqual(bootstrap["lineage_documents"][0]["hops"][0]["state"], "validated")

        trace = self.backend.mutate(
            "/api/lineage/upstream",
            {
                "lineages": ["sales-manual"],
                "reference": "mart.FactSales",
                "states": ["validated"],
            },
        )
        self.assertEqual(trace["origins"][0]["reference"], "DemoDW.mart.DimDate")


def _graph():
    graph = build_graph_from_catalog(
        "sales",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="DemoDW",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="mart",
                    name="FactSales",
                    kind="table",
                    fields=(
                        CatalogField("DateKey", 1, "integer", False),
                        CatalogField("SalesAmount", 2, "decimal(18,2)", False),
                    ),
                ),
                CatalogObject(
                    namespace="mart",
                    name="DimDate",
                    kind="table",
                    fields=(CatalogField("DateKey", 1, "integer", False),),
                ),
            ),
        ),
    )
    object_id = next(node.id for node in graph.nodes if node.label == "mart.FactSales")
    annotated = []
    for node in graph.nodes:
        if node.id == object_id:
            annotated.append(
                replace(
                    node,
                    metadata={**node.metadata, "grain": "One row per sales transaction line."},
                    annotation=_annotation("Sales by transaction line."),
                )
            )
        elif node.type == "field" and node.metadata.get("object_id") == object_id:
            annotated.append(replace(node, annotation=_annotation(f"Meaning of {node.label}.")))
        else:
            annotated.append(node)
    graph = replace(graph, nodes=tuple(annotated))
    fact = next(node for node in graph.nodes if node.label == "mart.FactSales")
    date = next(node for node in graph.nodes if node.label == "mart.DimDate")
    return replace(
        graph,
        edges=(
            *graph.edges,
            GraphEdge(
                id="relationship",
                source_id=fact.id,
                target_id=date.id,
                type="foreign_key",
                metadata={"from_fields": ["DateKey"], "to_fields": ["DateKey"]},
            ),
        ),
    )


def _annotation(description: str) -> GraphAnnotation:
    return GraphAnnotation(
        description=description,
        confidence=0.9,
        confidence_reason="Names and schema structure are explicit.",
        evidence=(AnnotationEvidence(source="object_name", reference="fixture"),),
        provenance=AnnotationProvenance(source="agent"),
    )


def _focus(
    name: str,
    graph,
    *,
    include_dimension: bool,
) -> FocusDocument:
    fact = next(item for item in graph.nodes if item.label == "mart.FactSales")
    dimension = next(item for item in graph.nodes if item.label == "mart.DimDate")

    def member(node, *, depth: int, origin: bool) -> FocusMember:
        return FocusMember(
            id=f"graph:{graph.name}:{node.id}",
            reference=f"{graph.catalog}.{node.label}",
            name=str(node.metadata.get("name") or node.label),
            kind=node.type,
            source=f"graph:{graph.name}",
            depth=depth,
            reasons=("seed",) if depth == 0 else ("upstream:reads_from",),
            origin=origin,
            annotation_state=node.annotation.state if node.annotation else None,
        )

    fact_member = member(fact, depth=0, origin=not include_dimension)
    dimension_member = member(dimension, depth=1, origin=True)
    members = (fact_member, dimension_member) if include_dimension else (fact_member,)
    hops = (
        (
            FocusHop(
                id="dimension-to-fact",
                depth=1,
                source_id=dimension_member.id,
                target_id=fact_member.id,
                relation="reads_from",
                state="validated",
                lineage=None,
            ),
        )
        if include_dimension
        else ()
    )
    return FocusDocument(
        name=name,
        seed=fact_member.reference,
        seed_id=fact_member.id,
        max_hops=12,
        states=("validated",),
        sources=(FocusSource("graph", graph.name, focus_graph_revision(graph)),),
        members=members,
        hops=hops,
        warnings=(),
        truncated=False,
    )
