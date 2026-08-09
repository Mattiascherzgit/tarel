"""Private provider profiles with redacted public checks."""

from __future__ import annotations

import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tarel.providers.contracts import ProviderCheck, ProviderFailure

DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"
BUILTIN_PROVIDER_ADAPTERS = {
    "local": "openai-compatible",
    "openrouter": "openrouter",
}
HTTP_PROVIDER_ADAPTERS = frozenset({"openai-compatible", "openrouter"})
REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})
STRUCTURED_MODES = frozenset({"json_schema", "tool"})
_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True, slots=True)
class OpenRouterConfig:
    api_key: str
    model: str
    base_url: str
    reasoning_effort: str | None = None
    name: str = "openrouter"


@dataclass(frozen=True, slots=True)
class HTTPProviderConfig:
    name: str
    adapter: str
    api_key: str | None
    model: str
    base_url: str
    reasoning_effort: str | None = None
    structured_mode: str = "json_schema"


def provider_config_path(name: str) -> Path:
    _require_provider_name(name)
    xdg_config = os.getenv("XDG_CONFIG_HOME")
    root = Path(xdg_config).expanduser() if xdg_config else Path.home() / ".config"
    return root / "tarel" / "providers" / f"{name}.toml"


def configure_openrouter(
    *,
    api_key: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    reasoning_effort: str | None = None,
) -> Path:
    current = _read_provider_file("openrouter")
    return configure_http_provider(
        "openrouter",
        adapter="openrouter",
        api_key=api_key,
        model=model or _text(current.get("model")) or DEFAULT_OPENROUTER_MODEL,
        base_url=base_url or _text(current.get("base_url")) or DEFAULT_OPENROUTER_BASE_URL,
        reasoning_effort=reasoning_effort,
    )


def load_openrouter_config() -> OpenRouterConfig:
    config = load_http_provider_config("openrouter")
    if config.api_key is None:
        raise ProviderFailure(
            "provider_not_configured",
            "OpenRouter is not configured. Run `tarel provider configure openrouter`.",
        )
    return OpenRouterConfig(
        api_key=config.api_key,
        model=config.model,
        base_url=config.base_url,
        reasoning_effort=config.reasoning_effort,
    )


def check_openrouter() -> ProviderCheck:
    return _check_http_provider("openrouter", adapter="openrouter")


def configure_http_provider(
    name: str,
    *,
    adapter: str,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
    reasoning_effort: str | None = None,
    structured_mode: str | None = None,
    allow_no_api_key: bool = False,
) -> Path:
    _require_provider_name(name)
    if adapter not in HTTP_PROVIDER_ADAPTERS:
        raise ProviderFailure(
            "invalid_provider_adapter",
            f"HTTP provider adapter is not supported: {adapter}",
        )
    _require_builtin_adapter(name, adapter)
    current = _read_provider_file(name)
    _require_unchanged_adapter(current, adapter)
    selected_model = (model or _text(current.get("model")) or "").strip()
    selected_base_url = (base_url or _text(current.get("base_url")) or "").strip()
    selected_key = (
        None
        if allow_no_api_key and api_key is None
        else api_key or _text(current.get("api_key"))
    )
    if not selected_model or not selected_base_url:
        raise ProviderFailure(
            "invalid_provider_config",
            "HTTP provider profiles require a model and base URL.",
        )
    if not selected_key and (adapter == "openrouter" or not allow_no_api_key):
        raise ProviderFailure("missing_api_key", f"Provider profile {name} requires an API key.")
    selected_reasoning = _reasoning_effort(
        reasoning_effort if reasoning_effort is not None else current.get("reasoning_effort")
    )
    selected_mode = _structured_mode(
        structured_mode if structured_mode is not None else current.get("structured_mode")
    )
    values = {
        "api_key": selected_key,
        "base_url": _validated_profile_base_url(name, selected_base_url),
        "model": selected_model,
        "reasoning_effort": selected_reasoning,
        "structured_mode": selected_mode,
    }
    return _write_profile(name, adapter, values)


