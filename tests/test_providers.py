import json
import os
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from tarel.cli import main
from tarel.providers.authoring import scaffold_provider
from tarel.providers.config import (
    HTTPProviderConfig,
    OpenRouterConfig,
    configure_installed_provider,
)
from tarel.providers.contracts import Message, ProviderFailure, StructuredRequest
from tarel.providers.host import load_provider
from tarel.providers.openai_compatible import OpenAICompatibleProvider
from tarel.providers.openrouter import OpenRouterProvider


class _Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class OpenRouterProviderTests(TestCase):
    def test_structured_request_bounds_reasoning_output(self) -> None:
        response = _Response(
            json.dumps(
                {"choices": [{"message": {"content": json.dumps({"status": "ok"})}}]}
            ).encode()
        )
        provider = OpenRouterProvider(
            OpenRouterConfig("secret", "deepseek/deepseek-v4-flash", "https://openrouter.ai/api/v1")
        )
        request = StructuredRequest(
            messages=(Message("user", "test"),),
            schema_name="Test",
            schema={"type": "object"},
            max_output_tokens=24_000,
            reasoning_effort="high",
        )

        with patch("tarel.providers.openrouter.urlopen", return_value=response) as call:
            result = provider.generate_structured(request)

        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(payload["max_tokens"], 24_000)
        self.assertEqual(payload["reasoning"], {"effort": "high", "exclude": True})

    def test_profile_default_can_disable_reasoning(self) -> None:
        response = _Response(
            json.dumps(
                {"choices": [{"message": {"content": json.dumps({"status": "ok"})}}]}
            ).encode()
        )
        provider = OpenRouterProvider(
            OpenRouterConfig(
                "secret",
                "qwen/qwen3.6-27b",
                "https://openrouter.ai/api/v1",
                reasoning_effort="none",
            )
        )
        request = StructuredRequest(
            messages=(Message("user", "test"),),
            schema_name="Test",
            schema={"type": "object"},
        )

        with patch("tarel.providers.openrouter.urlopen", return_value=response) as call:
            provider.generate_structured(request)

        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(payload["reasoning"], {"effort": "none", "exclude": True})


class OpenAICompatibleProviderTests(TestCase):
    def test_local_endpoint_uses_forced_tool_without_authorization(self) -> None:
        response = _Response(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "arguments": json.dumps({"status": "ok"}),
                                            "name": "submit_tarel_result",
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            ).encode()
        )
        provider = OpenAICompatibleProvider(
            HTTPProviderConfig(
                "local",
                "openai-compatible",
                None,
                "qwen-local",
                "http://127.0.0.1:8080/v1",
                structured_mode="tool",
            )
        )
        request = StructuredRequest(
            messages=(Message("user", "test"),),
            schema_name="Test",
            schema={"type": "object"},
            max_output_tokens=128,
        )

        with patch("tarel.providers.openai_compatible.urlopen", return_value=response) as call:
            result = provider.generate_structured(request)

        http_request = call.call_args.args[0]
        payload = json.loads(http_request.data)
        self.assertEqual(result, {"status": "ok"})
        self.assertNotIn("Authorization", dict(http_request.header_items()))
        self.assertEqual(payload["tool_choice"]["function"]["name"], "submit_tarel_result")
        self.assertEqual(payload["max_tokens"], 128)

    def test_json_schema_mode_accepts_message_content(self) -> None:
        response = _Response(
            json.dumps(
                {"choices": [{"message": {"content": json.dumps({"status": "ok"})}}]}
            ).encode()
        )
        provider = OpenAICompatibleProvider(
            HTTPProviderConfig(
                "corporate",
                "openai-compatible",
                "secret",
                "deepseek-private",
                "https://inference.example.test/v1",
            )
        )
        request = StructuredRequest(
            messages=(Message("user", "test"),),
            schema_name="Test",
            schema={"type": "object"},
        )

        with patch("tarel.providers.openai_compatible.urlopen", return_value=response) as call:
            result = provider.generate_structured(request)

        payload = json.loads(call.call_args.args[0].data)
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(payload["response_format"]["type"], "json_schema")


