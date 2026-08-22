import json
import os
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.entity_resolution.contracts import EntityResolutionFailure
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.revision import graph_revision
from tarel.sdk import EntityResolutionCandidate, Tarel


class EntityResolutionTests(TestCase):
    def test_sdk_offers_unreviewed_candidate_as_explicit_fallback(self) -> None:
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / ".tarel")
            graph = _graph()
            sdk.runtime.graph_store().save(graph)
            candidate = _candidate(graph)

            imported = sdk.entity_resolution.import_candidate(candidate)
            fallback = sdk.entity_resolution.find("music")
            confirmed = sdk.entity_resolution.find("music", mode="confirmed_only")
            stored_text = imported.path.read_text(encoding="utf-8")
            stored_mode = imported.path.stat().st_mode & 0o777

        self.assertTrue(imported.changed)
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0].usage, "exploratory_only")
        self.assertTrue(fallback[0].requires_runtime_validation)
        self.assertIn("runtime", fallback[0].to_dict()["warning"])
        self.assertEqual(confirmed, ())
        self.assertEqual(stored_mode, 0o600)
        for forbidden in ("samples", "raw_rows", "local_path", "Beatles"):
            self.assertNotIn(forbidden, stored_text)

    def test_reviewed_rule_wins_per_field_pair_without_hiding_other_pairs(self) -> None:
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / ".tarel")
            graph = _graph()
            sdk.runtime.graph_store().save(graph)
            primary = _candidate(graph, candidate_id="artist-name-v1")
            alternative = _candidate(
                graph,
                candidate_id="artist-name-v2",
                reverse=True,
            )
            sdk.entity_resolution.import_candidate(primary)
            sdk.entity_resolution.import_candidate(alternative)

            reviewed = sdk.entity_resolution.decide(
                primary.id,
                decision="approve",
                reason="Population test and exceptions were reviewed.",
                expected_revision=primary.revision,
            ).candidate
            fallback = sdk.entity_resolution.find("music")
            all_active = sdk.entity_resolution.find("music", mode="include_candidates")
            confirmed = sdk.entity_resolution.find("music", mode="confirmed_only")
            rejected = sdk.entity_resolution.decide(
                alternative.id,
                decision="reject",
                reason="The alternative collapses distinct artist credits.",
            ).candidate
            after_rejection = sdk.entity_resolution.find(
                "music",
                mode="include_candidates",
            )
            audit_history = sdk.entity_resolution.list(graph="music")

        self.assertEqual(reviewed.state, "reviewed")
        self.assertEqual(reviewed.review.source, "human")
        self.assertEqual([item.candidate.id for item in fallback], [primary.id])
        self.assertEqual(
            [item.candidate.id for item in all_active],
            [primary.id, alternative.id],
        )
        self.assertEqual([item.candidate.id for item in confirmed], [primary.id])
        self.assertEqual(rejected.state, "rejected")
        self.assertEqual([item.candidate.id for item in after_rejection], [primary.id])
        self.assertEqual(len(audit_history), 2)

    def test_cli_and_sdk_share_import_find_and_review_application_path(self) -> None:
        previous = Path.cwd()
        with TemporaryDirectory(dir=previous) as temporary_directory:
            project = Path(temporary_directory)
            sdk = Tarel(project / ".tarel")
            graph = _graph()
            sdk.runtime.graph_store().save(graph)
            source = project / "candidate.json"
            source.write_text(json.dumps(_candidate(graph).to_dict()), encoding="utf-8")
            imported_output = StringIO()
            found_output = StringIO()
            reviewed_output = StringIO()
            try:
                os.chdir(project)
                with redirect_stdout(imported_output):
                    imported_exit = main(
                        ["entity", "import", "--source", str(source), "--format", "json"]
                    )
                loaded = sdk.entity_resolution.load("artist-name-v1")
                with redirect_stdout(found_output):
                    found_exit = main(
                        [
                            "entity",
                            "find",
                            "music",
                            "--source-field",
                            "mb.ArtistCredit.Name",
                            "--mode",
                            "confirmed-then-candidates".replace("-", "_"),
                            "--format",
                            "json",
                        ]
                    )
                with redirect_stdout(reviewed_output):
                    reviewed_exit = main(
                        [
                            "entity",
                            "review",
                            loaded.id,
                            "--decision",
                            "approve",
                            "--reason",
                            "The tested population and collisions were reviewed.",
                            "--revision",
                            loaded.revision,
                            "--format",
                            "json",
                        ]
                    )
            finally:
                os.chdir(previous)

            found = json.loads(found_output.getvalue())
            reviewed = sdk.entity_resolution.load("artist-name-v1")

        self.assertEqual((imported_exit, found_exit, reviewed_exit), (0, 0, 0))
        self.assertEqual(found["count"], 1)
        self.assertEqual(found["matches"][0]["usage"], "exploratory_only")
        self.assertEqual(reviewed.state, "reviewed")

    def test_projection_is_visible_but_does_not_mutate_graph_or_revision(self) -> None:
        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / ".tarel")
            graph = _graph()
            sdk.runtime.graph_store().save(graph)
            sdk.entity_resolution.import_candidate(_candidate(graph))

            before = graph_revision(sdk.graph.load("music"))
            payload = sdk.view.graph("music")
            after_graph = sdk.graph.load("music")
            projected = next(
                edge
                for edge in payload["edges"]
                if edge["type"] == "entity_resolution_candidate"
            )

        self.assertEqual(graph_revision(after_graph), before)
        self.assertFalse(
            any(edge.type == "entity_resolution_candidate" for edge in after_graph.edges)
        )
        self.assertEqual(projected["metadata"]["evidence_level"], "sample_tested")
        self.assertEqual(projected["metadata"]["usage"], "exploratory_only")
        self.assertTrue(projected["metadata"]["requires_runtime_validation"])
        self.assertEqual(len(payload["entity_resolution"]), 1)

        static = Path(__file__).parents[1] / "src/tarel/ui/static"
        application = (static / "app.js").read_text(encoding="utf-8")
        html = (static / "index.html").read_text(encoding="utf-8")
        self.assertIn("entityResolutionCards", application)
        self.assertIn('edge[type = "entity_resolution_candidate"]', application)
        self.assertIn("toggle-entity-resolution", html)

    def test_contract_rejects_raw_fields_bad_metrics_and_stale_graphs(self) -> None:
        graph = _graph()
        unsafe = _candidate(graph).to_dict()
        unsafe["samples"] = [{"name": "raw"}]
        bad_coverage = _candidate(graph).to_dict()
        bad_coverage["evidence"]["coverage"] = 0.99
        unknown_rule = _candidate(graph).to_dict()
        unknown_rule["rule"]["operations"] = ["agent_generated_regex"]

        with self.assertRaises(EntityResolutionFailure) as unsafe_error:
            EntityResolutionCandidate.from_dict(unsafe)
        with self.assertRaises(EntityResolutionFailure) as coverage_error:
            EntityResolutionCandidate.from_dict(bad_coverage)
        with self.assertRaises(EntityResolutionFailure) as rule_error:
            EntityResolutionCandidate.from_dict(unknown_rule)

        with TemporaryDirectory(dir=Path.cwd()) as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / ".tarel")
            sdk.runtime.graph_store().save(graph)
            sdk.entity_resolution.import_candidate(_candidate(graph))
            sdk.runtime.graph_store().save(replace(graph, catalog="ChangedCatalog"))
            stale_matches = sdk.entity_resolution.find("music")
            with self.assertRaises(EntityResolutionFailure) as stale_review:
                sdk.entity_resolution.decide(
                    "artist-name-v1",
                    decision="approve",
                    reason="This review targets an obsolete graph.",
                )

        self.assertEqual(unsafe_error.exception.code, "invalid_entity_resolution")
        self.assertEqual(coverage_error.exception.code, "invalid_entity_resolution")
        self.assertEqual(rule_error.exception.code, "invalid_entity_resolution")
        self.assertEqual(stale_matches, ())
        self.assertEqual(
            stale_review.exception.code,
            "entity_resolution_graph_revision_mismatch",
        )

    def test_cli_rejects_raw_candidate_without_echoing_or_persisting_it(self) -> None:
        previous = Path.cwd()
        protected_value = "PROTECTED_ARTIST_VALUE"
        with TemporaryDirectory(dir=previous) as temporary_directory:
            project = Path(temporary_directory)
            graph = _graph()
            Tarel(project / ".tarel").runtime.graph_store().save(graph)
            payload = _candidate(graph).to_dict()
            payload["samples"] = [protected_value]
            source = project / "unsafe.json"
            source.write_text(json.dumps(payload), encoding="utf-8")
            errors = StringIO()
            try:
                os.chdir(project)
                with redirect_stderr(errors):
                    exit_code = main(
                        ["entity", "import", "--source", str(source)]
                    )
            finally:
                os.chdir(previous)
            persisted = project / ".tarel/entity-resolution/artist-name-v1/candidate.json"

        self.assertEqual(exit_code, 2)
        self.assertIn("invalid_entity_resolution", errors.getvalue())
        self.assertNotIn(protected_value, errors.getvalue())
        self.assertFalse(persisted.exists())


