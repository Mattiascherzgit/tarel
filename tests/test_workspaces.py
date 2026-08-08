import json
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.store import FileGraphStore
from tarel.workspaces.contracts import WorkspaceDocument, WorkspaceFailure
from tarel.workspaces.store import FileWorkspaceStore


class WorkspaceTests(TestCase):
    def test_schema_belongs_to_only_one_area(self) -> None:
        with self.assertRaises(WorkspaceFailure):
            WorkspaceDocument.from_dict(
                {
                    "contract_version": "tarel.workspace.v0.1",
                    "description": None,
                    "name": "demo",
                    "systems": [
                        {
                            "areas": [
                                {
                                    "description": None,
                                    "name": "finance",
                                    "schemas": [
                                        {"graph": "warehouse", "namespace": "sales"}
                                    ],
                                },
                                {
                                    "description": None,
                                    "name": "sales",
                                    "schemas": [
                                        {"graph": "warehouse", "namespace": "sales"}
                                    ],
                                },
                            ],
                            "description": None,
                            "graphs": ["warehouse"],
                            "name": "analytics",
                            "zones": [],
                        }
                    ],
                }
            )

    def test_cli_defines_cross_area_and_overlapping_zones(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            graph_store = FileGraphStore(root / "graphs")
            workspace_store = FileWorkspaceStore(root / "workspaces")
            graph_store.save(_graph("warehouse", "sales", "FactSales", "DimCustomer"))
            graph_store.save(_graph("erp", "public", "Orders"))

            with (
                patch("tarel.application.FileGraphStore", return_value=graph_store),
                patch("tarel.application.FileWorkspaceStore", return_value=workspace_store),
            ):
                self._run("workspace", "create", "enterprise")
                self._run(
                    "workspace",
                    "system",
                    "define",
                    "enterprise",
                    "commercial",
                    "--graph",
                    "warehouse",
                    "--graph",
                    "erp",
                )
                self._run(
                    "workspace",
                    "area",
                    "define",
                    "enterprise",
                    "commercial",
                    "analytics",
                    "--schema",
                    "warehouse:sales",
                )
                self._run(
                    "workspace",
                    "area",
                    "define",
                    "enterprise",
                    "commercial",
                    "operations",
                    "--schema",
                    "erp:public",
                )
                self._run(
                    "workspace",
                    "zone",
                    "define",
                    "enterprise",
                    "commercial",
                    "revenue",
                    "--object",
                    "warehouse:sales.FactSales",
                    "--object",
                    "erp:public.Orders",
                )
                self._run(
                    "workspace",
                    "zone",
                    "define",
                    "enterprise",
                    "commercial",
                    "customer-journey",
                    "--object",
                    "warehouse:sales.DimCustomer",
                    "--object",
                    "erp:public.Orders",
                )
                output = self._run(
                    "workspace",
                    "zone",
                    "show",
                    "enterprise",
                    "commercial",
                    "revenue",
                    "--format",
                    "json",
                )

            payload = json.loads(output)
            self.assertEqual(
                [(item["graph"], item["area"]) for item in payload["objects"]],
                [("erp", "operations"), ("warehouse", "analytics")],
            )
            workspace = workspace_store.load("enterprise")
            system = workspace.systems[0]
            orders_id = next(
                member.object_id
                for zone in system.zones
                for member in zone.members
                if member.graph == "erp"
            )
            self.assertEqual(
                sum(
                    member.graph == "erp" and member.object_id == orders_id
                    for zone in system.zones
                    for member in zone.members
                ),
                2,
            )

            with (
                patch("tarel.application.FileGraphStore", return_value=graph_store),
                patch("tarel.application.FileWorkspaceStore", return_value=workspace_store),
            ):
                scoped = json.loads(
                    self._run(
                        "workspace",
                        "scope",
                        "enterprise",
                        "--system",
                        "commercial",
                        "--zone",
                        "revenue",
                        "--format",
                        "json",
                    )
                )
            self.assertEqual(scoped["graphs"], ["erp", "warehouse"])
            self.assertEqual(
                [(item["graph"], item["label"]) for item in scoped["objects"]],
                [("warehouse", "sales.FactSales"), ("erp", "public.Orders")],
            )
            self.assertEqual(len(scoped["scope_hash"]), 64)

    def _run(self, *args: str) -> str:
        output = StringIO()
        errors = StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            exit_code = main(list(args))
        self.assertEqual(exit_code, 0, errors.getvalue())
        return output.getvalue()


def _graph(name: str, namespace: str, *objects: str):
    return build_graph_from_catalog(
        name,
        CatalogResult(
            connector="test",
            source_type="database",
            catalog=name,
            dialect="ansi",
            objects=tuple(
                CatalogObject(
                    namespace=namespace,
                    name=object_name,
                    kind="table",
                    fields=(CatalogField("Id", 1, "integer", False),),
                )
                for object_name in objects
            ),
        ),
    )
