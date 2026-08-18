import json
import sqlite3
import tomllib
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.cli import main
from tarel.connectors.contracts import (
    CatalogRequest,
    ConnectorFailure,
    ObjectProfileRequest,
    RelationshipPair,
    RelationshipProbeRequest,
    SampleRequest,
)
from tarel.connectors.host import load_connector, load_manifest
from tarel.demo import DemoFailure, create_retail_demo


class SqliteConnectorTests(TestCase):
    def test_retail_demo_exercises_discovery_sampling_and_relationship_probe(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            result = create_retail_demo(path=Path(temporary_directory) / "retail.sqlite")
            config = tomllib.loads(result.config_path.read_text(encoding="utf-8"))["sqlite"]
            connector = load_connector("sqlite")

            catalog = connector.discover_catalog(
                CatalogRequest(
                    url=config["url"],
                    database=config["default_database"],
                    namespace="main",
                )
            )
            sample = connector.sample_rows(
                SampleRequest(
                    url=config["url"],
                    database=config["default_database"],
                    namespace="main",
                    object_name="F_SLS_01",
                    limit=2,
                )
            )
            profile = connector.probe_relationships(
                RelationshipProbeRequest(
                    url=config["url"],
                    database=config["default_database"],
                    pairs=(
                        RelationshipPair(
                            from_namespace="main",
                            from_object="F_SLS_02",
                            from_field="RSLR_KEY",
                            to_namespace="main",
                            to_object="D_RSLR",
                            to_field="RSLR_KEY",
                        ),
                    ),
                    row_limit=10_000,
                )
            ).profiles[0]

            self.assertEqual(len(catalog.objects), 12)
            self.assertEqual(len(catalog.relationships), 13)
            self.assertEqual(sample.rows[0]["CHNL_CD"], "WEB")
            self.assertEqual(profile.overlap_count, 8)
            self.assertEqual(profile.source_coverage, 1.0)
            self.assertEqual(profile.target_uniqueness, 1.0)

    def test_object_profile_requires_explicit_opt_in_for_small_domain_values(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            result = create_retail_demo(path=Path(temporary_directory) / "retail.sqlite")
            config = tomllib.loads(result.config_path.read_text(encoding="utf-8"))["sqlite"]
            connector = load_connector("sqlite")
            request = ObjectProfileRequest(
                url=config["url"],
                database=config["default_database"],
                namespace="main",
                object_name="D_CHNL",
                row_limit=10,
            )

            aggregate_only = connector.profile_object(request)
            with_values = connector.profile_object(
                ObjectProfileRequest(
                    url=request.url,
                    database=request.database,
                    namespace=request.namespace,
                    object_name=request.object_name,
                    row_limit=request.row_limit,
                    include_values=True,
                )
            )

            aggregate_code = next(item for item in aggregate_only.columns if item.name == "CHNL_CD")
            value_code = next(item for item in with_values.columns if item.name == "CHNL_CD")
            self.assertTrue(aggregate_only.complete)
            self.assertEqual(aggregate_only.rows_profiled, 3)
            self.assertEqual(aggregate_code.distinct_count, 3)
            self.assertEqual(aggregate_code.min_length, 3)
            self.assertEqual(aggregate_code.max_length, 5)
            self.assertEqual(aggregate_code.values, ())
            self.assertTrue(value_code.values_complete)
            self.assertEqual(
                tuple((item.value, item.count) for item in value_code.values),
                (("RSLR", 1), ("STORE", 1), ("WEB", 1)),
            )

    def test_object_profile_marks_bounded_results_incomplete(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            result = create_retail_demo(path=Path(temporary_directory) / "retail.sqlite")
            config = tomllib.loads(result.config_path.read_text(encoding="utf-8"))["sqlite"]
            connector = load_connector("sqlite")

            profile = connector.profile_object(
                ObjectProfileRequest(
                    url=config["url"],
                    database=config["default_database"],
                    namespace="main",
                    object_name="F_SLS_01",
                    row_limit=10,
                    include_values=True,
                )
            )

            self.assertFalse(profile.complete)
            self.assertFalse(profile.includes_values)
            self.assertEqual(profile.rows_profiled, 10)
            self.assertEqual(profile.ordered_by, ("DOC_ID",))
            self.assertTrue(all(not item.values for item in profile.columns))

    def test_profile_cli_emits_ephemeral_json_without_values_by_default(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            result = create_retail_demo(path=Path(temporary_directory) / "retail.sqlite")
            output = StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    [
                        "connector",
                        "profile",
                        "sqlite",
                        "--config",
                        str(result.config_path),
                        "--schema",
                        "main",
                        "--object",
                        "D_CHNL",
                        "--row-limit",
                        "10",
                        "--format",
                        "json",
                    ]
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertFalse(payload["includes_values"])
            self.assertTrue(payload["complete"])
            self.assertTrue(all(not item["values"] for item in payload["columns"]))

    def test_profile_counts_nulls_and_reports_unsupported_columns(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "profile.sqlite"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TABLE Example ("
                    "RecordId INTEGER PRIMARY KEY, Status TEXT, Note TEXT, Payload BLOB)"
                )
                connection.executemany(
                    "INSERT INTO Example VALUES (?, ?, ?, ?)",
                    (
                        (1, "OPEN", None, b"one"),
                        (2, "OPEN", "ready", b"two"),
                        (3, "CLOSED", "done", b"three"),
                    ),
                )
            connector = load_connector("sqlite")

            profile = connector.profile_object(
                ObjectProfileRequest(
                    url=f"sqlite:///{path}",
                    database="profile",
                    namespace="main",
                    object_name="Example",
                    row_limit=10,
                    include_values=True,
                )
            )

            note = next(item for item in profile.columns if item.name == "Note")
            payload = next(item for item in profile.columns if item.name == "Payload")
            self.assertEqual(note.non_null_count, 2)
            self.assertEqual(note.null_count, 1)
            self.assertEqual(note.distinct_count, 2)
            self.assertEqual(payload.status, "omitted")
            self.assertEqual(payload.reason, "unsupported_type")

    def test_raw_preview_accepts_ten_rows_but_rejects_eleven(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            result = create_retail_demo(path=Path(temporary_directory) / "retail.sqlite")
            config = tomllib.loads(result.config_path.read_text(encoding="utf-8"))["sqlite"]
            connector = load_connector("sqlite")
            request = SampleRequest(
                url=config["url"],
                database=config["default_database"],
                namespace="main",
                object_name="F_SLS_01",
                limit=10,
            )

            sample = connector.sample_rows(request)

            self.assertEqual(len(sample.rows), 10)
            with self.assertRaisesRegex(ConnectorFailure, "between 1 and 10"):
                connector.sample_rows(
                    SampleRequest(
                        url=request.url,
                        database=request.database,
                        namespace=request.namespace,
                        object_name=request.object_name,
                        limit=11,
                    )
                )

    def test_foreign_key_names_are_stable_across_demo_drift(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "retail.sqlite"
            first = create_retail_demo(path=path)
            connector = load_connector("sqlite")
            first_config = tomllib.loads(first.config_path.read_text(encoding="utf-8"))["sqlite"]
            first_catalog = connector.discover_catalog(
                CatalogRequest(
                    url=first_config["url"],
                    database=first_config["default_database"],
                )
            )
            second = create_retail_demo(path=path, version=2, force=True)
            second_config = tomllib.loads(second.config_path.read_text(encoding="utf-8"))["sqlite"]
            second_catalog = connector.discover_catalog(
                CatalogRequest(
                    url=second_config["url"],
                    database=second_config["default_database"],
                )
            )

            first_names = {relationship.name for relationship in first_catalog.relationships}
            second_names = {relationship.name for relationship in second_catalog.relationships}
            stable_name = "fk_F_SLS_01__CHNL_CD__D_CHNL__CHNL_CD"
            removed_name = "fk_F_SLS_02__CURR_CD__D_CURR__CURR_CD"
            self.assertIn(stable_name, first_names & second_names)
            self.assertIn(removed_name, first_names - second_names)

    def test_demo_requires_force_before_replacing_local_files(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "retail.sqlite"
            create_retail_demo(path=path)

            with self.assertRaises(DemoFailure) as raised:
                create_retail_demo(path=path)

            self.assertEqual(raised.exception.code, "demo_exists")

    def test_sqlite_manifest_has_no_third_party_dependency(self) -> None:
        manifest = load_manifest("sqlite")

        self.assertEqual(manifest.dependencies, ())
        self.assertEqual(manifest.permissions, ("read",))
        self.assertEqual(manifest.dialect, "sqlite")
