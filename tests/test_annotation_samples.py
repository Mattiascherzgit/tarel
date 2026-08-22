from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.annotations.contracts import AnnotationFailure
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult, SampleResult
from tarel.sdk import Tarel

_PROTECTED_VALUE = "Private Customer 9472"


class CallerSuppliedAnnotationSampleTests(TestCase):
    def test_sdk_uses_authorized_sample_without_persisting_its_values(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            graph = sdk.graph.import_catalog("composite", _catalog()).graph
            target_id = _target_id(graph)
            provider = _Provider()

            with patch("tarel.application.load_provider", return_value=provider):
                result = sdk.annotation.run(
                    "composite",
                    provider="test",
                    samples_by_target={target_id: _sample()},
                )

            persisted = result.path.read_text(encoding="utf-8")
            request = provider.requests[0].messages[1].content

        self.assertEqual(result.run.annotated, 1)
        self.assertIn(_PROTECTED_VALUE, request)
        self.assertNotIn(_PROTECTED_VALUE, persisted)
        self.assertNotIn('"sample"', persisted)
        self.assertEqual(result.graph.node_by_id()[target_id].annotation.state, "draft")

    def test_sample_value_echo_fails_without_changing_the_graph(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            imported = sdk.graph.import_catalog("composite", _catalog())
            target_id = _target_id(imported.graph)
            before = imported.path.read_bytes()

            with (
                patch("tarel.application.load_provider", return_value=_Provider(echo_sample=True)),
                self.assertRaises(AnnotationFailure) as raised,
            ):
                sdk.annotation.run(
                    "composite",
                    provider="test",
                    samples_by_target={target_id: _sample()},
                )

            after = imported.path.read_bytes()

        self.assertEqual(raised.exception.code, "batch_failed")
        self.assertEqual(after, before)
        self.assertNotIn(_PROTECTED_VALUE.encode(), after)

    def test_more_than_ten_rows_is_rejected_before_provider_loading(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            graph = sdk.graph.import_catalog("composite", _catalog()).graph
            target_id = _target_id(graph)
            sample = replace(_sample(), rows=_sample().rows * 11)

            with (
                patch("tarel.application.load_provider") as load_provider,
                self.assertRaises(AnnotationFailure) as raised,
            ):
                sdk.annotation.run(
                    "composite",
                    provider="test",
                    samples_by_target={target_id: sample},
                )

        self.assertEqual(raised.exception.code, "invalid_annotation_samples")
        load_provider.assert_not_called()

    def test_connector_and_caller_sampling_are_mutually_exclusive(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            graph = sdk.graph.import_catalog("composite", _catalog()).graph
            target_id = _target_id(graph)

            with (
                patch("tarel.application.load_provider") as load_provider,
                self.assertRaises(AnnotationFailure) as raised,
            ):
                sdk.annotation.run(
                    "composite",
                    provider="test",
                    sample_limit=1,
                    samples_by_target={target_id: _sample()},
                )

        self.assertEqual(raised.exception.code, "conflicting_annotation_samples")
        load_provider.assert_not_called()

    def test_sample_must_cover_the_exact_graph_fields(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            sdk = Tarel(Path(temporary_directory) / "state")
            graph = sdk.graph.import_catalog("composite", _catalog()).graph
            target_id = _target_id(graph)
            incomplete = replace(_sample(), omitted_fields=())

            with self.assertRaises(AnnotationFailure) as raised:
                sdk.annotation.run(
                    "composite",
                    provider="test",
                    samples_by_target={target_id: incomplete},
                )

        self.assertEqual(raised.exception.code, "annotation_sample_field_mismatch")


def _catalog() -> CatalogResult:
    return CatalogResult(
        connector="composite-observer",
        source_type="federated",
        catalog="CompositeCatalog",
        dialect=None,
        objects=(
            CatalogObject(
                namespace="crm",
                name="Customer",
                kind="table",
                fields=(
                    CatalogField("CustomerId", 1, "integer", False, is_primary_key=True),
                    CatalogField("CustomerName", 2, "text", False),
                ),
                primary_key=("CustomerId",),
            ),
        ),
    )


def _target_id(graph) -> str:
    return next(node.id for node in graph.nodes if node.label == "crm.Customer")


def _sample() -> SampleResult:
    return SampleResult(
        connector="authorized-crm-source",
        catalog="CRM",
        namespace="crm",
        object_name="Customer",
        selected_fields=("CustomerName",),
        omitted_fields=("CustomerId",),
        ordered_by=("CustomerId",),
        rows=({"CustomerName": _PROTECTED_VALUE},),
        truncated_values=False,
    )


class _Provider:
    name = "test-provider"
    default_model = "test-model"

    def __init__(self, *, echo_sample: bool = False) -> None:
        self.echo_sample = echo_sample
        self.requests = []

    def generate_structured(self, request):
        self.requests.append(request)
        return {
            "confidence": 0.8,
            "confidence_reason": "The technical names support this draft.",
            "description": _PROTECTED_VALUE if self.echo_sample else "Customer master records.",
            "evidence": [_evidence("object_name", "crm.Customer")],
            "fields": [
                {
                    "confidence": 0.9,
                    "confidence_reason": "The field name identifies a key.",
                    "description": "Customer identifier.",
                    "evidence": [_evidence("field_name", "CustomerId")],
                    "name": "CustomerId",
                    "role": "key",
                    "semantic_type": "identifier",
                    "synonyms": [],
                    "warnings": [],
                },
                {
                    "confidence": 0.7,
                    "confidence_reason": "The field name indicates a display label.",
                    "description": "Customer display name.",
                    "evidence": [_evidence("field_name", "CustomerName")],
                    "name": "CustomerName",
                    "role": "label",
                    "semantic_type": "name",
                    "synonyms": [],
                    "warnings": [],
                },
            ],
            "grain": "One customer per row.",
            "role": "dimension",
            "synonyms": [],
            "warnings": [],
        }


def _evidence(source: str, reference: str) -> dict[str, object]:
    return {
        "reason": "The technical name is direct evidence.",
        "reference": reference,
        "source": source,
        "value": None,
    }
