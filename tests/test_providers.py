import json
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from tarel.providers.config import OpenRouterConfig
from tarel.providers.contracts import Message, StructuredRequest
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
