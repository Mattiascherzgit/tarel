"""Explicit loading and checking of trusted connector manifests."""

from __future__ import annotations

import importlib
import importlib.util
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from tarel.connectors.contracts import (
    Connector,
    ConnectorCheck,
    ConnectorFailure,
    ConnectorManifest,
)

_CONNECTOR_ROOT = Path(__file__).resolve().parent
_BUILTIN_MANIFESTS = {
    "sqlite": _CONNECTOR_ROOT / "sqlite" / "manifest.toml",
    "sqlserver": _CONNECTOR_ROOT / "sqlserver" / "manifest.toml",
}


def check_connector(name: str) -> ConnectorCheck:
    manifest = load_manifest(name)
    missing = tuple(
        dependency
        for dependency in manifest.dependencies
        if importlib.util.find_spec(dependency) is None
    )
    return ConnectorCheck(
        name=manifest.name,
        version=manifest.version,
        source_type=manifest.source_type,
        available=not missing,
        capabilities=manifest.capabilities,
        permissions=manifest.permissions,
        missing_dependencies=missing,
        extra=manifest.extra,
        dialect=manifest.dialect,
        references=manifest.references,
    )


def load_manifest(name: str) -> ConnectorManifest:
    manifest_path = _BUILTIN_MANIFESTS.get(name)
    if manifest_path is None:
        raise ConnectorFailure("unknown_connector", f"Unknown connector: {name}")
    try:
        with manifest_path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConnectorFailure(
            "invalid_manifest",
            f"Could not read connector manifest: {name}",
        ) from exc
    manifest = ConnectorManifest.from_mapping(data)
    if manifest.name != name:
        raise ConnectorFailure(
            "invalid_manifest",
            "Connector name does not match its manifest path.",
        )
    return manifest


def load_connector(name: str) -> Connector:
    manifest = load_manifest(name)
    check = check_connector(name)
    if not check.available:
        missing = ", ".join(check.missing_dependencies)
        raise ConnectorFailure(
            "missing_dependency",
            f"Connector {name} requires {missing}. "
            f"Install with `pip install tarel[{manifest.extra}]`.",
        )

    module_name, separator, factory_name = manifest.entrypoint.partition(":")
    if not separator or not module_name or not factory_name:
        raise ConnectorFailure("invalid_manifest", "Connector entrypoint must be module:function.")
    try:
        module = importlib.import_module(module_name)
        factory = cast(Callable[[ConnectorManifest], Any], getattr(module, factory_name))
        connector = factory(manifest)
    except ConnectorFailure:
        raise
    except (AttributeError, ImportError, TypeError) as exc:
        raise ConnectorFailure("invalid_entrypoint", f"Could not load connector: {name}") from exc
    return cast(Connector, connector)
