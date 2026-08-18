import json
import os
import sqlite3
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.application import sample_connector_use_case
from tarel.cli import main
from tarel.connectors.contracts import ConnectorFailure
from tarel.context import compile_context
from tarel.demo import create_retail_demo
from tarel.relationships.core import decide_relationship
from tarel.sdk import Tarel
from tarel.sources.contracts import SourceFailure


class EnrichmentTests(TestCase):
    def test_cli_emits_an_ephemeral_top_ten_workfile(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            project = Path(temporary_directory)
            root = project / ".tarel"
            create_retail_demo(path=root / "demos/retail.sqlite")
            output = StringIO()
            errors = StringIO()
            os.chdir(project)
            try:
                with redirect_stdout(StringIO()):
                    self.assertEqual(
                        main(
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
                                "--allow-raw-samples",
                            ]
                        ),
                        0,
                    )
                    self.assertEqual(
                        main(["source", "build", "retail", "retail-graph"]),
                        0,
                    )
                graph_path = root / "graphs/retail-graph/graph.json"
                before = graph_path.read_text(encoding="utf-8")
                with redirect_stdout(output), redirect_stderr(errors):
                    exit_code = main(
                        [
                            "source",
                            "enrich",
                            "retail",
                            "retail-graph",
                            "--format",
                            "json",
                        ]
                    )
                after = graph_path.read_text(encoding="utf-8")
            finally:
                os.chdir(previous)

        payload = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["permissions"], ["raw_samples"])
        self.assertEqual(len(payload["objects"]), 12)
        self.assertTrue(all(item["sample"] is not None for item in payload["objects"]))
        self.assertTrue(all(len(item["sample"]["rows"]) <= 10 for item in payload["objects"]))
        self.assertFalse(payload["persistence"]["raw_samples_persisted"])
        self.assertIn("not persisted", errors.getvalue())
        self.assertEqual(before, after)

    def test_batch_enrichment_is_denied_by_default_and_obeys_each_permission(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".tarel"
            create_retail_demo(path=root / "demos/retail.sqlite")
            sdk = Tarel(root)
            sdk.source.configure(
                "retail",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                namespace="main",
            )
            sdk.source.build_graph("retail", "retail-graph")

            with self.assertRaises(SourceFailure) as denied:
                sdk.source.enrich("retail", "retail-graph")

            sdk.source.configure(
                "retail",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                namespace="main",
                graphs=("retail-graph",),
                enrichment_permissions=("aggregates",),
                replace=True,
            )
            aggregate_result = sdk.source.enrich("retail", "retail-graph")
            graph_path = root / "graphs/retail-graph/graph.json"
            graph_before_samples = graph_path.read_text(encoding="utf-8")

            sdk.source.configure(
                "retail",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                namespace="main",
                graphs=("retail-graph",),
                enrichment_permissions=("aggregates", "small_domains", "raw_samples"),
                replace=True,
            )
            full_result = sdk.source.enrich("retail", "retail-graph")
            graph_after_samples = graph_path.read_text(encoding="utf-8")

        self.assertEqual(denied.exception.code, "enrichment_not_allowed")
        self.assertEqual(len(aggregate_result.workfile.objects), 12)
        self.assertTrue(all(item.profile is not None for item in aggregate_result.workfile.objects))
        self.assertTrue(all(item.sample is None for item in aggregate_result.workfile.objects))
        self.assertTrue(
            all(
                item.profile is not None and not item.profile.includes_values
                for item in aggregate_result.workfile.objects
            )
        )
        self.assertEqual(len(full_result.workfile.objects), 12)
        self.assertTrue(all(item.profile is not None for item in full_result.workfile.objects))
        self.assertTrue(all(item.sample is not None for item in full_result.workfile.objects))
        self.assertTrue(
            all(
                item.sample is not None and len(item.sample.rows) <= 10
                for item in full_result.workfile.objects
            )
        )
        self.assertTrue(
            all(
                item.profile is not None and item.profile.includes_values
                for item in full_result.workfile.objects
            )
        )
        self.assertTrue(full_result.workfile.samples_present)
        self.assertEqual(graph_before_samples, graph_after_samples)
        self.assertFalse(full_result.to_dict()["persistence"]["raw_samples_persisted"])

    def test_key_patterns_create_aggregate_draft_candidates_and_context_transform(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".tarel"
            _create_pattern_source(root)
            sdk = Tarel(root)
            sdk.source.configure(
                "keys",
                connector="sqlite",
                config_reference="state:demos/keys.toml",
                namespace="main",
                enrichment_permissions=("raw_samples",),
            )
            sdk.source.build_graph("keys", "key-graph")

            result = sdk.source.enrich(
                "keys",
                "key-graph",
                persist_join_candidates=True,
            )
            graph = sdk.graph.load("key-graph")
            graph_json = json.dumps(graph.to_dict(), sort_keys=True)
            composite = next(
                item for item in result.workfile.objects if item.label == "main.F_KEYS"
            )
            updated, validated_edge = decide_relationship(
                graph,
                edge_id=result.persisted_candidates[0].id,
                state="validated",
                reason="Pattern and target domains reviewed on the demo objects.",
            )
            context = compile_context(
                updated,
                "F_KEYS composite key",
                seed_limit=1,
                max_objects=3,
            )

        self.assertEqual(len(composite.key_patterns), 1)
        pattern = composite.key_patterns[0]
        self.assertEqual(pattern.pattern, "KST{digits_1:6}KTO{digits_2:6}")
        self.assertEqual(pattern.sample_count, 5)
        self.assertEqual(pattern.match_count, 4)
        self.assertEqual(pattern.coverage, 0.8)
        self.assertEqual(
            [(item.start, item.length) for item in pattern.components],
            [(3, 6), (12, 6)],
        )
        self.assertEqual(len(result.workfile.transformed_join_candidates), 2)
        self.assertEqual(
            len(
                {
                    (item.pair.from_field, item.component_index)
                    for item in result.workfile.transformed_join_candidates
                }
            ),
            2,
        )
        self.assertEqual(
            {
                (item.pair.to_object, item.pair.to_field)
                for item in result.workfile.transformed_join_candidates
            },
            {("D_KST", "KST_ID"), ("D_KTO", "KTO_ID")},
        )
        self.assertEqual(len(result.persisted_candidates), 2)
        self.assertTrue(
            all(edge.metadata["state"] == "draft" for edge in result.persisted_candidates)
        )
        self.assertTrue(
            all(
                edge.metadata["candidate_kind"] == "transformed_join_candidate"
                for edge in result.persisted_candidates
            )
        )
        self.assertNotIn("KST102020KTO102000", graph_json)
        self.assertNotIn("sample_values", graph_json)
        transformed_join = next(
            join for join in context.joins if join.id == validated_edge.id
        )
        self.assertEqual(transformed_join.kind, "validated_transformed_candidate")
        self.assertEqual(transformed_join.transformation["kind"], "fixed_segment")
        self.assertNotIn("KST102020KTO102000", context.canonical_json())

    def test_join_candidate_persistence_requires_raw_sample_permission(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".tarel"
            create_retail_demo(path=root / "demos/retail.sqlite")
            sdk = Tarel(root)
            sdk.source.configure(
                "retail",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                namespace="main",
                enrichment_permissions=("aggregates",),
            )
            sdk.source.build_graph("retail", "retail-graph")

            with self.assertRaises(SourceFailure) as raised:
                sdk.source.enrich(
                    "retail",
                    "retail-graph",
                    persist_join_candidates=True,
                )

        self.assertEqual(raised.exception.code, "raw_samples_not_allowed")

    def test_one_object_failure_is_visible_without_hiding_other_results(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory) / ".tarel"
            create_retail_demo(path=root / "demos/retail.sqlite")
            sdk = Tarel(root)
            sdk.source.configure(
                "retail",
                connector="sqlite",
                config_reference="state:demos/retail.toml",
                namespace="main",
                enrichment_permissions=("raw_samples",),
            )
            sdk.source.build_graph("retail", "retail-graph")

            def sample_with_one_failure(*args: object, **kwargs: object):
                if kwargs["object_name"] == "D_DATE":
                    raise ConnectorFailure("fixture_failure", "The fixture denied this object.")
                return sample_connector_use_case(*args, **kwargs)

            with patch(
                "tarel.sources.application.sample_connector_use_case",
                side_effect=sample_with_one_failure,
            ):
                result = sdk.source.enrich("retail", "retail-graph")

        failed = next(item for item in result.workfile.objects if item.label == "main.D_DATE")
        succeeded = next(item for item in result.workfile.objects if item.label == "main.D_PRD")
        self.assertIsNone(failed.sample)
        self.assertFalse(result.workfile.complete)
        self.assertEqual(failed.failures[0].operation, "sample")
        self.assertEqual(failed.failures[0].code, "fixture_failure")
        self.assertIsNotNone(succeeded.sample)


def _create_pattern_source(root: Path) -> None:
    demos = root / "demos"
    demos.mkdir(parents=True)
    database_path = demos / "keys.sqlite"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE D_KST (KST_ID INTEGER PRIMARY KEY);
            CREATE TABLE D_KST_ALT (KST_ID INTEGER PRIMARY KEY);
            CREATE TABLE D_KTO (KTO_ID INTEGER PRIMARY KEY);
            CREATE TABLE D_ACCT (ACCT_KY INTEGER PRIMARY KEY);
            CREATE TABLE F_KEYS (
                COMPOSITE_KEY TEXT PRIMARY KEY,
                EVENT_AT TEXT,
                EDUCATION TEXT
            );
            INSERT INTO D_KST VALUES (102020), (102021), (102022), (102023);
            INSERT INTO D_KST_ALT VALUES (102020), (102021), (102022), (102023);
            INSERT INTO D_KTO VALUES (102000), (102001), (102002), (102003);
            INSERT INTO D_ACCT VALUES (102020), (102021), (102022), (102023);
            INSERT INTO F_KEYS VALUES
                ('KST102020KTO102000', '2026-08-18T10:00:00', 'Bac + 5'),
                ('KST102021KTO102001', '2026-08-19T11:00:00', 'Bac + 4'),
                ('KST102022KTO102002', '2026-08-20T12:00:00', 'Bac + 3'),
                ('KST102023KTO102003', '2026-08-21T13:00:00', 'Bac + 2'),
                ('legacy-key', '2026-08-22T14:00:00', 'Bac + 1');
            """
        )
    config_path = demos / "keys.toml"
    config_path.write_text(
        "[sqlite]\n"
        f'url = "sqlite:///{database_path.resolve().as_posix()}"\n'
        'default_database = "KeyDemo"\n',
        encoding="utf-8",
    )
