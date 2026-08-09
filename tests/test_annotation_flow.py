import json
import os
import stat
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from tarel.annotations.apply import apply_annotation_proposal
from tarel.annotations.contracts import AnnotationFailure, AnnotationProposalEnvelope
from tarel.annotations.runner import _reject_protected_values, run_annotation_batch
from tarel.annotations.tasks import plan_annotation_tasks
from tarel.connectors.contracts import (
    CatalogField,
    CatalogObject,
    CatalogRelationship,
    CatalogResult,
    SampleResult,
)
from tarel.graph.build import build_graph_from_catalog
from tarel.providers.config import (
    check_openrouter,
    check_provider,
    configure_http_provider,
    configure_openrouter,
    load_http_provider_config,
    load_openrouter_config,
)
from tarel.providers.contracts import ProviderFailure


class AnnotationFlowTests(TestCase):
    def test_task_contains_structural_evidence_and_explicit_sample(self) -> None:
        catalog = CatalogResult(
            connector="test",
            source_type="database",
            catalog="Demo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="sales",
                    name="Customer",
                    kind="table",
                    fields=(
                        CatalogField(
                            "CustomerId",
                            1,
                            "integer",
                            False,
                            description="Technical customer key.",
                            is_primary_key=True,
                        ),
                    ),
                    description="Imported customer records.",
                    primary_key=("CustomerId",),
                ),
                CatalogObject(
                    namespace="sales",
                    name="Order",
                    kind="table",
                    fields=(CatalogField("CustomerId", 1, "integer", False),),
                ),
            ),
            relationships=(
                CatalogRelationship(
                    name="FK_Order_Customer",
                    from_namespace="sales",
                    from_object="Order",
                    from_fields=("CustomerId",),
                    to_namespace="sales",
                    to_object="Customer",
                    to_fields=("CustomerId",),
                ),
            ),
        )
        graph = build_graph_from_catalog("demo", catalog)
        customer_id = next(node.id for node in graph.nodes if node.label == "sales.Customer")
        sample = SampleResult(
            connector="test",
            catalog="Demo",
            namespace="sales",
            object_name="Customer",
            selected_fields=("CustomerId",),
            omitted_fields=(),
            ordered_by=("CustomerId",),
            rows=({"CustomerId": 1},),
            truncated_values=False,
        )

        task = plan_annotation_tasks(graph, samples_by_target={customer_id: sample})[0]
        serialized_context = task.request.messages[1].content.split("\n\n", 1)[1]
        context = json.loads(serialized_context.split("\n\nSAMPLE POLICY:", 1)[0])

        self.assertEqual(context["object"]["primary_key"], ["CustomerId"])
        self.assertEqual(context["object"]["technical_description"], "Imported customer records.")
        self.assertEqual(context["relationships"][0]["direction"], "incoming")
        self.assertEqual(context["sample"]["rows"], [{"CustomerId": 1}])
        self.assertEqual(task.protected_values, ("1",))
        with self.assertRaisesRegex(AnnotationFailure, "protected sample value"):
            _reject_protected_values({"evidence": {"value": "1"}}, task.protected_values)

    def test_agent_proposal_round_trip(self) -> None:
        catalog = CatalogResult(
            connector="test",
            source_type="database",
            catalog="Demo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="sales",
                    name="Customer",
                    kind="table",
                    fields=(CatalogField("CustomerId", 1, "integer", False),),
                ),
            ),
        )
        graph = build_graph_from_catalog("demo", catalog)
        task = plan_annotation_tasks(graph)[0]
        envelope = AnnotationProposalEnvelope.from_dict(
            {
                "annotation": {
                    "confidence": 0.9,
                    "confidence_reason": "The names are direct technical evidence.",
                    "description": "Customer records.",
                    "evidence": [_evidence("object_name", "sales.Customer")],
                    "fields": [
                        {
                            "confidence": 0.95,
                            "confidence_reason": "The field name identifies a customer key.",
                            "description": "Customer identifier.",
                            "evidence": [_evidence("field_name", "CustomerId")],
                            "name": "CustomerId",
                            "role": "key",
                            "semantic_type": "identifier",
                            "synonyms": [],
                            "warnings": [],
                        }
                    ],
                    "grain": "One customer per row.",
                    "role": "dimension",
                    "synonyms": [],
                    "warnings": [],
                },
                "target_id": task.target_id,
                "task_id": task.id,
            }
        )

        updated = apply_annotation_proposal(graph, envelope, source="agent")

        node = updated.node_by_id()[task.target_id]
        self.assertEqual(node.annotation.state, "draft")
        self.assertEqual(node.annotation.provenance.source, "agent")

    def test_provider_config_is_private_and_redacted(self) -> None:
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
        ):
            path = configure_openrouter(api_key="secret-value", model="test/model")
            check = check_openrouter().to_dict()

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertTrue(check["configured"])
            self.assertNotIn("secret-value", str(check))
            expected = Path(temporary_directory) / "tarel/providers/openrouter.toml"
            self.assertEqual(path, expected)

    def test_local_endpoint_profile_is_private_and_needs_no_api_key(self) -> None:
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
        ):
            path = configure_http_provider(
                "local",
                adapter="openai-compatible",
                api_key=None,
                model="qwen-local",
                base_url="http://127.0.0.1:8080/v1",
                structured_mode="tool",
                allow_no_api_key=True,
            )
            config = load_http_provider_config("local")
            check = check_provider("local").to_dict()

            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertIsNone(config.api_key)
            self.assertEqual(config.adapter, "openai-compatible")
            self.assertEqual(config.structured_mode, "tool")
            self.assertTrue(check["configured"])
            self.assertEqual(check["model"], "qwen-local")

    def test_local_profile_cannot_hide_a_remote_endpoint(self) -> None:
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
            self.assertRaisesRegex(ProviderFailure, "loopback endpoint"),
        ):
            configure_http_provider(
                "local",
                adapter="openai-compatible",
                api_key=None,
                model="remote-model",
                base_url="https://inference.example.test/v1",
                allow_no_api_key=True,
            )

    def test_no_api_key_removes_a_previous_local_key(self) -> None:
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
        ):
            options = {
                "adapter": "openai-compatible",
                "base_url": "http://127.0.0.1:8080/v1",
                "model": "qwen-local",
            }
            configure_http_provider("local", api_key="old-secret", **options)
            path = configure_http_provider(
                "local",
                api_key=None,
                allow_no_api_key=True,
                **options,
            )

            self.assertNotIn("old-secret", path.read_text(encoding="utf-8"))
            self.assertIsNone(load_http_provider_config("local").api_key)

    def test_remote_http_profile_requires_key_unless_explicitly_disabled(self) -> None:
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
            self.assertRaisesRegex(ProviderFailure, "requires an API key"),
        ):
            configure_http_provider(
                "corporate",
                adapter="openai-compatible",
                api_key=None,
                model="private-model",
                base_url="https://inference.example.test/v1",
            )

    def test_provider_rejects_unsafe_base_urls_from_cli_and_environment(self) -> None:
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
        ):
            with self.assertRaisesRegex(ProviderFailure, "must use HTTPS"):
                configure_openrouter(
                    api_key="secret-value",
                    model="test/model",
                    base_url="file:///tmp/provider",
                )
            configure_openrouter(api_key="secret-value", model="test/model")
            with (
                patch.dict(
                    os.environ,
                    {"TAREL_OPENROUTER_BASE_URL": "https://user@example.test/api"},
                    clear=False,
                ),
                self.assertRaisesRegex(ProviderFailure, "without credentials"),
            ):
                load_openrouter_config()

    def test_invalid_provider_proposal_is_retried_with_validation_feedback(self) -> None:
        catalog = CatalogResult(
            connector="test",
            source_type="database",
            catalog="Demo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="sales",
                    name="Customer",
                    kind="table",
                    fields=(CatalogField("CustomerId", 1, "integer", False),),
                ),
            ),
        )
        graph = build_graph_from_catalog("demo", catalog)
        task = plan_annotation_tasks(graph)[0]
        provider = _CorrectingProvider()

        updated, result = run_annotation_batch(
            graph,
            (task,),
            provider,
            workers=1,
            retry=1,
            retry_backoff=0,
            skip_errors=False,
            max_errors=None,
            model=None,
        )

        self.assertEqual(result.annotated, 1)
        self.assertEqual(len(provider.requests), 2)
        correction = provider.requests[1].messages[-1].content
        self.assertIn("invalid_proposal", correction)
        self.assertIn("Do not omit any supplied field", correction)
        self.assertEqual(updated.node_by_id()[task.target_id].annotation.state, "draft")

    def test_sample_echo_retry_uses_redacted_draft_without_raw_sample(self) -> None:
        catalog = CatalogResult(
            connector="test",
            source_type="database",
            catalog="Demo",
            dialect="ansi",
            objects=(
                CatalogObject(
                    namespace="sales",
                    name="Customer",
                    kind="table",
                    fields=(CatalogField("CustomerId", 1, "integer", False),),
                ),
            ),
        )
        graph = build_graph_from_catalog("demo", catalog)
        target_id = next(node.id for node in graph.nodes if node.label == "sales.Customer")
        sample = SampleResult(
            connector="test",
            catalog="Demo",
            namespace="sales",
            object_name="Customer",
            selected_fields=("CustomerId",),
            omitted_fields=(),
            ordered_by=("CustomerId",),
            rows=({"CustomerId": "Sensitive Example"},),
            truncated_values=False,
        )
        task = plan_annotation_tasks(graph, samples_by_target={target_id: sample})[0]
        provider = _SampleEchoCorrectingProvider()

        updated, result = run_annotation_batch(
            graph,
            (task,),
            provider,
            workers=1,
            retry=1,
            retry_backoff=0,
            skip_errors=False,
            max_errors=None,
            model=None,
        )

        self.assertEqual(result.annotated, 1)
        retry_text = "\n".join(message.content for message in provider.requests[1].messages)
        self.assertNotIn("Sensitive Example", retry_text)
        self.assertIn("[redacted: repeated sample value]", retry_text)
        self.assertEqual(updated.node_by_id()[task.target_id].annotation.state, "draft")


