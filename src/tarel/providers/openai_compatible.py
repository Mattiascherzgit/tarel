"""Structured generation through OpenAI-compatible HTTP endpoints."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tarel.providers.config import REASONING_EFFORTS, HTTPProviderConfig
from tarel.providers.contracts import ProviderFailure, StructuredRequest
from tarel.providers.openrouter import _structured_content

_RESULT_TOOL = "submit_tarel_result"


class OpenAICompatibleProvider:
    """Use one named profile backed by an OpenAI-compatible Chat Completions API."""

    def __init__(self, config: HTTPProviderConfig, *, timeout: float = 120.0) -> None:
        self.config = config
        self.name = config.name
        self.default_model = config.model
        self.timeout = timeout

    def generate_structured(self, request: StructuredRequest) -> dict[str, object]:
        payload: dict[str, Any] = {
            "messages": [message.to_dict() for message in request.messages],
            "model": request.model or self.config.model,
            "stream": False,
            "temperature": request.temperature,
        }
        if self.config.structured_mode == "tool":
            payload.update(
                {
                    "parallel_tool_calls": False,
                    "tool_choice": {
                        "function": {"name": _RESULT_TOOL},
                        "type": "function",
                    },
                    "tools": [
                        {
                            "function": {
                                "description": (
                                    f"Submit the complete {request.schema_name} result."
                                ),
                                "name": _RESULT_TOOL,
                                "parameters": request.schema,
                            },
                            "type": "function",
                        }
                    ],
                }
            )
        else:
            payload["response_format"] = {
                "json_schema": {
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                },
                "type": "json_schema",
            }
        if request.max_output_tokens is not None:
            if request.max_output_tokens < 1:
                raise ProviderFailure(
                    "invalid_provider_request",
                    "Completion-token limit must be positive.",
                )
            payload["max_tokens"] = request.max_output_tokens
        reasoning_effort = request.reasoning_effort or self.config.reasoning_effort
        if reasoning_effort is not None:
            if reasoning_effort not in REASONING_EFFORTS:
                raise ProviderFailure(
                    "invalid_provider_request",
                    "Provider reasoning effort is invalid.",
                )
            payload["reasoning_effort"] = reasoning_effort
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        http_request = Request(
            f"{self.config.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout) as response:  # nosec B310
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise ProviderFailure(
                "provider_http_error",
                f"Provider request failed with HTTP {exc.code}.",
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ProviderFailure("provider_unavailable", "Provider request failed.") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderFailure(
                "invalid_provider_response",
                "Provider returned an unreadable response.",
            ) from exc
        return _structured_content(response_data, provider_label=self.name)
