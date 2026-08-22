import hashlib
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.revision import graph_revision
from tarel.lineage.contracts import LineageFailure
from tarel.lineage.runtime import RuntimeLineageInput
from tarel.sdk import Tarel


class RuntimeLineageTests(TestCase):
    def test_sdk_imports_sanitized_sql_attempts_create_only(self) -> None:
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            root = Path(temporary_directory) / ".tarel"
            sdk = Tarel(root)
            graph = _graph()
            sdk.runtime.graph_store().save(graph)
            observed = _observed(graph)

            imported = sdk.lineage.import_runtime("run-001", observed)
            loaded = sdk.lineage.load_runtime("run-001")
            trace = sdk.lineage.trace_runtime("run-001", "duckdb-accepted")

            self.assertEqual(imported.document, loaded)
            self.assertEqual(sdk.lineage.list_runtime(), ("run-001",))
            self.assertEqual(loaded.events[0].inputs[0].reference, "SalesDW.dbo.Orders")
            self.assertEqual(loaded.events[0].dialect, "sqlite")
            self.assertEqual(
                loaded.events[0].inputs[1].reference,
                "SalesDW.dbo.Orders.OrderId",
            )
            self.assertEqual(loaded.events[2].operation, "aggregate")
            self.assertEqual(loaded.events[2].inputs[0].reference, "SalesDW.doc.CustomerProfiles")
            self.assertEqual(loaded.events[1].dialect, "duckdb")
            self.assertEqual(loaded.events[1].duration_ms, 17)
            self.assertFalse(loaded.events[1].result.truncated)
            self.assertEqual(loaded.events[3].dialect, "duckdb")
            self.assertEqual(loaded.events[3].status, "failed")
            self.assertEqual(loaded.events[3].error_code, "query_timeout")
            self.assertEqual(loaded.events[3].duration_ms, 1250)
            self.assertEqual(loaded.events[4].status, "accepted")
            self.assertEqual(loaded.events[4].to_dict()["engine"], "duckdb")
            self.assertEqual(
                loaded.events[4].consumes,
                ("sql-success", "sql-customers", "mongo-profiles"),
            )
            self.assertEqual(loaded.events[4].result.row_count, 4)
            self.assertTrue(loaded.events[4].result.truncated)
            self.assertEqual(loaded.events[5].status, "failed")
            self.assertEqual(loaded.events[5].error_code, "duckdb_conversion_error")
            self.assertEqual(
                tuple(item.call_id for item in trace.calls),
                (
                    "sql-success",
                    "sql-customers",
                    "mongo-profiles",
                    "duckdb-accepted",
                ),
            )
            self.assertEqual(
                tuple(item.source_call_id for item in trace.dependencies),
                ("sql-success", "sql-customers", "mongo-profiles"),
            )
            self.assertEqual(
                tuple(item.kind for item in trace.calls),
                ("sql_query", "sql_query", "mongo_query", "federated_query"),
            )
            self.assertEqual(
                {item.reference for item in trace.origins},
                {
                    "SalesDW.crm.Customers",
                    "SalesDW.crm.Customers.CustomerId",
                    "SalesDW.dbo.Orders",
                    "SalesDW.dbo.Orders.OrderId",
                    "SalesDW.doc.CustomerProfiles",
                    "SalesDW.doc.CustomerProfiles.ProfileCustomerId",
                },
            )
            with self.assertRaises(LineageFailure) as failed_trace:
                sdk.lineage.trace_runtime("run-001", "duckdb-failed")
            persisted = imported.path.read_text(encoding="utf-8")
            for forbidden in (
                "SELECT",
                "raw_rows",
                "connection_url",
                "parameter",
                "$match",
            ):
                self.assertNotIn(forbidden, persisted)

            with self.assertRaises(LineageFailure) as raised:
                sdk.lineage.import_runtime("run-001", observed)

        self.assertEqual(raised.exception.code, "runtime_lineage_exists")
        self.assertEqual(failed_trace.exception.code, "runtime_call_not_evidence")

    def test_old_runtime_payload_roundtrips_without_new_optional_fields(self) -> None:
        payload = _observed(_graph()).to_dict()
        for event in payload["events"]:
            event.pop("duration_ms", None)
            result = event.get("result")
            if result is not None:
                result.pop("truncated", None)

        observed = RuntimeLineageInput.from_dict(payload)
        roundtripped = observed.to_dict()

        self.assertEqual(roundtripped, payload)
        for event in roundtripped["events"]:
            self.assertNotIn("duration_ms", event)
            if event.get("result") is not None:
                self.assertNotIn("truncated", event["result"])

    def test_cli_and_sdk_share_the_runtime_import_path(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir=previous) as temporary_directory:
            project = Path(temporary_directory)
            graph = _graph()
            sdk = Tarel(project / ".tarel")
            sdk.runtime.graph_store().save(graph)
            source = project / "observed-run.json"
            source.write_text(
                json.dumps(_observed(graph).to_dict()),
                encoding="utf-8",
            )
            output = StringIO()
            shown = StringIO()
            listed = StringIO()
            traced = StringIO()
            try:
                os.chdir(project)
                with redirect_stdout(output):
                    exit_code = main(
                        [
                            "lineage",
                            "import-runtime",
                            "cli-run",
                            "--source",
                            str(source),
                            "--format",
                            "json",
                        ]
                    )
                with redirect_stdout(shown):
                    show_exit = main(
                        ["lineage", "show-runtime", "cli-run", "--format", "json"]
                    )
                with redirect_stdout(listed):
                    list_exit = main(["lineage", "list-runtime"])
                with redirect_stdout(traced):
                    trace_exit = main(
                        [
                            "lineage",
                            "trace-runtime",
                            "cli-run",
                            "duckdb-accepted",
                            "--format",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous)

            payload = json.loads(output.getvalue())
            shown_payload = json.loads(shown.getvalue())
            trace_payload = json.loads(traced.getvalue())
            loaded = sdk.lineage.load_runtime("cli-run")

        self.assertEqual(exit_code, 0)
        self.assertEqual(show_exit, 0)
        self.assertEqual(list_exit, 0)
        self.assertEqual(trace_exit, 0)
        self.assertEqual(payload["runtime_lineage"], loaded.to_dict())
        self.assertEqual(shown_payload["runtime_lineage"], loaded.to_dict())
        self.assertEqual(listed.getvalue().strip(), "cli-run")
        self.assertEqual(trace_payload["start_call_id"], "duckdb-accepted")
        self.assertEqual(len(trace_payload["dependencies"]), 3)
        self.assertEqual(loaded.run_id, "agent-run-001")

    def test_cli_rejects_raw_sql_without_echoing_or_persisting_it(self) -> None:
        previous = Path.cwd()
        protected_sql = "SELECT * FROM dbo.Orders WHERE token = 'PROTECTED_VALUE'"
        with TemporaryDirectory(dir=previous) as temporary_directory:
            project = Path(temporary_directory)
            graph = _graph()
            Tarel(project / ".tarel").runtime.graph_store().save(graph)
            payload = _observed(graph).to_dict()
            payload["events"][0]["query"] = protected_sql
            source = project / "unsafe-run.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            errors = StringIO()
            try:
                os.chdir(project)
                with redirect_stderr(errors):
                    exit_code = main(
                        [
                            "lineage",
                            "import-runtime",
                            "unsafe-run",
                            "--source",
                            str(source),
                        ]
                    )
            finally:
                os.chdir(previous)

            stored = project / ".tarel/runtime-lineage/unsafe-run/run.json"
            was_stored = stored.exists()
            message = errors.getvalue()

        self.assertEqual(exit_code, 2)
        self.assertIn("invalid_runtime_lineage", message)
        self.assertNotIn(protected_sql, message)
        self.assertFalse(was_stored)

    def test_cli_rejects_raw_mongo_pipeline_without_echoing_or_persisting_it(self) -> None:
        previous = Path.cwd()
        protected_pipeline = {"$match": {"api_token": "PROTECTED_VALUE"}}
        with TemporaryDirectory(dir=previous) as temporary_directory:
            project = Path(temporary_directory)
            graph = _graph()
            Tarel(project / ".tarel").runtime.graph_store().save(graph)
            payload = _observed(graph).to_dict()
            payload["events"][2]["pipeline"] = [protected_pipeline]
            source = project / "unsafe-mongo-run.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            errors = StringIO()
            try:
                os.chdir(project)
                with redirect_stderr(errors):
                    exit_code = main(
                        [
                            "lineage",
                            "import-runtime",
                            "unsafe-mongo-run",
                            "--source",
                            str(source),
                        ]
                    )
            finally:
                os.chdir(previous)

            stored = project / ".tarel/runtime-lineage/unsafe-mongo-run/run.json"
            was_stored = stored.exists()
            message = errors.getvalue()

        self.assertEqual(exit_code, 2)
        self.assertIn("invalid_runtime_lineage", message)
        self.assertNotIn("PROTECTED_VALUE", message)
        self.assertFalse(was_stored)

    def test_import_rejects_stale_graph_and_invalid_event_identity(self) -> None:
        graph = _graph()
        stale = _observed(graph).to_dict()
        stale["graph"]["revision"] = "0" * 64
        duplicate = _observed(graph).to_dict()
        duplicate["events"][1]["call_id"] = duplicate["events"][0]["call_id"]
        invalid_hash = _observed(graph).to_dict()
        invalid_hash["events"][0]["result"]["sha256"] = "not-a-sha256"
        unsafe_source = _observed(graph).to_dict()
        unsafe_source["events"][0]["source"] = "postgresql://user:secret@host/database"
        write_attempt = _observed(graph).to_dict()
        write_attempt["events"][0]["operation"] = "update"
        consumes_failed = _observed(graph).to_dict()
        consumes_failed["events"][4]["consumes"] = ["sql-failed"]
        consumes_future = _observed(graph).to_dict()
        consumes_future["events"][4]["consumes"] = ["duckdb-failed"]
        mongo_write = _observed(graph).to_dict()
        mongo_write["events"][2]["operation"] = "update_many"
        negative_duration = _observed(graph).to_dict()
        negative_duration["events"][1]["duration_ms"] = -1
        invalid_truncation = _observed(graph).to_dict()
        invalid_truncation["events"][1]["result"]["truncated"] = "false"

        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / ".tarel")
            sdk.runtime.graph_store().save(graph)
            with self.assertRaises(LineageFailure) as stale_error:
                sdk.lineage.import_runtime("stale", RuntimeLineageInput.from_dict(stale))
            with self.assertRaises(LineageFailure) as duplicate_error:
                RuntimeLineageInput.from_dict(duplicate)
            with self.assertRaises(LineageFailure) as hash_error:
                RuntimeLineageInput.from_dict(invalid_hash)
            with self.assertRaises(LineageFailure) as source_error:
                RuntimeLineageInput.from_dict(unsafe_source)
            with self.assertRaises(LineageFailure) as operation_error:
                RuntimeLineageInput.from_dict(write_attempt)
            with self.assertRaises(LineageFailure) as failed_dependency_error:
                RuntimeLineageInput.from_dict(consumes_failed)
            with self.assertRaises(LineageFailure) as future_dependency_error:
                RuntimeLineageInput.from_dict(consumes_future)
            with self.assertRaises(LineageFailure) as mongo_operation_error:
                RuntimeLineageInput.from_dict(mongo_write)
            with self.assertRaises(LineageFailure) as duration_error:
                RuntimeLineageInput.from_dict(negative_duration)
            with self.assertRaises(LineageFailure) as truncation_error:
                RuntimeLineageInput.from_dict(invalid_truncation)

        self.assertEqual(stale_error.exception.code, "runtime_graph_revision_mismatch")
        self.assertEqual(duplicate_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(hash_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(source_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(operation_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(failed_dependency_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(future_dependency_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(mongo_operation_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(duration_error.exception.code, "invalid_runtime_lineage")
        self.assertEqual(truncation_error.exception.code, "invalid_runtime_lineage")

    def test_mongo_find_failure_keeps_only_safe_error_evidence(self) -> None:
        payload = _observed(_graph()).to_dict()
        payload["events"] = payload["events"][:3]
        mongo = payload["events"][2]
        mongo["operation"] = "find"
        mongo["status"] = "failed"
        mongo["result"] = None
        mongo["error_code"] = "mongo_timeout"

        observed = RuntimeLineageInput.from_dict(payload)

        self.assertEqual(observed.events[2].operation, "find")
        self.assertEqual(observed.events[2].status, "failed")
        self.assertEqual(observed.events[2].error_code, "mongo_timeout")
        self.assertIsNone(observed.events[2].result)

    def test_sdk_imports_v02_duckdb_and_python_analysis_evidence(self) -> None:
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / ".tarel")
            graph = _graph()
            sdk.runtime.graph_store().save(graph)
            observed = _observed_v02(graph)

            imported = sdk.lineage.import_runtime("analysis-run", observed)
            loaded = sdk.lineage.load_runtime("analysis-run")
            trace = sdk.lineage.trace_runtime("analysis-run", "python-analysis")

        self.assertEqual(imported.document, loaded)
        self.assertEqual(loaded.contract_version, "tarel.runtime-lineage.v0.2")
        federated = loaded.events[4]
        python_analysis = loaded.events[6]
        self.assertEqual(federated.to_dict()["engine"], "duckdb")
        self.assertEqual(federated.executor.plugin_id, "v2.duckdb")
        self.assertEqual(federated.analysis.join_coverage, 0.75)
        self.assertEqual(dict(federated.analysis.unmatched_counts), {"orders": 2})
        self.assertEqual(python_analysis.to_dict()["tool_type"], "python")
        self.assertEqual(python_analysis.executor.plugin_id, "v2.python")
        self.assertEqual(python_analysis.analysis.reconciliation_status, "matched")
        self.assertEqual(
            tuple((item.call_id, item.kind) for item in trace.calls),
            (
                ("sql-success", "sql_query"),
                ("sql-customers", "sql_query"),
                ("mongo-profiles", "mongo_query"),
                ("duckdb-accepted", "federated_query"),
                ("python-analysis", "python_analysis"),
            ),
        )
        self.assertEqual(
            tuple((item.source_call_id, item.target_call_id) for item in trace.dependencies),
            (
                ("sql-success", "duckdb-accepted"),
                ("sql-customers", "duckdb-accepted"),
                ("mongo-profiles", "duckdb-accepted"),
                ("duckdb-accepted", "python-analysis"),
            ),
        )

    def test_cli_roundtrips_v02_through_the_sdk_application_path(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir=previous) as temporary_directory:
            project = Path(temporary_directory)
            graph = _graph()
            sdk = Tarel(project / ".tarel")
            sdk.runtime.graph_store().save(graph)
            source = project / "analysis.json"
            source.write_text(json.dumps(_observed_v02(graph).to_dict()), encoding="utf-8")
            imported = StringIO()
            traced = StringIO()
            try:
                os.chdir(project)
                with redirect_stdout(imported):
                    import_exit = main(
                        [
                            "lineage",
                            "import-runtime",
                            "analysis-cli",
                            "--source",
                            str(source),
                            "--format",
                            "json",
                        ]
                    )
                with redirect_stdout(traced):
                    trace_exit = main(
                        [
                            "lineage",
                            "trace-runtime",
                            "analysis-cli",
                            "python-analysis",
                            "--format",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous)
            loaded = sdk.lineage.load_runtime("analysis-cli")

        self.assertEqual(import_exit, 0)
        self.assertEqual(trace_exit, 0)
        self.assertEqual(json.loads(imported.getvalue())["runtime_lineage"], loaded.to_dict())
        self.assertEqual(json.loads(traced.getvalue())["calls"][-1]["kind"], "python_analysis")

    def test_v02_rejects_unsafe_or_inconsistent_analysis_evidence(self) -> None:
        graph = _graph()

        def payload() -> dict:
            return _observed_v02(graph).to_dict()

        invalid_payloads = []
        raw_code = payload()
        raw_code["events"][6]["code"] = "print('PROTECTED_VALUE')"
        invalid_payloads.append(raw_code)
        wrong_input_order = payload()
        wrong_input_order["events"][4]["inputs"].reverse()
        invalid_payloads.append(wrong_input_order)
        duplicate_alias = payload()
        duplicate_alias["events"][4]["inputs"][1]["alias"] = "orders"
        invalid_payloads.append(duplicate_alias)
        wrong_source = payload()
        wrong_source["events"][4]["inputs"][0]["source"] = "another-reader"
        invalid_payloads.append(wrong_source)
        unknown_unmatched_alias = payload()
        unknown_unmatched_alias["events"][4]["analysis"]["unmatched_counts"] = {"other": 1}
        invalid_payloads.append(unknown_unmatched_alias)
        invalid_coverage = payload()
        invalid_coverage["events"][4]["analysis"]["join_coverage"] = 1.1
        invalid_payloads.append(invalid_coverage)
        invalid_limit = payload()
        invalid_limit["events"][6]["analysis"]["limits"]["timeout_ms"] = 0
        invalid_payloads.append(invalid_limit)
        empty_success_grain = payload()
        empty_success_grain["events"][6]["analysis"]["grain"] = []
        invalid_payloads.append(empty_success_grain)
        duplicate_grain = payload()
        duplicate_grain["events"][6]["analysis"]["grain"] = ["Segment", "segment"]
        invalid_payloads.append(duplicate_grain)
        external_dependency = payload()
        external_dependency["events"][4]["consumes"][0] = "another-document-call"
        external_dependency["events"][4]["inputs"][0]["call_id"] = "another-document-call"
        invalid_payloads.append(external_dependency)

        for invalid in invalid_payloads:
            with self.subTest(invalid=invalid), self.assertRaises(LineageFailure) as raised:
                RuntimeLineageInput.from_dict(invalid)
            self.assertEqual(raised.exception.code, "invalid_runtime_lineage")

        v01_with_python = _observed_v02(graph).to_dict()
        v01_with_python["contract_version"] = "tarel.runtime-lineage-input.v0.1"
        v01_with_python["events"] = [v01_with_python["events"][6]]
        v01_with_python["events"][0]["sequence"] = 1
        with self.assertRaises(LineageFailure) as unsupported_kind:
            RuntimeLineageInput.from_dict(v01_with_python)
        self.assertEqual(unsupported_kind.exception.code, "invalid_runtime_lineage")

    def test_v02_preserves_failed_python_analysis_without_raw_code(self) -> None:
        payload = _observed_v02(_graph()).to_dict()
        analysis = payload["events"][6]
        analysis["status"] = "failed"
        analysis["result"] = None
        analysis["error_code"] = "python_timeout"
        analysis["analysis"]["grain"] = []
        analysis["analysis"]["join_coverage"] = None
        analysis["analysis"]["reconciliation_status"] = "not_run"

        observed = RuntimeLineageInput.from_dict(payload)
        roundtripped = observed.to_dict()

        self.assertEqual(roundtripped, payload)
        self.assertEqual(observed.events[6].error_code, "python_timeout")
        self.assertIsNone(observed.events[6].result)
        self.assertNotIn('"code":', json.dumps(roundtripped))


def _observed(graph) -> RuntimeLineageInput:
    order = next(node for node in graph.nodes if node.label == "dbo.Orders")
    order_id = next(node for node in graph.nodes if node.label == "OrderId")
    customer = next(node for node in graph.nodes if node.label == "crm.Customers")
    customer_id = next(node for node in graph.nodes if node.label == "CustomerId")
    profiles = next(node for node in graph.nodes if node.label == "doc.CustomerProfiles")
    profile_customer_id = next(node for node in graph.nodes if node.label == "ProfileCustomerId")
    return RuntimeLineageInput.from_dict(
        {
            "contract_version": "tarel.runtime-lineage-input.v0.1",
            "events": [
                {
                    "call_id": "sql-success",
                    "dialect": "sqlite",
                    "duration_ms": 8,
                    "error_code": None,
                    "inputs": [order.id, order_id.id],
                    "kind": "sql_query",
                    "operation": "select",
                    "result": {
                        "columns": ["OrderId"],
                        "row_count": 12,
                        "sha256": hashlib.sha256(b"sanitized-result").hexdigest(),
                        "truncated": False,
                    },
                    "sequence": 1,
                    "source": "sales-reader",
                    "statement_sha256": hashlib.sha256(b"read-only statement").hexdigest(),
                    "status": "succeeded",
                },
                {
                    "call_id": "sql-customers",
                    "dialect": "duckdb",
                    "duration_ms": 17,
                    "error_code": None,
                    "inputs": [customer.id, customer_id.id],
                    "kind": "sql_query",
                    "operation": "select",
                    "result": {
                        "columns": ["CustomerId"],
                        "row_count": 9,
                        "sha256": hashlib.sha256(b"customer-result").hexdigest(),
                        "truncated": False,
                    },
                    "sequence": 2,
                    "source": "customer-reader",
                    "statement_sha256": hashlib.sha256(b"customer statement").hexdigest(),
                    "status": "succeeded",
                },
                {
                    "call_id": "mongo-profiles",
                    "error_code": None,
                    "inputs": [profiles.id, profile_customer_id.id],
                    "kind": "mongo_query",
                    "operation": "aggregate",
                    "request_sha256": hashlib.sha256(b"mongo aggregate").hexdigest(),
                    "result": {
                        "columns": ["ProfileCustomerId", "LifetimeValueBand"],
                        "row_count": 7,
                        "sha256": hashlib.sha256(b"mongo-result").hexdigest(),
                    },
                    "sequence": 3,
                    "source": "profiles-reader",
                    "status": "succeeded",
                },
                {
                    "call_id": "sql-failed",
                    "dialect": "duckdb",
                    "duration_ms": 1250,
                    "error_code": "query_timeout",
                    "inputs": [order.id],
                    "kind": "sql_query",
                    "operation": "select",
                    "result": None,
                    "sequence": 4,
                    "source": "sales-reader",
                    "statement_sha256": hashlib.sha256(b"failed statement").hexdigest(),
                    "status": "failed",
                },
                {
                    "call_id": "duckdb-accepted",
                    "consumes": ["sql-success", "sql-customers", "mongo-profiles"],
                    "engine": "duckdb",
                    "error_code": None,
                    "kind": "federated_query",
                    "operation": "select",
                    "result": {
                        "columns": ["OrderCount", "GrossAmount"],
                        "row_count": 4,
                        "sha256": hashlib.sha256(b"federated-result").hexdigest(),
                        "truncated": True,
                    },
                    "sequence": 5,
                    "statement_sha256": hashlib.sha256(b"duckdb statement").hexdigest(),
                    "status": "accepted",
                },
                {
                    "call_id": "duckdb-failed",
                    "consumes": ["sql-success"],
                    "engine": "duckdb",
                    "error_code": "duckdb_conversion_error",
                    "kind": "federated_query",
                    "operation": "select",
                    "result": None,
                    "sequence": 6,
                    "statement_sha256": hashlib.sha256(b"bad duckdb statement").hexdigest(),
                    "status": "failed",
                },
            ],
            "graph": {"name": graph.name, "revision": graph_revision(graph)},
            "run_id": "agent-run-001",
        }
    )


def _observed_v02(graph) -> RuntimeLineageInput:
    payload = _observed(graph).to_dict()
    payload["contract_version"] = "tarel.runtime-lineage-input.v0.2"
    accepted = payload["events"][4]
    accepted.update(
        {
            "analysis": {
                "duration_ms": 34,
                "grain": ["CustomerId"],
                "join_coverage": 0.75,
                "limits": {
                    "input_row_limit": 10_000,
                    "output_row_limit": 1_000,
                    "timeout_ms": 5_000,
                },
                "reconciliation_status": "partial",
                "unmatched_counts": {"orders": 2},
            },
            "executor": {"plugin_id": "v2.duckdb", "plugin_version": "1.0.0"},
            "inputs": [
                {
                    "alias": "orders",
                    "call_id": "sql-success",
                    "frame_sha256": hashlib.sha256(b"orders-frame").hexdigest(),
                    "source": "sales-reader",
                },
                {
                    "alias": "customers",
                    "call_id": "sql-customers",
                    "frame_sha256": hashlib.sha256(b"customers-frame").hexdigest(),
                    "source": "customer-reader",
                },
                {
                    "alias": "profiles",
                    "call_id": "mongo-profiles",
                    "frame_sha256": hashlib.sha256(b"profiles-frame").hexdigest(),
                    "source": "profiles-reader",
                },
            ],
        }
    )
    failed = payload["events"][5]
    failed.update(
        {
            "analysis": {
                "duration_ms": 9,
                "grain": [],
                "join_coverage": None,
                "limits": {
                    "input_row_limit": 1_000,
                    "output_row_limit": 100,
                    "timeout_ms": 1_000,
                },
                "reconciliation_status": "not_run",
                "unmatched_counts": {},
            },
            "executor": {"plugin_id": "v2.duckdb", "plugin_version": "1.0.0"},
            "inputs": [
                {
                    "alias": "orders",
                    "call_id": "sql-success",
                    "frame_sha256": hashlib.sha256(b"orders-frame").hexdigest(),
                    "source": "sales-reader",
                }
            ],
        }
    )
    payload["events"].append(
        {
            "analysis": {
                "duration_ms": 21,
                "grain": ["Segment"],
                "join_coverage": None,
                "limits": {
                    "input_row_limit": 1_000,
                    "output_row_limit": 100,
                    "timeout_ms": 2_000,
                },
                "reconciliation_status": "matched",
                "unmatched_counts": {},
            },
            "call_id": "python-analysis",
            "code_sha256": hashlib.sha256(b"controlled python code").hexdigest(),
            "consumes": ["duckdb-accepted"],
            "error_code": None,
            "executor": {"plugin_id": "v2.python", "plugin_version": "1.0.0"},
            "inputs": [
                {
                    "alias": "federated",
                    "call_id": "duckdb-accepted",
                    "frame_sha256": hashlib.sha256(b"federated-frame").hexdigest(),
                    "source": "federated-memory",
                }
            ],
            "kind": "python_analysis",
            "operation": "analyze",
            "result": {
                "columns": ["Segment", "GrossAmount"],
                "row_count": 3,
                "sha256": hashlib.sha256(b"python-result").hexdigest(),
                "truncated": False,
            },
            "sequence": 7,
            "status": "succeeded",
            "tool_type": "python",
        }
    )
    return RuntimeLineageInput.from_dict(payload)


def _graph():
    return build_graph_from_catalog(
        "sales-runtime",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="SalesDW",
            dialect="tsql",
            objects=(
                CatalogObject(
                    namespace="dbo",
                    name="Orders",
                    kind="table",
                    fields=(
                        CatalogField("OrderId", 1, "integer", False, is_primary_key=True),
                        CatalogField("TotalAmount", 2, "decimal(18,2)", False),
                    ),
                    primary_key=("OrderId",),
                ),
                CatalogObject(
                    namespace="crm",
                    name="Customers",
                    kind="table",
                    fields=(
                        CatalogField("CustomerId", 1, "integer", False, is_primary_key=True),
                        CatalogField("Segment", 2, "varchar(40)", True),
                    ),
                    primary_key=("CustomerId",),
                ),
                CatalogObject(
                    namespace="doc",
                    name="CustomerProfiles",
                    kind="table",
                    fields=(
                        CatalogField("ProfileCustomerId", 1, "string", False),
                        CatalogField("LifetimeValueBand", 2, "string", True),
                    ),
                ),
            ),
        ),
    )