def load_http_provider_config(name: str) -> HTTPProviderConfig:
    _require_provider_name(name)
    data = _read_provider_file(name)
    adapter = _text(data.get("adapter")) or BUILTIN_PROVIDER_ADAPTERS.get(name)
    if adapter not in HTTP_PROVIDER_ADAPTERS:
        raise ProviderFailure(
            "provider_not_configured" if not data else "invalid_provider_adapter",
            f"Provider profile {name} is not a configured HTTP provider.",
        )
    if not data and name != "openrouter":
        raise ProviderFailure("provider_not_configured", f"Provider is not configured: {name}")
    _require_matching_profile_name(name, data)
    model = _environment_value(name, adapter, "MODEL") or _text(data.get("model"))
    base_url = _environment_value(name, adapter, "BASE_URL") or _text(data.get("base_url"))
    if name == "openrouter":
        model = model or DEFAULT_OPENROUTER_MODEL
        base_url = base_url or DEFAULT_OPENROUTER_BASE_URL
    if not model or not base_url:
        raise ProviderFailure(
            "invalid_provider_config",
            f"Provider profile {name} requires a model and base URL.",
        )
    api_key = (
        _environment_value(name, adapter, "API_KEY") or _text(data.get("api_key"))
    )
    if adapter == "openrouter" and not api_key:
        raise ProviderFailure(
            "provider_not_configured",
            f"Provider profile {name} requires an API key.",
        )
    return HTTPProviderConfig(
        name=name,
        adapter=adapter,
        api_key=api_key,
        model=model,
        base_url=_validated_profile_base_url(name, base_url),
        reasoning_effort=_reasoning_effort(
            _environment_value(name, adapter, "REASONING_EFFORT")
            or data.get("reasoning_effort")
        ),
        structured_mode=_structured_mode(data.get("structured_mode")),
    )


def configure_installed_provider(
    name: str,
    *,
    adapter: str,
    api_key: str | None,
    model: str | None,
    base_url: str | None,
) -> Path:
    _require_provider_name(name)
    _require_provider_name(adapter)
    if name in BUILTIN_PROVIDER_ADAPTERS or adapter in HTTP_PROVIDER_ADAPTERS:
        raise ProviderFailure(
            "invalid_provider_adapter",
            "Use the built-in adapter configuration path for this provider.",
        )
    current = _read_provider_file(name)
    _require_unchanged_adapter(current, adapter)
    selected_base_url = base_url or _text(current.get("base_url"))
    values = {
        "api_key": api_key or _text(current.get("api_key")),
        "base_url": _validated_base_url(selected_base_url) if selected_base_url else None,
        "model": model or _text(current.get("model")),
    }
    return _write_profile(name, adapter, values)


def provider_adapter(name: str) -> str:
    _require_provider_name(name)
    data = _read_provider_file(name)
    adapter = _text(data.get("adapter")) or BUILTIN_PROVIDER_ADAPTERS.get(name)
    if adapter is None:
        code = "invalid_provider_config" if data else "unknown_provider"
        raise ProviderFailure(code, f"Unknown annotation provider: {name}")
    _require_builtin_adapter(name, adapter)
    return adapter


def list_provider_names() -> tuple[str, ...]:
    root = provider_config_path("local").parent
    configured = (
        {
            path.stem
            for path in root.glob("*.toml")
            if path.is_file() and _PROVIDER_NAME.fullmatch(path.stem)
        }
        if root.exists()
        else set()
    )
    return tuple(sorted(configured | set(BUILTIN_PROVIDER_ADAPTERS)))


def check_provider(name: str) -> ProviderCheck:
    adapter = provider_adapter(name)
    if adapter in HTTP_PROVIDER_ADAPTERS:
        return _check_http_provider(name, adapter=adapter)
    path = provider_config_path(name)
    data = _read_provider_file(name)
    return ProviderCheck(
        name=name,
        configured=bool(data),
        model=_text(data.get("model")),
        base_url=_text(data.get("base_url")),
        config_path=str(path),
        adapter=adapter,
    )


def load_provider_mapping(name: str) -> dict[str, Any]:
    data = _read_provider_file(name)
    if not data:
        raise ProviderFailure("provider_not_configured", f"Provider is not configured: {name}")
    _require_matching_profile_name(name, data)
    return dict(data)


