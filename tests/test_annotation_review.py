import json
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.annotations.apply import apply_annotation_proposal
from tarel.annotations.contracts import AnnotationFailure, AnnotationProposalEnvelope
from tarel.annotations.review import decide_annotation, decide_annotation_scope, edit_annotation
from tarel.annotations.tasks import plan_annotation_tasks
from tarel.cli import main
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.context import compile_context
from tarel.graph.build import build_graph_from_catalog
from tarel.graph.contracts import AnnotationProvenance, GraphAnnotation, GraphDocument
from tarel.graph.store import FileGraphStore
from tarel.search import search_graph


class AnnotationReviewTests(TestCase):
    def test_edit_preserves_original_proposal_and_validate_appends_review(self) -> None:
        graph = _review_graph()

        edited, record = edit_annotation(
            graph,
            "warehouse.T1",
            {
                "description": "Internet revenue transactions.",
                "grain": "One transaction line per row.",
                "synonyms": ["web sales"],
            },
            reason="Confirmed the business meaning with the data owner.",
        )
        validated, records = decide_annotation_scope(
            edited,
            record.reference,
            state="validated",
            reason="Definition and grain are approved.",
            include_fields=True,
        )
        record = records[0]

        payload = record.to_dict()
        annotation = payload["annotation"]
        review = payload["review"]
        self.assertIsInstance(annotation, dict)
        self.assertIsInstance(review, dict)
        assert isinstance(annotation, dict) and isinstance(review, dict)
        self.assertEqual(annotation["description"], "Internet revenue transactions.")
        self.assertEqual(annotation["state"], "validated")
        self.assertEqual(
            annotation["provenance"],
            {"model": None, "provider": None, "source": "human"},
        )
        self.assertIsNone(annotation["confidence"])
        self.assertEqual(
            review["original"]["annotation"]["description"],
            "Proposed business meaning.",
        )
        self.assertEqual(
            [event["action"] for event in review["events"]],
            ["edit", "validate"],
        )
        self.assertEqual(payload["grain"], "One transaction line per row.")
        self.assertEqual(len(records), 2)
        self.assertTrue(
            all(
                node.annotation and node.annotation.state == "validated"
                for node in validated.nodes
                if node.type in {"table", "view", "field"}
            )
        )

        restored = GraphDocument.from_dict(validated.to_dict())
        self.assertEqual(restored.to_dict(), validated.to_dict())

    def test_rejected_semantics_are_hidden_but_the_technical_schema_remains(self) -> None:
        graph, _record = decide_annotation(
            _review_graph(),
            "warehouse.T1",
            state="rejected",
            reason="The proposed business meaning is incorrect.",
        )

        default_search = search_graph(graph, "business meaning")
        rejected_search = search_graph(
            graph,
            "business meaning",
            annotation_states=frozenset({"rejected"}),
        )
        context = compile_context(graph, "T1", seed_limit=1, max_objects=1)
        validated_context = compile_context(
            graph,
            "T1",
            seed_limit=1,
            max_objects=1,
            annotation_states=frozenset({"validated"}),
        )

        self.assertEqual(default_search.hits, ())
        self.assertEqual(rejected_search.hits[0].label, "warehouse.T1")
        self.assertEqual(context.objects[0].label, "warehouse.T1")
        self.assertEqual(context.objects[0].annotation_state, "rejected")
        self.assertIsNone(context.objects[0].description)
        self.assertIsNone(context.objects[0].grain)
        self.assertEqual(context.objects[0].fields[0].description, "Provider field proposal.")
        self.assertEqual(validated_context.objects[0].fields[0].annotation_state, "draft")
        self.assertIsNone(validated_context.objects[0].fields[0].description)

    def test_cli_edit_validate_show_and_filter_the_review_queue(self) -> None:
        graph = _review_graph()
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            store = FileGraphStore(root / "graphs")
            store.save(graph)
            patch_path = root / "patch.json"
            patch_path.write_text(
                json.dumps({"description": "Reviewed amount field."}),
                encoding="utf-8",
            )
            output = StringIO()
            with (
                patch("tarel.application.FileGraphStore", return_value=store),
                redirect_stdout(output),
            ):
                edited = main(
                    [
                        "annotation",
                        "edit",
                        "review_demo",
                        "warehouse.T1.C1",
                        "--input",
                        str(patch_path),
                        "--reason",
                        "Data owner supplied the description.",
                        "--format",
                        "json",
                    ]
                )
                validated = main(
                    [
                        "annotation",
                        "validate",
                        "review_demo",
                        "warehouse.T1.C1",
                        "--reason",
                        "Field meaning approved.",
                        "--format",
                        "json",
                    ]
                )
                listed = main(
                    [
                        "annotation",
                        "list",
                        "review_demo",
                        "--state",
                        "validated",
                        "--format",
                        "json",
                    ]
                )

        documents = [json.loads(item) for item in _split_json_documents(output.getvalue())]
        self.assertEqual((edited, validated, listed), (0, 0, 0))
        self.assertEqual(documents[0]["annotation"]["state"], "draft")
        self.assertEqual(documents[1]["annotation"]["state"], "validated")
        self.assertEqual(documents[2]["count"], 1)
        self.assertEqual(documents[2]["annotations"][0]["reference"], "warehouse.T1.C1")

    def test_reviewed_annotation_cannot_be_overwritten_without_explicit_reset(self) -> None:
        graph, _record = decide_annotation(
            _review_graph(),
            "warehouse.T1",
            state="deferred",
            reason="Review needs domain input.",
        )
        task = plan_annotation_tasks(graph, missing_only=False)[0]
        envelope = AnnotationProposalEnvelope.from_dict(
            {
                "annotation": {
                    "confidence": 0.9,
                    "confidence_reason": "Technical evidence.",
                    "description": "Replacement proposal.",
                    "evidence": [_evidence("object_name", "warehouse.T1")],
                    "fields": [
                        {
                            "confidence": 0.9,
                            "confidence_reason": "Technical evidence.",
                            "description": "Replacement field proposal.",
                            "evidence": [_evidence("field_name", "C1")],
                            "name": "C1",
                            "role": "measure",
                            "semantic_type": "currency",
                            "synonyms": [],
                            "warnings": [],
                        }
                    ],
                    "grain": "Replacement grain.",
                    "role": "fact",
                    "synonyms": [],
                    "warnings": [],
                },
                "target_id": task.target_id,
                "task_id": task.id,
            }
        )

        with self.assertRaisesRegex(AnnotationFailure, "human-reviewed"):
            apply_annotation_proposal(graph, envelope, source="provider")


