"""Versioned, dependency-free workspace contracts."""

from __future__ import annotations

import re
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from typing import Any

WORKSPACE_CONTRACT_VERSION = "tarel.workspace.v0.1"
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RELATIONSHIP_STATES = frozenset({"draft", "rejected", "validated"})


class WorkspaceFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SchemaReference:
    graph: str
    namespace: str

    def to_dict(self) -> dict[str, str]:
        return {"graph": self.graph, "namespace": self.namespace}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchemaReference:
        return cls(
            graph=_required_string(data, "graph"),
            namespace=_required_string(data, "namespace"),
        )


@dataclass(frozen=True, slots=True)
class ZoneMember:
    graph: str
    object_id: str

    def to_dict(self) -> dict[str, str]:
        return {"graph": self.graph, "object_id": self.object_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ZoneMember:
        return cls(
            graph=_required_string(data, "graph"),
            object_id=_required_string(data, "object_id"),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceRelationshipEndpoint:
    graph: str
    object_id: str
    fields: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": list(self.fields),
            "graph": self.graph,
            "object_id": self.object_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceRelationshipEndpoint:
        return cls(
            graph=_required_string(data, "graph"),
            object_id=_required_string(data, "object_id"),
            fields=tuple(_string_list(data, "fields")),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceRelationship:
    id: str
    source: WorkspaceRelationshipEndpoint
    target: WorkspaceRelationshipEndpoint
    state: str
    origin: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "origin": self.origin,
            "reason": self.reason,
            "source": self.source.to_dict(),
            "state": self.state,
            "target": self.target.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceRelationship:
        source = data.get("source")
        target = data.get("target")
        if not isinstance(source, dict) or not isinstance(target, dict):
            raise WorkspaceFailure(
                "invalid_workspace",
                "Workspace relationship endpoints must be objects.",
            )
        return cls(
            id=_required_string(data, "id"),
            source=WorkspaceRelationshipEndpoint.from_dict(source),
            target=WorkspaceRelationshipEndpoint.from_dict(target),
            state=_required_string(data, "state"),
            origin=_required_string(data, "origin"),
            reason=_required_string(data, "reason"),
        )


@dataclass(frozen=True, slots=True)
class Area:
    name: str
    schemas: tuple[SchemaReference, ...]
    description: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "description": self.description,
            "name": self.name,
            "schemas": [
                item.to_dict()
                for item in sorted(self.schemas, key=lambda item: (item.graph, item.namespace))
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Area:
        schemas = _object_list(data, "schemas")
        return cls(
            name=_required_string(data, "name"),
            schemas=tuple(
                sorted(
                    (SchemaReference.from_dict(item) for item in schemas),
                    key=lambda item: (item.graph, item.namespace),
                )
            ),
            description=_optional_string(data.get("description"), "description"),
        )


@dataclass(frozen=True, slots=True)
class Zone:
    name: str
    members: tuple[ZoneMember, ...]
    description: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "description": self.description,
            "members": [
                item.to_dict()
                for item in sorted(self.members, key=lambda item: (item.graph, item.object_id))
            ],
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Zone:
        members = _object_list(data, "members")
        return cls(
            name=_required_string(data, "name"),
            members=tuple(
                sorted(
                    (ZoneMember.from_dict(item) for item in members),
                    key=lambda item: (item.graph, item.object_id),
                )
            ),
            description=_optional_string(data.get("description"), "description"),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceSystem:
    name: str
    graphs: tuple[str, ...]
    areas: tuple[Area, ...] = ()
    zones: tuple[Zone, ...] = ()
    description: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "areas": [item.to_dict() for item in sorted(self.areas, key=lambda item: item.name)],
            "description": self.description,
            "graphs": sorted(self.graphs),
            "name": self.name,
            "zones": [item.to_dict() for item in sorted(self.zones, key=lambda item: item.name)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceSystem:
        graphs = _string_list(data, "graphs")
        areas = _object_list(data, "areas")
        zones = _object_list(data, "zones")
        return cls(
            name=_required_string(data, "name"),
            graphs=tuple(sorted(graphs)),
            areas=tuple(
                sorted((Area.from_dict(item) for item in areas), key=lambda item: item.name)
            ),
            zones=tuple(
                sorted((Zone.from_dict(item) for item in zones), key=lambda item: item.name)
            ),
            description=_optional_string(data.get("description"), "description"),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceDocument:
    name: str
    systems: tuple[WorkspaceSystem, ...] = ()
    description: str | None = None
    contract_version: str = WORKSPACE_CONTRACT_VERSION
    relationships: tuple[WorkspaceRelationship, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "description": self.description,
            "name": self.name,
            "relationships": [
                item.to_dict()
                for item in sorted(self.relationships, key=lambda item: item.id)
            ],
            "systems": [
                item.to_dict() for item in sorted(self.systems, key=lambda item: item.name)
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceDocument:
        if data.get("contract_version") != WORKSPACE_CONTRACT_VERSION:
            raise WorkspaceFailure(
                "unsupported_workspace",
                "Unsupported TAREL workspace contract.",
            )
        systems = _object_list(data, "systems")
        relationships = _object_list(data, "relationships", required=False)
        workspace = cls(
            name=_required_string(data, "name"),
            systems=tuple(
                sorted(
                    (WorkspaceSystem.from_dict(item) for item in systems),
                    key=lambda item: item.name,
                )
            ),
            relationships=tuple(
                sorted(
                    (WorkspaceRelationship.from_dict(item) for item in relationships),
                    key=lambda item: item.id,
                )
            ),
            description=_optional_string(data.get("description"), "description"),
        )
        validate_workspace(workspace)
        return workspace


def validate_workspace(workspace: WorkspaceDocument) -> None:
    if workspace.contract_version != WORKSPACE_CONTRACT_VERSION:
        raise WorkspaceFailure(
            "unsupported_workspace",
            "Unsupported TAREL workspace contract.",
        )
    _validate_identifier(workspace.name, "workspace")
    _validate_description(workspace.description, "workspace")
    _require_unique((item.name for item in workspace.systems), "system")

    graph_owners: dict[str, str] = {}
    for system in workspace.systems:
        _validate_identifier(system.name, "system")
        _validate_description(system.description, f"system {system.name}")
        if not system.graphs:
            raise WorkspaceFailure(
                "invalid_workspace",
                f"System must reference at least one graph: {system.name}",
            )
        _require_unique(system.graphs, f"graph in system {system.name}")
        for graph in system.graphs:
            _validate_identifier(graph, "graph")
            owner = graph_owners.setdefault(graph, system.name)
            if owner != system.name:
                raise WorkspaceFailure(
                    "graph_in_multiple_systems",
                    f"Graph {graph} belongs to both {owner} and {system.name}.",
                )

        _require_unique((item.name for item in system.areas), f"area in system {system.name}")
        _require_unique((item.name for item in system.zones), f"zone in system {system.name}")
        known_graphs = set(system.graphs)
        schema_owners: dict[tuple[str, str], str] = {}

        for area in system.areas:
            _validate_identifier(area.name, "area")
            _validate_description(area.description, f"area {area.name}")
            if not area.schemas:
                raise WorkspaceFailure(
                    "invalid_workspace",
                    f"Area must reference at least one schema: {area.name}",
                )
            _require_unique(
                ((item.graph, item.namespace) for item in area.schemas),
                f"schema in area {area.name}",
            )
            for schema in area.schemas:
                if not isinstance(schema.namespace, str) or not schema.namespace:
                    raise WorkspaceFailure(
                        "invalid_workspace",
                        f"Area {area.name} contains an empty schema namespace.",
                    )
                if schema.graph not in known_graphs:
                    raise WorkspaceFailure(
                        "graph_outside_system",
                        f"Area {area.name} references graph outside system {system.name}: "
                        f"{schema.graph}",
                    )
                key = (schema.graph, schema.namespace)
                owner = schema_owners.setdefault(key, area.name)
                if owner != area.name:
                    reference = f"{schema.graph}:{schema.namespace}"
                    raise WorkspaceFailure(
                        "schema_in_multiple_areas",
                        f"Schema {reference} belongs to both {owner} and {area.name}.",
                    )

        for zone in system.zones:
            _validate_identifier(zone.name, "zone")
            _validate_description(zone.description, f"zone {zone.name}")
            if not zone.members:
                raise WorkspaceFailure(
                    "invalid_workspace",
                    f"Zone must reference at least one object: {zone.name}",
                )
            _require_unique(
                ((item.graph, item.object_id) for item in zone.members),
                f"object in zone {zone.name}",
            )
            for member in zone.members:
                if not isinstance(member.object_id, str) or not member.object_id:
                    raise WorkspaceFailure(
                        "invalid_workspace",
                        f"Zone {zone.name} contains an empty object ID.",
                    )
                if member.graph not in known_graphs:
                    raise WorkspaceFailure(
                        "graph_outside_system",
                        f"Zone {zone.name} references graph outside system {system.name}: "
                        f"{member.graph}",
                    )

    _require_unique((item.id for item in workspace.relationships), "workspace relationship")
    known_graphs = set(graph_owners)
    for relationship in workspace.relationships:
        _validate_identifier(relationship.id, "workspace relationship")
        if relationship.state not in _RELATIONSHIP_STATES:
            raise WorkspaceFailure(
                "invalid_workspace",
                f"Invalid workspace relationship state: {relationship.state}",
            )
        if not relationship.origin.strip() or not relationship.reason.strip():
            raise WorkspaceFailure(
                "invalid_workspace",
                f"Workspace relationship requires origin and reason: {relationship.id}",
            )
        for endpoint in (relationship.source, relationship.target):
            if endpoint.graph not in known_graphs:
                raise WorkspaceFailure(
                    "graph_outside_workspace",
                    f"Relationship {relationship.id} references graph outside the workspace: "
                    f"{endpoint.graph}",
                )
            if not endpoint.object_id or not endpoint.fields:
                raise WorkspaceFailure(
                    "invalid_workspace",
                    f"Workspace relationship endpoint is incomplete: {relationship.id}",
                )
            _require_unique(endpoint.fields, f"field in relationship {relationship.id}")
        if relationship.source == relationship.target:
            raise WorkspaceFailure(
                "invalid_workspace",
                f"Workspace relationship endpoints must differ: {relationship.id}",
            )


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise WorkspaceFailure(
            "invalid_workspace",
            f"Workspace field must be a non-empty string: {key}",
        )
    return value


def _optional_string(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise WorkspaceFailure(
            "invalid_workspace",
            f"Optional workspace field must be a non-empty string or null: {key}",
        )
    return value


def _object_list(
    data: dict[str, Any],
    key: str,
    *,
    required: bool = True,
) -> list[dict[str, Any]]:
    value = data.get(key, [] if not required else None)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise WorkspaceFailure(
            "invalid_workspace",
            f"Workspace field must be an array of objects: {key}",
        )
    return value


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise WorkspaceFailure(
            "invalid_workspace",
            f"Workspace field must be an array of non-empty strings: {key}",
        )
    return value


def _validate_identifier(value: str, kind: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise WorkspaceFailure(
            "invalid_workspace_identifier",
            f"Invalid {kind} name: {value}. Use letters, numbers, dots, underscores, or hyphens.",
        )


def _validate_description(value: str | None, kind: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise WorkspaceFailure(
            "invalid_workspace",
            f"Description must not be blank for {kind}.",
        )


def _require_unique(values: Iterable[Hashable], kind: str) -> None:
    seen: set[Hashable] = set()
    for value in values:
        if value in seen:
            raise WorkspaceFailure(
                "invalid_workspace",
                f"Duplicate {kind}: {value}",
            )
        seen.add(value)
