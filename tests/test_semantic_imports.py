import json
import os
from contextlib import redirect_stderr, redirect_stdout
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
from tarel.graph.build import build_graph_from_catalog
from tarel.runtime import TarelRuntime
from tarel.sdk import Tarel
from tarel.semantics.application import (
    edit_semantic_source_use_case,
    import_semantic_use_case,
)
from tarel.semantics.contracts import (
    SemanticFailure,
    SemanticImportDocument,
    SourceSnapshot,
    semantic_target_values,
    validate_semantic_import,
)
from tarel.semantics.ossie import read_ossie_import
from tarel.ui.presentation import browser_graph
from tarel.ui.server import TarelUIBackend, UIConfig


class SemanticImportTests(TestCase):
    def test_ossie_snapshot_normalization_and_graph_bindings_are_explicit(self) -> None:
        graph = _graph()
        content = _source()

        document = read_ossie_import(
            "sales-model",
            graph=graph,
            content=content,
            media_type="application/json",
        )

        model = document.models[0]
        fact = model.datasets[0]
        relationship = model.relationships[0]
        self.assertTrue(document.complete)
        self.assertEqual(document.snapshot.content, content)
        self.assertEqual(fact.graph_node_id, "object:DemoDW/mart/FactSales")
        self.assertEqual(
            [field.graph_node_id for field in fact.fields],
            [
                "field:DemoDW/mart/FactSales/DateKey",
                "field:DemoDW/mart/FactSales/SalesAmount",
            ],
        )
        self.assertEqual(
            relationship.graph_edge_id,
            "foreign_key:DemoDW/mart/FactSales/fk_sales_date",
        )
        self.assertEqual(
            [item.code for item in document.diagnostics],
            ["preserved_unknown_construct", "preserved_unknown_construct"],
        )
        self.assertTrue(
            any(
                item.source_reference.endswith("/ai_context/instructions")
                for item in document.diagnostics
            )
        )

    def test_ontology_is_preserved_but_reported_as_incomplete(self) -> None:
        content = json.dumps(
            {
                "version": "0.2.0.dev0",
                "ontology": [{"concept": "Revenue", "type": "ValueType"}],
            }
        )

        document = read_ossie_import(
            "ontology",
            graph=_graph(),
            content=content,
            media_type="application/json",
        )

        self.assertFalse(document.complete)
        self.assertEqual(document.models, ())
        self.assertEqual(document.snapshot.content, content)
        self.assertEqual(document.diagnostics[0].code, "unsupported_ossie_ontology")

    def test_duplicate_json_keys_fail_instead_of_silently_overwriting_source(self) -> None:
        content = '{"version":"0.2.0.dev0","version":"changed","semantic_model":[]}'

        with self.assertRaises(SemanticFailure) as raised:
            read_ossie_import(
                "duplicate",
                graph=_graph(),
                content=content,
                media_type="application/json",
            )

        self.assertEqual(raised.exception.code, "invalid_ossie")

    def test_reimport_is_idempotent_and_source_edits_do_not_change_snapshot(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = TarelRuntime.local(root / "state")
            runtime.graph_store().save(_graph())
            source = root / "model.json"
            source.write_text(_source(), encoding="utf-8")

            first = import_semantic_use_case(
                "sales-model",
                graph_name="sales",
                source_path=source,
                runtime=runtime,
            )
            second = import_semantic_use_case(
                "sales-model",
                graph_name="sales",
                source_path=source,
                runtime=runtime,
            )
            target_id = first.document.models[0].datasets[0].id
            edited = edit_semantic_source_use_case(
                "sales-model",
                target_id,
                {"description": "Reviewed sales fact.", "synonyms": ["sales"]},
                reason="Confirmed by the analytics owner.",
                expected_revision=second.document.revision,
                runtime=runtime,
            )

            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertEqual(edited.document.snapshot, first.document.snapshot)
            self.assertEqual(
                semantic_target_values(edited.document, target_id),
                ("Reviewed sales fact.", ("sales",)),
            )
            self.assertEqual(edited.document.edits[0].reason, "Confirmed by the analytics owner.")

    def test_file_import_preserves_crlf_source_text_exactly(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = TarelRuntime.local(root / "state")
            runtime.graph_store().save(_graph())
            exact_content = _source().replace("\n", "\r\n")
            source = root / "model.json"
            source.write_bytes(exact_content.encode("utf-8"))

            imported = import_semantic_use_case(
                "sales-model",
                graph_name="sales",
                source_path=source,
                runtime=runtime,
            )

        self.assertEqual(imported.document.snapshot.content, exact_content)

    def test_same_snapshot_cannot_change_reader_without_explicit_replace(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = TarelRuntime.local(root / "state")
            runtime.graph_store().save(_graph())
            source = root / "model.yaml"
            source.write_text(
                json.dumps(
                    {
                        "version": "0.2.0.dev0",
                        "semantic_model": [],
                        "cubes": [],
                    }
                ),
                encoding="utf-8",
            )
            import_semantic_use_case(
                "shared-name",
                graph_name="sales",
                source_path=source,
                format_name="ossie",
                runtime=runtime,
            )

            with self.assertRaises(SemanticFailure) as raised:
                import_semantic_use_case(
                    "shared-name",
                    graph_name="sales",
                    source_path=source,
                    format_name="cube",
                    runtime=runtime,
                )

        self.assertEqual(raised.exception.code, "semantic_import_exists")

    def test_persisted_contract_is_not_hard_coded_to_exercised_readers(self) -> None:
        document = SemanticImportDocument(
            name="future-model",
            graph_name="sales",
            format_name="vendor.experimental",
            format_version="1",
            snapshot=SourceSnapshot.from_content("{}", media_type="application/json"),
            models=(),
        )

        validate_semantic_import(document)
        self.assertEqual(
            SemanticImportDocument.from_dict(document.to_dict()).format_name,
            "vendor.experimental",
        )

    def test_replace_refuses_to_discard_source_edits(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = TarelRuntime.local(root / "state")
            runtime.graph_store().save(_graph())
            source = root / "model.json"
            source.write_text(_source(), encoding="utf-8")
            imported = import_semantic_use_case(
                "sales-model",
                graph_name="sales",
                source_path=source,
                runtime=runtime,
            )
            edit_semantic_source_use_case(
                "sales-model",
                imported.document.models[0].datasets[0].id,
                {"description": "Reviewed."},
                reason="Owner review.",
                runtime=runtime,
            )
            changed = json.loads(_source())
            changed["semantic_model"][0]["description"] = "New source revision."
            source.write_text(json.dumps(changed), encoding="utf-8")

            with self.assertRaises(SemanticFailure) as raised:
                import_semantic_use_case(
                    "sales-model",
                    graph_name="sales",
                    source_path=source,
                    replace_existing=True,
                    runtime=runtime,
                )

        self.assertEqual(raised.exception.code, "semantic_import_has_edits")

    def test_browser_projection_keeps_tarel_and_source_semantics_separate(self) -> None:
        document = read_ossie_import(
            "sales-model",
            graph=_graph(),
            content=_source(),
            media_type="application/json",
        )

        payload = browser_graph(_graph(), semantic_imports=(document,))

        fact = next(item for item in payload["objects"] if item["label"] == "mart.FactSales")
        self.assertIsNone(fact["annotation"])
        self.assertEqual(fact["source_semantics"][0]["import_name"], "sales-model")
        self.assertEqual(fact["source_semantics"][0]["description"], "Sales facts.")
        self.assertEqual(
            fact["fields"][0]["source_semantics"][0]["description"],
            "Date dimension key.",
        )
        self.assertEqual(payload["semantic_imports"][0]["source_sha256"], document.snapshot.sha256)
        relationship = next(
            item
            for item in payload["edges"]
            if item["type"] == "foreign_key" and item["source_semantics"]
        )
        self.assertEqual(relationship["source_semantics"][0]["name"], "sales_to_date")
        model_dataset = payload["semantic_models"][0]["datasets"][0]
        self.assertEqual(model_dataset["graph_node_id"], fact["object_id"])
        self.assertEqual(len(model_dataset["fields"]), 2)
        self.assertNotIn("content", json.dumps(payload))

    def test_cli_import_writes_to_the_selected_project_state(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            project = Path(temporary_directory)
            runtime = TarelRuntime.local(project / ".tarel")
            runtime.graph_store().save(_graph())
            source = project / "model.json"
            source.write_text(_source(), encoding="utf-8")
            output = StringIO()
            errors = StringIO()
            os.chdir(project)
            try:
                with redirect_stdout(output), redirect_stderr(errors):
                    exit_code = main(
                        [
                            "semantic",
                            "import",
                            "sales-model",
                            "--graph",
                            "sales",
                            "--source",
                            str(source),
                            "--output",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous)

        self.assertEqual(exit_code, 0)
        self.assertEqual(errors.getvalue(), "")
        self.assertTrue(json.loads(output.getvalue())["complete"])

    def test_sdk_import_and_view_share_the_explicit_runtime(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = Tarel(root / "state")
            sdk.runtime.graph_store().save(_graph())
            source = root / "model.json"
            source.write_text(_source(), encoding="utf-8")

            imported = sdk.semantic.import_file(
                "sales-model",
                graph="sales",
                source=source,
            )
            payload = sdk.view.graph("sales", editable=True)
            import_names = tuple(item.name for item in sdk.semantic.list(graph="sales"))

        self.assertTrue(imported.document.complete)
        self.assertEqual(import_names, ("sales-model",))
        fact = next(item for item in payload["objects"] if item["label"] == "mart.FactSales")
        self.assertEqual(fact["source_semantics"][0]["import_name"], "sales-model")

    def test_ui_source_edit_requires_revision_and_preserves_original_values(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = TarelRuntime.local(root / "state")
            runtime.graph_store().save(_graph())
            source = root / "model.json"
            source.write_text(_source(), encoding="utf-8")
            imported = import_semantic_use_case(
                "sales-model",
                graph_name="sales",
                source_path=source,
                runtime=runtime,
            )
            target = imported.document.models[0].datasets[0]
            backend = TarelUIBackend(UIConfig("sales", editable=True))
            with (
                patch(
                    "tarel.semantics.application.FileSemanticImportStore",
                    return_value=runtime.semantic_import_store(),
                ),
                patch(
                    "tarel.application.FileGraphStore",
                    return_value=runtime.graph_store(),
                ),
            ):
                result = backend.mutate(
                    "/api/semantic/edit",
                    {
                        "import_name": "sales-model",
                        "patch": {
                            "description": "Reviewed sales facts.",
                            "synonyms": ["sales ledger"],
                        },
                        "reason": "Reviewed in the GUI.",
                        "revision": imported.document.revision,
                        "target_id": target.id,
                    },
                )
                stored = runtime.semantic_import_store().load("sales-model")

        self.assertEqual(result["revision"], stored.revision)
        self.assertEqual(target.description, "Sales facts.")
        self.assertEqual(
            semantic_target_values(stored, target.id),
            ("Reviewed sales facts.", ("sales ledger",)),
        )

    def test_ossie_sml_and_cube_share_one_experimental_import_contract(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            runtime = TarelRuntime.local(root / "state")
            runtime.graph_store().save(_graph())
            ossie_path = root / "ossie.json"
            ossie_path.write_text(_source(), encoding="utf-8")
            sml_path = root / "sml"
            cube_path = root / "cube"
            _write_project(sml_path, _sml_source_files())
            _write_project(cube_path, _cube_source_files())

            ossie = import_semantic_use_case(
                "contract-ossie",
                graph_name="sales",
                source_path=ossie_path,
                format_name="ossie",
                runtime=runtime,
            ).document
            sml = import_semantic_use_case(
                "contract-sml",
                graph_name="sales",
                source_path=sml_path,
                format_name="sml",
                runtime=runtime,
            ).document
            cube = import_semantic_use_case(
                "contract-cube",
                graph_name="sales",
                source_path=cube_path,
                format_name="cube",
                runtime=runtime,
            ).document
            payload = Tarel(runtime.root).view.graph("sales", editable=True)

        self.assertEqual(
            {ossie.format_name, sml.format_name, cube.format_name},
            {"apache-ossie", "cube", "sml"},
        )
        self.assertTrue(all(item.complete for item in (ossie, sml, cube)))
        self.assertEqual(
            sml.snapshot.media_type,
            "application/vnd.tarel.semantic-source-bundle+json",
        )
        self.assertEqual(len(sml.models[0].datasets), 1)
        self.assertEqual(len(sml.models[0].metrics), 1)
        self.assertEqual(
            sml.models[0].datasets[0].graph_node_id,
            "object:DemoDW/mart/factinternetsales",
        )
        self.assertEqual(len(cube.models[0].datasets), 2)
        self.assertEqual(
            cube.models[0].relationships[0].graph_edge_id,
            "foreign_key:DemoDW/cube/orders/fk_orders_users",
        )
        self.assertEqual(
            {item["format_name"] for item in payload["semantic_imports"]},
            {"apache-ossie", "cube", "sml"},
        )
        self.assertNotIn("SELECT 1 as id", json.dumps(payload))


def _graph():
    return build_graph_from_catalog(
        "sales",
        CatalogResult(
            connector="fixture",
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
                    primary_key=("DateKey",),
                ),
                CatalogObject(
                    namespace="mart",
                    name="DimDate",
                    kind="table",
                    fields=(CatalogField("DateKey", 1, "integer", False),),
                    primary_key=("DateKey",),
                ),
                CatalogObject(
                    namespace="mart",
                    name="factinternetsales",
                    kind="table",
                    fields=(
                        CatalogField("orderdatekey", 1, "integer", False),
                        CatalogField("salesamount", 2, "decimal(18,2)", False),
                    ),
                ),
                CatalogObject(
                    namespace="cube",
                    name="orders",
                    kind="view",
                    fields=(
                        CatalogField("id", 1, "integer", False),
                        CatalogField("user_id", 2, "integer", False),
                        CatalogField("status", 3, "text", False),
                    ),
                ),
                CatalogObject(
                    namespace="cube",
                    name="users",
                    kind="view",
                    fields=(
                        CatalogField("id", 1, "integer", False),
                        CatalogField("full_name", 2, "text", False),
                        CatalogField("city", 3, "text", False),
                    ),
                ),
            ),
            relationships=(
                CatalogRelationship(
                    name="fk_sales_date",
                    from_namespace="mart",
                    from_object="FactSales",
                    from_fields=("DateKey",),
                    to_namespace="mart",
                    to_object="DimDate",
                    to_fields=("DateKey",),
                ),
                CatalogRelationship(
                    name="fk_orders_users",
                    from_namespace="cube",
                    from_object="orders",
                    from_fields=("user_id",),
                    to_namespace="cube",
                    to_object="users",
                    to_fields=("id",),
                ),
            ),
        ),
    )


def _source() -> str:
    return json.dumps(
        {
            "version": "0.2.0.dev0",
            "semantic_model": [
                {
                    "name": "sales",
                    "description": "Sales semantic model.",
                    "ai_context": {"instructions": "Prefer the reviewed revenue metric."},
                    "datasets": [
                        {
                            "name": "sales_fact",
                            "source": "DemoDW.mart.FactSales",
                            "description": "Sales facts.",
                            "primary_key": ["DateKey"],
                            "fields": [
                                {
                                    "name": "sale_date_key",
                                    "description": "Date dimension key.",
                                    "datatype": "Integer",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "DateKey"}
                                        ]
                                    },
                                },
                                {
                                    "name": "sales_amount",
                                    "description": "Sales amount.",
                                    "datatype": "Decimal",
                                    "expression": {
                                        "dialects": [
                                            {
                                                "dialect": "ANSI_SQL",
                                                "expression": "SalesAmount",
                                            }
                                        ]
                                    },
                                },
                            ],
                        },
                        {
                            "name": "date_dimension",
                            "source": "mart.DimDate",
                            "description": "Calendar dates.",
                            "fields": [
                                {
                                    "name": "date_key",
                                    "description": "Date key.",
                                    "expression": {
                                        "dialects": [
                                            {"dialect": "ANSI_SQL", "expression": "DateKey"}
                                        ]
                                    },
                                }
                            ],
                        },
                    ],
                    "relationships": [
                        {
                            "name": "sales_to_date",
                            "from": "sales_fact",
                            "to": "date_dimension",
                            "from_columns": ["DateKey"],
                            "to_columns": ["DateKey"],
                        }
                    ],
                    "metrics": [
                        {
                            "name": "total_sales",
                            "description": "Total sales.",
                            "expression": {
                                "dialects": [
                                    {
                                        "dialect": "ANSI_SQL",
                                        "expression": "SUM(sales_fact.SalesAmount)",
                                    }
                                ]
                            },
                        }
                    ],
                    "future_construct": {"preserve": True},
                }
            ],
        },
        indent=2,
    )


def _write_project(root: Path, files: dict[str, dict[str, object]]) -> None:
    for name, payload in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _sml_source_files() -> dict[str, dict[str, object]]:
    return {
        "catalog.yml": {
            "unique_name": "internet_sales_catalog",
            "object_type": "catalog",
            "version": 1.6,
        },
        "datasets/factinternetsales.yml": {
            "unique_name": "factinternetsales",
            "object_type": "dataset",
            "description": "Internet sales facts.",
            "table": "factinternetsales",
            "columns": [
                {"name": "orderdatekey", "data_type": "long"},
                {"name": "salesamount", "data_type": "double"},
            ],
        },
        "metrics/Sales Amount.yml": {
            "unique_name": "Sales Amount",
            "object_type": "metric",
            "description": "Total sales amount.",
            "calculation_method": "sum",
            "dataset": "factinternetsales",
            "column": "salesamount",
        },
        "models/internet_sales.yml": {
            "unique_name": "internet_sales",
            "object_type": "model",
            "description": "Internet sales semantic model.",
            "metrics": [{"unique_name": "Sales Amount"}],
        },
    }


def _cube_source_files() -> dict[str, dict[str, object]]:
    return {
        "cubes/orders.yaml": {
            "cubes": [
                {
                    "name": "orders",
                    "sql_table": "cube.orders",
                    "dimensions": [
                        {"name": "id", "sql": "id", "type": "number", "primary_key": True},
                        {"name": "user_id", "sql": "user_id", "type": "number"},
                        {"name": "status", "sql": "status", "type": "string"},
                    ],
                    "measures": [{"name": "count", "type": "count"}],
                    "joins": [
                        {
                            "name": "users",
                            "sql": "{CUBE}.user_id = {users.id}",
                            "relationship": "many_to_one",
                        }
                    ],
                }
            ]
        },
        "cubes/users.yaml": {
            "cubes": [
                {
                    "name": "users",
                    "sql_table": "cube.users",
                    "dimensions": [
                        {"name": "id", "sql": "id", "type": "number", "primary_key": True},
                        {"name": "full_name", "sql": "full_name", "type": "string"},
                        {"name": "city", "sql": "city", "type": "string"},
                    ],
                    "measures": [{"name": "count", "type": "count"}],
                }
            ]
        },
        "views/users_view.yaml": {
            "views": [
                {
                    "name": "users_with_links",
                    "cubes": [{"join_path": "users", "includes": ["full_name", "city"]}],
                }
            ]
        },
    }
