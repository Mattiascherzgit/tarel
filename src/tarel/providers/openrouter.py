"""OpenRouter adapter implemented with the Python standard library."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tarel.providers.config import OpenRouterConfig
from tarel.providers.contracts import ProviderFailure, StructuredRequest


class OpenRouterProvider:
    name = "openrouter"

    def __init__(self, config: OpenRouterConfig, *, timeout: float = 120.0) -> None:
        self.config = config
        self.timeout = timeout
        self.default_model = config.model

    def generate_structured(self, request: StructuredRequest) -> dict[str, object]:
        payload = {
            "messages": [message.to_dict() for message in request.messages],
            "model": request.model or self.config.model,
            "provider": {"require_parameters": True},
            "response_format": {
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                },
                "type": "json_schema",
            },
            "stream": False,
            "temperature": request.temperature,
        }
        http_request = Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/tarel-ai/tarel",
                "X-OpenRouter-Title": "TAREL",
            },
            method="POST",
        )
        try:
            # The provider config boundary accepts only credential-free HTTPS base URLs.
            with urlopen(http_request, timeout=self.timeout) as response:  # nosec B310
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderFailure(
                "provider_http_error",
                f"OpenRouter request failed with HTTP {exc.code}.",
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ProviderFailure("provider_unavailable", "OpenRouter request failed.") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderFailure(
                "invalid_provider_response",
                "OpenRouter returned an unreadable response.",
            ) from exc
        return _structured_content(response_data)


def _structured_content(data: Any) -> dict[str, object]:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderFailure(
            "invalid_provider_response",
            "OpenRouter response did not contain message content.",
        ) from exc
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        content = "".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
        )
    if not isinstance(content, str):
        raise ProviderFailure(
            "invalid_provider_response",
            "OpenRouter response content was not structured JSON.",
        )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProviderFailure(
            "invalid_provider_response",
            "OpenRouter response content was not valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise ProviderFailure(
            "invalid_provider_response",
            "OpenRouter structured response must be a JSON object.",
        )
    return parsed
