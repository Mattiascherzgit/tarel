"""OpenRouter adapter implemented with the Python standard library."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tarel.providers.config import REASONING_EFFORTS, OpenRouterConfig
from tarel.providers.contracts import ProviderFailure, StructuredRequest


class OpenRouterProvider:
    def __init__(self, config: OpenRouterConfig, *, timeout: float = 120.0) -> None:
        self.config = config
        self.name = config.name
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
        if request.max_output_tokens is not None:
            if request.max_output_tokens < 1:
                raise ProviderFailure(
                    "invalid_provider_request",
                    "OpenRouter completion-token limit must be positive.",
                )
            payload["max_tokens"] = request.max_output_tokens
        reasoning_effort = request.reasoning_effort or self.config.reasoning_effort
        if reasoning_effort is not None:
            if reasoning_effort not in REASONING_EFFORTS:
                raise ProviderFailure(
                    "invalid_provider_request",
                    "OpenRouter reasoning effort is invalid.",
                )
            payload["reasoning"] = {
                "effort": reasoning_effort,
                "exclude": True,
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
        return _structured_content(response_data, provider_label="OpenRouter")


def _structured_content(data: Any, *, provider_label: str) -> dict[str, object]:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderFailure(
            "invalid_provider_response",
            f"{provider_label} response did not contain message content.",
        ) from exc
    tool_result = _tool_result(message)
    if tool_result is not None:
        if isinstance(tool_result, dict):
            return tool_result
        return _json_object(tool_result, provider_label=provider_label)
    content = message.get("content") if isinstance(message, dict) else None
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
            f"{provider_label} response content was not structured JSON.",
        )
    return _json_object(content, provider_label=provider_label)


def _tool_result(message: object) -> dict[str, object] | str | None:
    if not isinstance(message, dict):
        return None
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return None
    for item in tool_calls:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict) or function.get("name") != "submit_tarel_result":
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, (dict, str)):
            return arguments
    return None


def _json_object(content: str, *, provider_label: str) -> dict[str, object]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProviderFailure(
            "invalid_provider_response",
            f"{provider_label} response content was not valid JSON.",
        ) from exc
    if not isinstance(parsed, dict):
        raise ProviderFailure(
            "invalid_provider_response",
            f"{provider_label} structured response must be a JSON object.",
        )
    return parsed