def _evidence(source: str, reference: str) -> dict[str, object]:
    return {"reason": None, "reference": reference, "source": source, "value": reference}


class _CorrectingProvider:
    name = "test-provider"
    default_model = "test-model"

    def __init__(self) -> None:
        self.requests = []

    def generate_structured(self, request):
        self.requests.append(request)
        fields = []
        if len(self.requests) > 1:
            fields = [
                {
                    "confidence": 0.9,
                    "confidence_reason": "The field name is direct evidence.",
                    "description": "Customer identifier.",
                    "evidence": [_evidence("field_name", "CustomerId")],
                    "name": "CustomerId",
                    "role": "key",
                    "semantic_type": "identifier",
                    "synonyms": [],
                    "warnings": [],
                }
            ]
        return {
            "confidence": 0.9,
            "confidence_reason": "The object name is direct evidence.",
            "description": "Customer records.",
            "evidence": [_evidence("object_name", "sales.Customer")],
            "fields": fields,
            "grain": "One customer per row.",
            "role": "dimension",
            "synonyms": [],
            "warnings": [],
        }


class _SampleEchoCorrectingProvider(_CorrectingProvider):
    def generate_structured(self, request):
        result = super().generate_structured(request)
        if len(self.requests) == 1:
            result["description"] = "Sensitive Example"
        return result
