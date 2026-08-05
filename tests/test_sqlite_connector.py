import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.connectors.contracts import (
    CatalogRequest,
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
