import json
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.cli import main
from tarel.connectors.catalog import catalog_result_from_dict
from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogResult,
    ConnectorFailure,
)
from tarel.graph.contracts import GraphFailure
from tarel.sdk import Tarel


class CatalogImportTests(TestCase):
    def test_serialized_catalog_round_trips_strictly(self) -> None:
        catalog = _catalog()

        loaded = catalog_result_from_dict(catalog.to_dict())

        self.assertEqual(loaded, catalog)

    def test_serialized_catalog_rejects_unknown_fields(self) -> None:
        payload = _catalog().to_dict()
        payload["connection_url"] = "must-not-be-accepted"

        with self.assertRaises(ConnectorFailure) as raised:
            catalog_result_from_dict(payload)

        self.assertEqual(raised.exception.code, "invalid_catalog")

    def test_sdk_imports_without_discovery_and_refuses_overwrite(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / "state"
            sdk = Tarel(root)

            imported = sdk.graph.import_catalog("observed", _catalog())
            before = imported.path.read_bytes()
            with self.assertRaises(GraphFailure) as raised:
                sdk.graph.import_catalog(
                    "observed",
                    replace(_catalog(), catalog="OtherCatalog"),
                )
            after = imported.path.read_bytes()
            loaded = sdk.graph.load("observed")

        self.assertEqual(raised.exception.code, "graph_exists")
        self.assertEqual(before, after)
        self.assertEqual(imported.graph, loaded)
        self.assertEqual(imported.path, root / "graphs/observed/graph.json")

    def test_sdk_rejects_invalid_relationship_without_persisting_a_graph(self) -> None:
        catalog = replace(
            _catalog(),
            relationships=(
                replace(_catalog().relationships[0], from_fields=("missing",)),
            ),
        )
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")

            with self.assertRaises(ConnectorFailure) as raised:
                sdk.graph.import_catalog("invalid", catalog)

            self.assertEqual(sdk.graph.list(), ())

        self.assertEqual(raised.exception.code, "invalid_catalog")

    def test_cli_imports_connector_discovery_json_through_the_same_state(self) -> None:
        previous = Path.cwd()
        output = StringIO()
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            project = Path(temporary_directory)
            source = project / "catalog.json"
            source.write_text(json.dumps(_catalog().to_dict()), encoding="utf-8")
            os.chdir(project)
            try:
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "graph",
                            "import-catalog",
                            "cli-observed",
                            "--source",
                            str(source),
                            "--format",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous)
            loaded = Tarel(project / ".tarel").graph.load("cli-observed")

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["name"], "cli-observed")
        self.assertEqual(payload["nodes"], 8)
        self.assertEqual(loaded.catalog, "ObservedDW")

    def test_cli_reports_invalid_catalog_without_creating_state(self) -> None:
        previous = Path.cwd()
        error = StringIO()
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            project = Path(temporary_directory)
            source = project / "catalog.json"
            source.write_text('{"status":"ok"}', encoding="utf-8")
            os.chdir(project)
            try:
                with redirect_stderr(error):
                    exit_code = main(
                        [
                            "graph",
                            "import-catalog",
                            "invalid",
                            "--source",
                            str(source),
                        ]
                    )
            finally:
                os.chdir(previous)

            self.assertFalse((project / ".tarel").exists())

        self.assertEqual(exit_code, 2)
        self.assertIn("error [invalid_catalog]", error.getvalue())


def _catalog() -> CatalogResult:
    return CatalogResult(
        connector="embedded-observer",
        source_type="sql",
        catalog="ObservedDW",
        dialect="duckdb",
        objects=(
            CatalogObject(
                namespace="main",
                name="customers",
                kind="table",
                fields=(
                    CatalogField("customer_id", 1, "INTEGER", False, is_primary_key=True),
                    CatalogField("display_name", 2, "VARCHAR", True),
                ),
                primary_key=("customer_id",),
            ),
            CatalogObject(
                namespace="main",
                name="orders",
                kind="table",
                fields=(
                    CatalogField("order_id", 1, "INTEGER", False, is_primary_key=True),
                    CatalogField("customer_id", 2, "INTEGER", False),
                ),
                primary_key=("order_id",),
            ),
        ),
        relationships=(
            CatalogRelationship(
                name="fk_orders_customer",
                from_namespace="main",
                from_object="orders",
                from_fields=("customer_id",),
                to_namespace="main",
                to_object="customers",
                to_fields=("customer_id",),
            ),
        ),
    )