class ProviderScaffoldTests(TestCase):
    def test_scaffold_is_inactive_and_declares_reviewed_entry_point(self) -> None:
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            target = Path(temporary_directory) / "vendor"

            result = scaffold_provider("vendor", output=target)

            self.assertEqual(result.path, target)
            pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
            task = (target / "PROVIDER_TASK.md").read_text(encoding="utf-8")
            self.assertIn('entry-points."tarel.providers"', pyproject)
            self.assertIn("human has reviewed", task)

    def test_default_scaffold_stays_under_the_ignored_tarel_directory(self) -> None:
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            working_directory = Path(temporary_directory)
            with patch("pathlib.Path.cwd", return_value=working_directory):
                result = scaffold_provider("anthropic")

            self.assertEqual(
                result.path,
                working_directory / ".tarel/providers/anthropic",
            )
            self.assertTrue((result.path / "PROVIDER_TASK.md").is_file())

    def test_installed_adapter_is_loaded_only_through_its_entry_point(self) -> None:
        provider = Mock()
        provider.generate_structured = Mock(return_value={"status": "ok"})
        factory = Mock(return_value=provider)
        entry_point = Mock()
        entry_point.load.return_value = factory
        entry_points = Mock()
        entry_points.select.return_value = (entry_point,)
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
            patch("tarel.providers.host.entry_points", return_value=entry_points),
        ):
            configure_installed_provider(
                "warehouse-vendor",
                adapter="vendor",
                api_key="secret",
                model="model",
                base_url="https://provider.example.test/v1",
            )

            loaded = load_provider("warehouse-vendor", timeout=15.0)

        self.assertIs(loaded, provider)
        entry_points.select.assert_called_once_with(group="tarel.providers", name="vendor")
        self.assertEqual(factory.call_args.kwargs["name"], "warehouse-vendor")
        self.assertEqual(factory.call_args.kwargs["timeout"], 15.0)
        self.assertEqual(factory.call_args.kwargs["config"]["api_key"], "secret")


class ProviderCLITests(TestCase):
    def test_local_profile_can_be_configured_and_listed_without_network_access(self) -> None:
        with (
            TemporaryDirectory(dir="/tmp") as temporary_directory,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": temporary_directory}, clear=False),
        ):
            output = StringIO()
            with redirect_stdout(output):
                configured = main(
                    [
                        "provider",
                        "configure",
                        "local",
                        "--no-api-key",
                        "--model",
                        "qwen-local",
                        "--base-url",
                        "http://127.0.0.1:8080/v1",
                        "--structured-mode",
                        "tool",
                    ]
                )
            listed_output = StringIO()
            with redirect_stdout(listed_output):
                listed = main(["provider", "list", "--format", "json"])

        profiles = json.loads(listed_output.getvalue())
        local = next(item for item in profiles if item["name"] == "local")
        self.assertEqual(configured, 0)
        self.assertEqual(listed, 0)
        self.assertTrue(local["configured"])
        self.assertEqual(local["adapter"], "openai-compatible")

    def test_cli_scaffold_defaults_to_local_tarel_directory(self) -> None:
        with TemporaryDirectory(dir="/tmp") as temporary_directory:
            working_directory = Path(temporary_directory)
            output = StringIO()
            with (
                patch("pathlib.Path.cwd", return_value=working_directory),
                redirect_stdout(output),
            ):
                exit_code = main(["provider", "scaffold", "vendor"])

            target = working_directory / ".tarel/providers/vendor"
            self.assertEqual(exit_code, 0)
            self.assertTrue((target / "PROVIDER_TASK.md").is_file())
            self.assertIn(str(target), output.getvalue())


class InvalidProviderResponseTests(TestCase):
    def test_local_tool_response_must_be_json(self) -> None:
        response = _Response(
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {
                                        "function": {
                                            "arguments": "not json",
                                            "name": "submit_tarel_result",
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            ).encode()
        )
        provider = OpenAICompatibleProvider(
            HTTPProviderConfig(
                "local",
                "openai-compatible",
                None,
                "qwen-local",
                "http://127.0.0.1:8080/v1",
                structured_mode="tool",
            )
        )
        request = StructuredRequest(
            messages=(Message("user", "test"),),
            schema_name="Test",
            schema={"type": "object"},
        )

        with (
            patch("tarel.providers.openai_compatible.urlopen", return_value=response),
            self.assertRaisesRegex(ProviderFailure, "not valid JSON"),
        ):
            provider.generate_structured(request)
