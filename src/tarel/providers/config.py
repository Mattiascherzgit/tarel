"""Private user-level provider configuration with redacted public views."""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tarel.providers.contracts import ProviderCheck, ProviderFailure

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"


@dataclass(frozen=True, slots=True)
class OpenRouterConfig:
    api_key: str
    model: str
    base_url: str


def provider_config_path(name: str) -> Path:
    xdg_config = os.getenv("XDG_CONFIG_HOME")
    root = Path(xdg_config).expanduser() if xdg_config else Path.home() / ".config"
    return root / "tarel" / "providers" / f"{name}.toml"


def configure_openrouter(
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> Path:
    current = _read_provider_file("openrouter")
    selected_key = (api_key or _optional_text(current.get("api_key")) or "").strip()
    if not selected_key:
        raise ProviderFailure("missing_api_key", "OpenRouter configuration requires an API key.")
    selected_model = (
        model or _optional_text(current.get("model")) or DEFAULT_OPENROUTER_MODEL
    ).strip()
    selected_base_url = (
        base_url or _optional_text(current.get("base_url")) or DEFAULT_OPENROUTER_BASE_URL
    ).strip()
    if not selected_model or not selected_base_url:
        raise ProviderFailure("invalid_config", "OpenRouter model and base URL cannot be empty.")
    selected_base_url = _validated_base_url(selected_base_url)

    path = provider_config_path("openrouter")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "[provider]",
                'name = "openrouter"',
                f"api_key = {_toml_quote(selected_key)}",
                f"model = {_toml_quote(selected_model)}",
                f"base_url = {_toml_quote(selected_base_url)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return path


def load_openrouter_config() -> OpenRouterConfig:
    data = _read_provider_file("openrouter")
    api_key = (
        os.getenv("TAREL_OPENROUTER_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or _optional_text(data.get("api_key"))
    )
    model = (
        os.getenv("TAREL_OPENROUTER_MODEL")
        or _optional_text(data.get("model"))
        or DEFAULT_OPENROUTER_MODEL
    )
    base_url = (
        os.getenv("TAREL_OPENROUTER_BASE_URL")
        or _optional_text(data.get("base_url"))
        or DEFAULT_OPENROUTER_BASE_URL
    )
    if not api_key:
        raise ProviderFailure(
            "provider_not_configured",
            "OpenRouter is not configured. Run `tarel provider configure openrouter`.",
        )
    return OpenRouterConfig(
        api_key=api_key,
        model=model,
        base_url=_validated_base_url(base_url),
    )


def check_openrouter() -> ProviderCheck:
    path = provider_config_path("openrouter")
    try:
        config = load_openrouter_config()
    except ProviderFailure:
        return ProviderCheck(
            name="openrouter",
            configured=False,
            model=None,
            base_url=None,
            config_path=str(path),
        )
    return ProviderCheck(
        name="openrouter",
        configured=True,
        model=config.model,
        base_url=config.base_url,
        config_path=str(path),
    )


def _read_provider_file(name: str) -> dict[str, Any]:
    path = provider_config_path(name)
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ProviderFailure("invalid_config", f"Could not read provider config: {path}") from exc
    provider = data.get("provider", {})
    if not isinstance(provider, dict):
        raise ProviderFailure("invalid_config", f"Provider config is invalid: {path}")
    return provider


def _optional_text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _validated_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderFailure(
            "invalid_config",
            "OpenRouter base URL must be an HTTPS URL without credentials, query, or fragment.",
        )
    return normalized


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
