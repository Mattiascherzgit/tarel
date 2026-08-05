"""Explicit provider loading without import-time registration."""

from __future__ import annotations

from tarel.providers.config import load_openrouter_config
from tarel.providers.contracts import ProviderFailure, StructuredProvider
from tarel.providers.openrouter import OpenRouterProvider


def load_provider(name: str, *, timeout: float = 120.0) -> StructuredProvider:
    if name == "openrouter":
        return OpenRouterProvider(load_openrouter_config(), timeout=timeout)
    raise ProviderFailure("unknown_provider", f"Unknown annotation provider: {name}")
