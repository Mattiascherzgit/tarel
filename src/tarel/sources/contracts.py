"""Versioned contracts for non-secret logical source profiles."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Any

SOURCE_CONTRACT_VERSION = "tarel.source.v0.1"
ENRICHMENT_PERMISSIONS = frozenset({"aggregates", "raw_samples", "small_domains"})

_SOURCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CONNECTOR_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SourceFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceProfile:
    """A logical source whose credentials remain behind a config reference."""

    name: str
    connector: str
    config_reference: str | None = None
    database: str | None = None
    namespace: str | None = None
    graphs: tuple[str, ...] = ()
    enrichment_permissions: tuple[str, ...] = ()
    read_only: bool = True
    contract_version: str = SOURCE_CONTRACT_VERSION

    @property
    def revision(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "config_reference": self.config_reference,
            "connector": self.connector,
            "contract_version": self.contract_version,
            "database": self.database,
            "enrichment_permissions": list(self.enrichment_permissions),
            "graphs": list(self.graphs),
            "name": self.name,
            "namespace": self.namespace,
            "read_only": self.read_only,
        }

    def with_graph(self, graph: str) -> SourceProfile:
        return replace(self, graphs=tuple(sorted({*self.graphs, graph})))

    def allows_enrichment(self, permission: str) -> bool:
        if permission not in ENRICHMENT_PERMISSIONS:
            raise SourceFailure(
                "invalid_enrichment_permission",
                f"Unknown source enrichment permission: {permission}",
            )
        return permission in self.enrichment_permissions

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceProfile:
        if data.get("contract_version") != SOURCE_CONTRACT_VERSION:
            raise SourceFailure("unsupported_source", "Unsupported TAREL source contract.")
        profile = cls(
            name=_required_string(data, "name"),
            connector=_required_string(data, "connector"),
            config_reference=_optional_string(data.get("config_reference"), "config_reference"),
            database=_optional_string(data.get("database"), "database"),
            namespace=_optional_string(data.get("namespace"), "namespace"),
            graphs=tuple(sorted(_string_tuple(data.get("graphs"), "graphs"))),
            enrichment_permissions=tuple(
                sorted(
                    _string_tuple(
                        data.get("enrichment_permissions", []),
                        "enrichment_permissions",
                    )
                )
            ),
            read_only=data.get("read_only") is True,
        )
        validate_source(profile)
        return profile


def create_source(
    name: str,
    *,
    connector: str,
    config_reference: str | None = None,
    database: str | None = None,
    namespace: str | None = None,
    graphs: tuple[str, ...] = (),
    enrichment_permissions: tuple[str, ...] = (),
) -> SourceProfile:
    profile = SourceProfile(
        name=name.strip(),
        connector=connector.strip(),
        config_reference=config_reference.strip() if config_reference else None,
        database=database.strip() if database else None,
        namespace=namespace.strip() if namespace else None,
        graphs=tuple(sorted(set(graphs))),
        enrichment_permissions=tuple(sorted(set(enrichment_permissions))),
    )
    validate_source(profile)
    return profile


def validate_source(profile: SourceProfile) -> None:
    if not _SOURCE_NAME.fullmatch(profile.name):
        raise SourceFailure(
            "invalid_source_name",
            "Source names may contain letters, numbers, dots, underscores, and hyphens.",
        )
    if not _CONNECTOR_NAME.fullmatch(profile.connector):
        raise SourceFailure("invalid_source", "Source connector name is invalid.")
    if not profile.read_only:
        raise SourceFailure("invalid_source", "TAREL source profiles must be read-only.")
    if len(profile.graphs) != len(set(profile.graphs)):
        raise SourceFailure("invalid_source", "Source graph names must be unique.")
    if len(profile.enrichment_permissions) != len(set(profile.enrichment_permissions)):
        raise SourceFailure(
            "invalid_source",
            "Source enrichment permissions must be unique.",
        )
    unknown_permissions = set(profile.enrichment_permissions) - ENRICHMENT_PERMISSIONS
    if unknown_permissions:
        raise SourceFailure(
            "invalid_enrichment_permission",
            f"Unknown source enrichment permission: {sorted(unknown_permissions)[0]}",
        )
    if "small_domains" in profile.enrichment_permissions and not profile.allows_enrichment(
        "aggregates"
    ):
        raise SourceFailure(
            "invalid_enrichment_policy",
            "Small-domain access requires aggregate profiling permission.",
        )
    for graph in profile.graphs:
        if not _SOURCE_NAME.fullmatch(graph):
            raise SourceFailure("invalid_source", f"Source graph name is invalid: {graph}")
    if profile.config_reference is not None:
        _validate_config_reference(profile.config_reference)


def config_reference_parts(reference: str) -> tuple[str, str]:
    _validate_config_reference(reference)
    kind, value = reference.split(":", 1)
    return kind, value


def _validate_config_reference(reference: str) -> None:
    kind, separator, value = reference.partition(":")
    if not separator or not value:
        raise SourceFailure(
            "invalid_config_reference",
            "Config references must use env:VARIABLE or state:relative/path.toml.",
        )
    if kind == "env" and _ENV_NAME.fullmatch(value):
        return
    if kind == "state":
        path = PurePosixPath(value)
        if (
            not path.is_absolute()
            and ".." not in path.parts
            and value not in {"", "."}
            and "\\" not in value
            and ":" not in value
        ):
            return
    raise SourceFailure(
        "invalid_config_reference",
        "Config references must use env:VARIABLE or a safe state:relative/path.toml path.",
    )


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SourceFailure("invalid_source", f"Source field must be a string: {key}")
    return value.strip()


def _optional_string(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SourceFailure("invalid_source", f"Source field must be null or a string: {key}")
    return value.strip()


def _string_tuple(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SourceFailure("invalid_source", f"Source field must be a string array: {key}")
    return tuple(value)