def _review_graph() -> GraphDocument:
    graph = build_graph_from_catalog(
        "review_demo",
        CatalogResult(
            connector="test",
            source_type="database",
            catalog="ReviewDemo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="warehouse",
                    name="T1",
                    kind="table",
                    fields=(CatalogField("C1", 1, "decimal", False),),
                ),
            ),
        ),
    )
    return replace(
        graph,
        nodes=tuple(
            replace(
                node,
                metadata={
                    **node.metadata,
                    **(
                        {"grain": "Provider-proposed grain."}
                        if node.type == "table"
                        else {"semantic_type": "currency"}
                    ),
                },
                annotation=GraphAnnotation(
                    description=(
                        "Proposed business meaning."
                        if node.type == "table"
                        else "Provider field proposal."
                    ),
                    confidence=0.8,
                    confidence_reason="Inferred from technical evidence.",
                    provenance=AnnotationProvenance(
                        source="provider",
                        provider="test",
                        model="test-model",
                    ),
                ),
            )
            for node in graph.nodes
        ),
    )


def _evidence(source: str, reference: str) -> dict[str, object]:
    return {"reason": None, "reference": reference, "source": source, "value": reference}


def _split_json_documents(value: str) -> tuple[str, ...]:
    decoder = json.JSONDecoder()
    documents: list[str] = []
    position = 0
    while position < len(value):
        while position < len(value) and value[position].isspace():
            position += 1
        if position >= len(value):
            break
        _payload, end = decoder.raw_decode(value, position)
        documents.append(value[position:end])
        position = end
    return tuple(documents)
