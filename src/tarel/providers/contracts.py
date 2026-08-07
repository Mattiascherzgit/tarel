"""Small provider-neutral structured-generation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ProviderFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class StructuredRequest:
    messages: tuple[Message, ...]
    schema_name: str
    schema: dict[str, object]
    model: str | None = None
    temperature: float = 0.0
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderCheck:
    name: str
    configured: bool
    model: str | None
    base_url: str | None
    config_path: str

    def to_dict(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "config_path": self.config_path,
            "configured": self.configured,
            "model": self.model,
            "name": self.name,
        }


class StructuredProvider(Protocol):
    name: str
    default_model: str | None

    def generate_structured(self, request: StructuredRequest) -> dict[str, object]: ...
