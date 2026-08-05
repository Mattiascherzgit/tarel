"""Create isolated, inactive connector candidates for humans and coding agents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from tarel.connectors.contracts import ConnectorFailure

_CONNECTOR_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    name: str
    path: Path


def scaffold_connector(name: str, *, output: Path | None = None) -> ScaffoldResult:
    if not _CONNECTOR_NAME.fullmatch(name):
        raise ConnectorFailure(
            "invalid_connector_name",
            "Connector names must start with a lowercase letter and contain only "
            "lowercase letters, numbers, underscores, or hyphens.",
        )

    target = (output or Path(name)).expanduser().resolve()
    if target.exists():
        raise ConnectorFailure("target_exists", f"Scaffold target already exists: {target}")

    module_name = name.replace("-", "_")
    files = _scaffold_files(name=name, module_name=module_name)
    try:
        for relative_path, content in files.items():
            path = target / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise ConnectorFailure("scaffold_failed", f"Could not create scaffold: {target}") from exc
    return ScaffoldResult(name=name, path=target)


def _scaffold_files(*, name: str, module_name: str) -> dict[Path, str]:
    return {
        Path("CONNECTOR_TASK.md"): _task_template(name),
        Path("README.md"): _readme_template(name),
        Path(module_name) / "__init__.py": "",
        Path(module_name) / "connector.py": _connector_template(name),
        Path(module_name) / "manifest.toml": _manifest_template(name, module_name),
        Path(module_name) / "references" / "authoring.md": _authoring_reference_template(name),
        Path(module_name) / "references" / "dialect.md": _dialect_reference_template(name),
    }


def _manifest_template(name: str, module_name: str) -> str:
    return f'''[connector]
contract_version = 1
name = "{name}"
version = "0.0.1"
source_type = "{name}"
entrypoint = "{module_name}.connector:create_connector"
extra = "{name}"
capabilities = ["probe", "discover_catalog"]
dependencies = []
permissions = ["read"]
dialect = "replace_me"
references = ["references/authoring.md", "references/dialect.md"]
'''


def _connector_template(name: str) -> str:
    class_name = "".join(part.capitalize() for part in re.split(r"[-_]", name)) + "Connector"
    return f'''"""Candidate {name} connector. This module is inactive until human approval."""

from tarel.connectors.contracts import (
    CatalogRequest,
    CatalogResult,
    ConnectorFailure,
    ConnectorManifest,
    ProbeRequest,
    ProbeResult,
)


class {class_name}:
    def __init__(self, manifest: ConnectorManifest) -> None:
        self.manifest = manifest

    def probe(self, request: ProbeRequest) -> ProbeResult:
        raise ConnectorFailure("not_implemented", "Implement a bounded read-only probe.")

    def discover_catalog(self, request: CatalogRequest) -> CatalogResult:
        raise ConnectorFailure(
            "not_implemented",
            "Implement read-only namespace, object, and field discovery.",
        )


def create_connector(manifest: ConnectorManifest) -> {class_name}:
    return {class_name}(manifest)
'''


def _task_template(name: str) -> str:
    return f'''# TAREL Connector Task: {name}

Create a connector that implements contract version 1 with exactly these first capabilities:

1. `probe`: establish a bounded connection and return source identity.
2. `discover_catalog`: return namespaces, tables or equivalent objects, and fields in stable order.

## Boundaries

- Use read-only operations and parameterized queries.
- Keep drivers optional and import them only inside the connector adapter.
- Never copy credentials into code, manifests, prompts, references, fixtures, or output.
- Return `ConnectorFailure` for expected failures and never silently omit failed observations.
- Do not edit TAREL kernel contracts to accommodate this candidate.
- Do not activate or install generated code without human review.

## Research

Use official vendor documentation when connection behavior, metadata APIs, or SQL dialect details
are unknown. Record concise findings and source links under `references/`. For proprietary systems,
store only permitted notes, links, tested queries, and version constraints—not copied manuals.

## Completion

- Replace the manifest placeholders and declare the real optional dependencies.
- Implement and locally test `probe` before implementing `discover_catalog`.
- Document the dialect and every metadata query category.
- Run both capabilities against a private test source without exposing its configuration.
- Stop for human code and result review; activation is a separate decision.
'''


def _readme_template(name: str) -> str:
    return f'''# {name} connector candidate

This directory was generated by `tarel connector scaffold {name}`. It is an isolated candidate,
not an installed or trusted connector. Start with `CONNECTOR_TASK.md`.
'''


def _authoring_reference_template(name: str) -> str:
    return f'''# {name} authoring evidence

## Official sources

- Add source links here.

## Connection behavior

- Document tested, non-secret connection assumptions here.

## Metadata discovery

- Document metadata APIs or query categories here.

## Version constraints

- Document verified product or driver constraints here.
'''


def _dialect_reference_template(name: str) -> str:
    return f'''# {name} dialect notes

Document only the syntax and metadata behavior needed by this connector.

## Identifier quoting

## Parameter binding

## Catalog and namespace semantics

## Read-only metadata queries
'''
