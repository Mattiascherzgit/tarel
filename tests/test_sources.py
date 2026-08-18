import json
import os
import sqlite3
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.cli import main
from tarel.demo import create_retail_demo
from tarel.sdk import Tarel
from tarel.sources.contracts import SourceFailure, SourceProfile, create_source


class SourceTests(TestCase):
    def test_sqlite_source_runs_the_complete_sdk_and_grounding_path(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".tarel"
            demo = create_retail_demo(path=root / "demos/retail.sqlite")
            sdk = Tarel(root)

            configured = sdk.source.configure(
                "retail-local",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                namespace="main",
            )
            checked = sdk.source.check("retail-local")
            probe = sdk.source.probe("retail-local")
            catalog = sdk.source.discover("retail-local")
            built = sdk.source.build_graph("retail-local", "retail-source")
            refreshed = sdk.source.refresh_graph("retail-local", "retail-source")
            with sqlite3.connect(demo.database_path) as connection:
                connection.execute(
                    "CREATE TABLE SOURCE_DRIFT_PROBE (PROBE_ID INTEGER PRIMARY KEY, NOTE TEXT)"
                )
            changed_refresh = sdk.source.refresh_graph("retail-local", "retail-source")
            stable_refresh = sdk.source.refresh_graph("retail-local", "retail-source")
            bundle = sdk.grounding.context(
                "internet and reseller sales by year",
                graph="retail-source",
                sources=("retail-local",),
                mode="bm25",
            )
            stored = sdk.source.load("retail-local")
            registry_payload = (root / "sources/retail-local/source.json").read_text()

        self.assertTrue(configured.created)
        self.assertTrue(checked.available)
        self.assertEqual(checked.config_status, "resolved")
        self.assertEqual(probe.connector, "sqlite")
        self.assertEqual(catalog.dialect, "sqlite")
        self.assertEqual(len(catalog.objects), 12)
        self.assertEqual(built.graph.connector, "sqlite")
        self.assertEqual(refreshed.report.changes, ())
        self.assertIn("object_added", {item.kind for item in changed_refresh.report.changes})
        self.assertEqual(stable_refresh.report.changes, ())
        self.assertEqual(stored.graphs, ("retail-source",))
        self.assertEqual(bundle.sources[0].source, "retail-local")
        self.assertEqual(bundle.sources[0].source_revision, stored.revision)
        self.assertEqual(bundle.sources[0].dialect, "sqlite")
        self.assertNotIn("config_reference", bundle.canonical_json())
        self.assertNotIn(str(demo.config_path), bundle.canonical_json())
        self.assertNotIn("sqlite://", registry_payload)

    def test_cli_and_sdk_share_one_source_registry(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            project = Path(temporary_directory)
            root = project / ".tarel"
            create_retail_demo(path=root / "demos/retail.sqlite")
            output = StringIO()
            os.chdir(project)
            try:
                with redirect_stdout(output):
                    configured_exit = main(
                        [
                            "source",
                            "configure",
                            "retail",
                            "--connector",
                            "sqlite",
                            "--config-ref",
                            "state:demos/retail.toml",
                            "--namespace",
                            "main",
                            "--allow-aggregates",
                            "--allow-raw-samples",
                            "--format",
                            "json",
                        ]
                    )
                with redirect_stdout(StringIO()):
                    build_exit = main(
                        ["source", "build", "retail", "retail-graph", "--format", "json"]
                    )
            finally:
                os.chdir(previous)
            sdk = Tarel(root)
            source = sdk.source.load("retail")
            graph = sdk.graph.load("retail-graph")

        self.assertEqual(configured_exit, 0)
        self.assertEqual(build_exit, 0)
        self.assertEqual(json.loads(output.getvalue())["name"], "retail")
        self.assertEqual(source.graphs, ("retail-graph",))
        self.assertEqual(source.enrichment_permissions, ("aggregates", "raw_samples"))
        self.assertEqual(graph.dialect, "sqlite")

    def test_config_references_are_strict_and_missing_env_fails_visibly(self) -> None:
        for reference in (
            "postgresql://user:secret@localhost/db",
            "state:../private.toml",
            "state:..\\private.toml",
            "state:C:/private.toml",
            "file:/tmp/private.toml",
            "env:NOT-AN-ENV",
        ):
            with self.subTest(reference=reference):
                with self.assertRaises(SourceFailure) as raised:
                    create_source("unsafe", connector="sqlite", config_reference=reference)
                self.assertEqual(raised.exception.code, "invalid_config_reference")

        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / ".tarel")
            sdk.source.configure(
                "missing",
                connector="sqlite",
                config_reference="env:TAREL_TEST_MISSING_CONFIG",
            )
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("TAREL_TEST_MISSING_CONFIG", None)
                check = sdk.source.check("missing")
                with self.assertRaises(SourceFailure) as raised:
                    sdk.source.probe("missing")

        self.assertFalse(check.available)
        self.assertEqual(check.config_status, "missing")
        self.assertEqual(raised.exception.code, "source_config_not_resolved")

    def test_source_replacement_and_ambiguous_graph_routing_fail_closed(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".tarel"
            create_retail_demo(path=root / "demos/retail.sqlite")
            sdk = Tarel(root)
            sdk.source.configure(
                "primary",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
            )
            sdk.source.build_graph("primary", "retail")

            with self.assertRaises(SourceFailure) as exists:
                sdk.source.configure(
                    "primary",
                    connector="sqlite",
                    config_reference="state:demos/retail.toml",
                    namespace="main",
                )

            sdk.source.configure(
                "secondary",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                graphs=("retail",),
            )
            with self.assertRaises(SourceFailure) as ambiguous:
                sdk.grounding.context("sales", graph="retail")
            selected = sdk.grounding.context(
                "sales",
                graph="retail",
                sources=("primary",),
            )
            before_revision = selected.sources[0].source_revision
            sdk.source.configure(
                "primary",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                namespace="main",
                graphs=("retail",),
                replace=True,
            )
            changed = sdk.grounding.context(
                "sales",
                graph="retail",
                sources=("primary",),
            )
            sdk.source.configure("unmapped", connector="sqlite")
            with self.assertRaises(SourceFailure) as unmapped:
                sdk.grounding.context(
                    "sales",
                    graph="retail",
                    sources=("unmapped",),
                )
            with self.assertRaises(SourceFailure) as mismatch:
                sdk.source.configure(
                    "wrong-connector",
                    connector="sqlserver",
                    graphs=("retail",),
                )

        self.assertEqual(exists.exception.code, "source_exists")
        self.assertEqual(ambiguous.exception.code, "ambiguous_source_mapping")
        self.assertEqual(selected.sources[0].source, "primary")
        self.assertNotEqual(before_revision, changed.sources[0].source_revision)
        self.assertNotEqual(selected.stable_hash, changed.stable_hash)
        self.assertEqual(unmapped.exception.code, "source_graph_not_mapped")
        self.assertEqual(mismatch.exception.code, "source_graph_mismatch")

    def test_serialized_profile_rejects_write_access(self) -> None:
        payload = create_source("warehouse", connector="sqlserver").to_dict()
        payload["read_only"] = False

        with self.assertRaises(SourceFailure) as raised:
            SourceProfile.from_dict(payload)

        self.assertEqual(raised.exception.code, "invalid_source")

    def test_enrichment_policy_is_explicit_revisioned_and_dependency_checked(self) -> None:
        baseline = create_source("warehouse", connector="sqlserver")
        legacy_payload = baseline.to_dict()
        legacy_payload.pop("enrichment_permissions")
        enriched = create_source(
            "warehouse",
            connector="sqlserver",
            enrichment_permissions=("raw_samples", "aggregates", "small_domains"),
        )

        self.assertFalse(baseline.allows_enrichment("aggregates"))
        self.assertEqual(SourceProfile.from_dict(legacy_payload), baseline)
        self.assertTrue(enriched.allows_enrichment("aggregates"))
        self.assertTrue(enriched.allows_enrichment("small_domains"))
        self.assertTrue(enriched.allows_enrichment("raw_samples"))
        self.assertNotEqual(baseline.revision, enriched.revision)
        self.assertEqual(
            enriched.to_dict()["enrichment_permissions"],
            ["aggregates", "raw_samples", "small_domains"],
        )
        with self.assertRaises(SourceFailure) as missing_aggregate:
            create_source(
                "invalid",
                connector="sqlserver",
                enrichment_permissions=("small_domains",),
            )
        with self.assertRaises(SourceFailure) as unknown:
            create_source(
                "invalid",
                connector="sqlserver",
                enrichment_permissions=("unbounded_rows",),
            )

        self.assertEqual(missing_aggregate.exception.code, "invalid_enrichment_policy")
        self.assertEqual(unknown.exception.code, "invalid_enrichment_permission")
