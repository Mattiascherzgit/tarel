from dataclasses import replace
from unittest import TestCase

from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogResult,
    RelationshipPairProfile,
)
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.contracts import GraphAnnotation
from tarel.graph.refresh import refresh_graph
from tarel.relationships.core import (
    add_manual_relationship,
    add_profile_candidates,
    candidate_pairs,
    decide_relationship,
    relationship_pair,
    usable_relationships,
)


class RelationshipTests(TestCase):
    def test_sqlite_integer_fields_can_form_candidate_pairs(self) -> None:
        graph = _anonymous_graph()
        graph = replace(
            graph,
            nodes=tuple(
                replace(
                    node,
                    metadata={**node.metadata, "data_type": "INTEGER"},
                )
                if node.type == "field"
                else node
                for node in graph.nodes
            ),
        )

        pairs = candidate_pairs(
            graph,
            object_reference="x.A001",
            field_name="C001",
            max_pairs=5,
        )

        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0].to_field, "Z9")

    def test_declared_foreign_key_supersedes_a_matching_candidate_on_refresh(self) -> None:
        graph = _anonymous_graph()
        pair = relationship_pair(graph, "x.A001.C001", "x.B001.Z9")
        graph, _edge = add_manual_relationship(
            graph,
            pair=pair,
            reason="Temporary source mapping.",
            validated=True,
        )

        refreshed, report = refresh_graph(graph, _anonymous_graph(declared_relationship=True))

        self.assertEqual(report.superseded_relationships, 1)
        self.assertEqual(
            [edge.type for edge in usable_relationships(refreshed)],
            ["foreign_key"],
        )

    def test_refresh_preserves_annotations_and_human_relationships(self) -> None:
        graph = _anonymous_graph()
        annotated_nodes = tuple(
            replace(node, annotation=GraphAnnotation(description="Reviewed source object."))
            if node.label == "x.A001"
            else node
            for node in graph.nodes
        )
        graph = replace(graph, nodes=annotated_nodes)
        pair = relationship_pair(graph, "x.A001.C001", "x.B001.Z9")
        graph, _edge = add_manual_relationship(
            graph,
            pair=pair,
            reason="Confirmed in the source mapping.",
            validated=True,
        )

        refreshed, report = refresh_graph(graph, _anonymous_graph())

        source = next(node for node in refreshed.nodes if node.label == "x.A001")
        self.assertEqual(source.annotation.description, "Reviewed source object.")
        self.assertEqual(report.carried_annotations, 1)
        self.assertEqual(report.carried_relationships, 1)
        self.assertEqual(len(usable_relationships(refreshed)), 1)

    def test_human_can_validate_or_reject_an_inferred_candidate(self) -> None:
        graph = _anonymous_graph()
        pair = relationship_pair(graph, "x.A001.C001", "x.B001.Z9")
        profile = RelationshipPairProfile(
            pair=pair,
            source_non_null_count=4,
            source_distinct_count=3,
            target_non_null_count=3,
            target_distinct_count=3,
            overlap_count=3,
            profile_row_limit=10_000,
        )
        graph, candidates = add_profile_candidates(
            graph,
            (profile,),
            min_source_coverage=0.85,
            min_overlap_count=3,
            min_target_uniqueness=0.9,
        )

        validated, edge = decide_relationship(
            graph,
            edge_id=candidates[0].id,
            state="validated",
            reason="Confirmed by the data owner.",
        )
        rejected, rejected_edge = decide_relationship(
            validated,
            edge_id=edge.id,
            state="rejected",
            reason="Correction after source-system review.",
        )

        self.assertEqual(len(usable_relationships(validated)), 1)
        self.assertEqual(rejected_edge.metadata["state"], "rejected")
        self.assertEqual(usable_relationships(rejected), ())

    def test_human_can_add_a_validated_pair_without_profiling(self) -> None:
        graph = _anonymous_graph()
        pair = relationship_pair(graph, "x.A001.C001", "x.B001.Z9")

        updated, edge = add_manual_relationship(
            graph,
            pair=pair,
            reason="Confirmed in the source mapping.",
            validated=True,
        )

        self.assertEqual(edge.metadata["origin"], "human")
        self.assertEqual(edge.metadata["state"], "validated")
        self.assertEqual(len(updated.edges), len(graph.edges) + 1)

    def test_bounded_profile_creates_only_an_aggregate_draft(self) -> None:
        graph = _anonymous_graph()
        pair = candidate_pairs(
            graph,
            object_reference="x.A001",
            field_name="C001",
            max_pairs=5,
        )[0]
        profile = RelationshipPairProfile(
            pair=pair,
            source_non_null_count=4,
            source_distinct_count=3,
            target_non_null_count=3,
            target_distinct_count=3,
            overlap_count=3,
            profile_row_limit=10_000,
        )

        _updated, candidates = add_profile_candidates(
            graph,
            (profile,),
            min_source_coverage=0.85,
            min_overlap_count=3,
            min_target_uniqueness=0.9,
        )

        self.assertEqual(len(candidates), 1)
        metadata = candidates[0].metadata
        self.assertEqual(metadata["state"], "draft")
        self.assertEqual(metadata["source_coverage"], 1.0)
        self.assertNotIn("sample_values", metadata)


def _anonymous_graph(*, declared_relationship: bool = False):
    return build_graph_from_catalog(
        "mystery",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="MysteryDW",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="x",
                    name="A001",
                    kind="table",
                    fields=(CatalogField("C001", 1, "int", False),),
                ),
                CatalogObject(
                    namespace="x",
                    name="B001",
                    kind="table",
                    fields=(CatalogField("Z9", 1, "int", False),),
                ),
            ),
            relationships=(
                CatalogRelationship(
                    name="FK_A001_B001",
                    from_namespace="x",
                    from_object="A001",
                    from_fields=("C001",),
                    to_namespace="x",
                    to_object="B001",
                    to_fields=("Z9",),
                ),
            )
            if declared_relationship
            else (),
        ),
    )
