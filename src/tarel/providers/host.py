"""Explicit provider loading without import-time registration."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import cast

from tarel.providers.config import (
    OpenRouterConfig,
    load_http_provider_config,
    load_openrouter_config,
    load_provider_mapping,
    provider_adapter,
)
from tarel.providers.contracts import ProviderFailure, StructuredProvider
from tarel.providers.openai_compatible import OpenAICompatibleProvider
from tarel.providers.openrouter import OpenRouterProvider


def load_provider(name: str, *, timeout: float = 120.0) -> StructuredProvider:
    adapter = provider_adapter(name)
    if adapter == "openrouter" and name == "openrouter":
        return OpenRouterProvider(load_openrouter_config(), timeout=timeout)
    if adapter == "openrouter":
        config = load_http_provider_config(name)
        if config.api_key is None:
            raise ProviderFailure(
                "provider_not_configured",
                f"Provider profile {name} requires an API key.",
            )
        return OpenRouterProvider(
            OpenRouterConfig(
                api_key=config.api_key,
                model=config.model,
                base_url=config.base_url,
                reasoning_effort=config.reasoning_effort,
                name=name,
            ),
            timeout=timeout,
        )
    if adapter == "openai-compatible":
        return OpenAICompatibleProvider(load_http_provider_config(name), timeout=timeout)
    return _load_installed_provider(name, adapter, timeout=timeout)


def _load_installed_provider(name: str, adapter: str, *, timeout: float) -> StructuredProvider:
    matches = tuple(entry_points().select(group="tarel.providers", name=adapter))
    if len(matches) != 1:
        code = "provider_adapter_not_installed" if not matches else "ambiguous_provider_adapter"
        raise ProviderFailure(code, f"Provider adapter is not uniquely installed: {adapter}")
    try:
        factory = matches[0].load()
        provider = factory(
            name=name,
            config=load_provider_mapping(name),
            timeout=timeout,
        )
    except ProviderFailure:
        raise
    except (AttributeError, ImportError, TypeError) as exc:
        raise ProviderFailure(
            "invalid_provider_adapter",
            f"Could not load provider adapter: {adapter}",
        ) from exc
    if not callable(getattr(provider, "generate_structured", None)):
        raise ProviderFailure(
            "invalid_provider_adapter",
            f"Provider adapter does not implement generate_structured: {adapter}",
        )
    return cast(StructuredProvider, provider)