def _candidate(
    graph,
    *,
    candidate_id: str = "artist-name-v1",
    reverse: bool = False,
) -> EntityResolutionCandidate:
    source = next(
        node
        for node in graph.nodes
        if node.type == "field" and node.label == "Name" and "ArtistCredit" in node.id
    )
    target = next(
        node
        for node in graph.nodes
        if node.type == "field" and node.label == "Name" and "ArtistCredit" not in node.id
    )
    if reverse:
        source, target = target, source
    return EntityResolutionCandidate.from_dict(
        {
            "contract_version": "tarel.entity-resolution-candidate.v0.1",
            "evidence": {
                "collision_count": 3,
                "collision_rate": 0.04,
                "confidence": 0.67,
                "counterexample_count": 4,
                "coverage": 0.75,
                "evaluated_count": 100,
                "level": "sample_tested",
                "matched_count": 75,
            },
            "graph": {"name": graph.name, "revision": graph_revision(graph)},
            "id": candidate_id,
            "provenance": {"producer": "v2-agent", "run_id": "music-run-42"},
            "review": None,
            "rule": {
                "kind": "normalized_exact",
                "operations": ["unicode_nfkc", "trim", "casefold"],
            },
            "source_field_id": source.id,
            "state": "candidate",
            "target_field_id": target.id,
        }
    )


def _graph():
    return build_graph_from_catalog(
        "music",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="MusicBrainz",
            dialect="sqlite",
            objects=(
                CatalogObject(
                    namespace="mb",
                    name="ArtistCredit",
                    kind="table",
                    fields=(
                        CatalogField("ArtistCreditId", 1, "integer", False),
                        CatalogField("Name", 2, "text", False),
                    ),
                    primary_key=("ArtistCreditId",),
                ),
                CatalogObject(
                    namespace="mb",
                    name="Artist",
                    kind="table",
                    fields=(
                        CatalogField("ArtistId", 1, "integer", False),
                        CatalogField("Name", 2, "text", False),
                    ),
                    primary_key=("ArtistId",),
                ),
            ),
        ),
    )