def _check_http_provider(name: str, *, adapter: str) -> ProviderCheck:
    path = provider_config_path(name)
    try:
        config = load_http_provider_config(name)
    except ProviderFailure:
        return ProviderCheck(name, False, None, None, str(path), adapter)
    return ProviderCheck(
        name,
        True,
        config.model,
        config.base_url,
        str(path),
        adapter,
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


def _write_profile(name: str, adapter: str, values: dict[str, str | None]) -> Path:
    lines = [
        "[provider]",
        f"name = {_toml_quote(name)}",
        f"adapter = {_toml_quote(adapter)}",
    ]
    lines.extend(
        f"{key} = {_toml_quote(value)}"
        for key, value in sorted(values.items())
        if value is not None
    )
    path = provider_config_path(name)
    descriptor: int | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = stat.S_IRUSR | stat.S_IWUSR
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write("\n".join([*lines, ""]))
    except OSError as exc:
        raise ProviderFailure(
            "invalid_provider_config",
            f"Could not write provider config: {path}",
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return path


def _environment_value(name: str, adapter: str, suffix: str) -> str | None:
    profile_name = name.upper().replace("-", "_")
    candidates = [
        os.getenv(f"TAREL_PROVIDER_{profile_name}_{suffix}"),
        os.getenv(f"TAREL_PROVIDER_{suffix}"),
    ]
    if adapter == "openrouter":
        candidates.append(os.getenv(f"TAREL_OPENROUTER_{suffix}"))
        if suffix == "API_KEY":
            candidates.append(os.getenv("OPENROUTER_API_KEY"))
    if name == "openai" and suffix == "API_KEY":
        candidates.append(os.getenv("OPENAI_API_KEY"))
    return next((_text(value) for value in candidates if _text(value)), None)


def _require_provider_name(name: str) -> None:
    if not _PROVIDER_NAME.fullmatch(name):
        raise ProviderFailure(
            "invalid_provider_name",
            "Provider names must start with a lowercase letter and contain only "
            "lowercase letters, numbers, underscores, or hyphens.",
        )


def _require_builtin_adapter(name: str, adapter: str) -> None:
    expected = BUILTIN_PROVIDER_ADAPTERS.get(name)
    if expected is not None and expected != adapter:
        raise ProviderFailure(
            "invalid_provider_adapter",
            f"Provider profile {name} uses the reserved {expected} adapter.",
        )


def _require_unchanged_adapter(data: dict[str, Any], adapter: str) -> None:
    current = _text(data.get("adapter"))
    if current is not None and current != adapter:
        raise ProviderFailure(
            "invalid_provider_config",
            "Changing an existing provider profile's adapter requires a new profile name.",
        )


def _require_matching_profile_name(name: str, data: dict[str, Any]) -> None:
    configured_name = _text(data.get("name"))
    if configured_name is not None and configured_name != name:
        raise ProviderFailure(
            "invalid_provider_config",
            "Provider profile name does not match its configuration file.",
        )


def _reasoning_effort(value: Any) -> str | None:
    selected = _text(value)
    if selected is not None and selected not in REASONING_EFFORTS:
        raise ProviderFailure(
            "invalid_provider_config",
            f"Unsupported reasoning effort: {selected}",
        )
    return selected


def _structured_mode(value: Any) -> str:
    selected = _text(value) or "json_schema"
    if selected not in STRUCTURED_MODES:
        raise ProviderFailure(
            "invalid_provider_config",
            f"Unsupported structured output mode: {selected}",
        )
    return selected


def _validated_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    local_http = parsed.scheme == "http" and parsed.hostname in _LOOPBACK_HOSTS
    if (
        (parsed.scheme != "https" and not local_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderFailure(
            "invalid_config",
            "Provider base URL must use HTTPS (or loopback HTTP) without credentials, "
            "query, or fragment.",
        )
    return normalized


def _validated_profile_base_url(name: str, value: str) -> str:
    normalized = _validated_base_url(value)
    if name == "local" and urlsplit(normalized).hostname not in _LOOPBACK_HOSTS:
        raise ProviderFailure(
            "invalid_provider_config",
            "The reserved local provider profile must use a loopback endpoint.",
        )
    return normalized


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _toml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
