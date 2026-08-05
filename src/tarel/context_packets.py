"""Load, validate, and compare serialized TAREL context packets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from tarel.context import ContextFailure
from tarel.context_output import CONTEXT_CONTRACT_VERSION, canonical_hash
from tarel.graph.changes import GraphChange
from tarel.graph.contracts import GraphDocument
from tarel.graph.refresh import GraphRefreshReport
from tarel.graph.revision import graph_revision

_SUPPORTED_CONTRACTS = {"tarel.context.v0.1", CONTEXT_CONTRACT_VERSION}


@dataclass(frozen=True, slots=True)
class ContextPacketSnapshot:
    contract_version: str
    stable: dict[str, object]
    dynamic: dict[str, object]
    stable_hash: str
    dynamic_hash: str
    packet_hash: str


@dataclass(frozen=True, slots=True)
class ContextPacketDiff:
    left_contract: str
    right_contract: str
    left_packet_hash: str
    right_packet_hash: str
    stable_changed: bool
    dynamic_changed: bool
    graph_revision_changed: bool
    scope_changed: bool
    query_changed: bool
    objects_added: tuple[str, ...]
    objects_removed: tuple[str, ...]
    objects_changed: tuple[str, ...]
    joins_added: tuple[str, ...]
    joins_removed: tuple[str, ...]
    joins_changed: tuple[str, ...]

    @property
    def identical(self) -> bool:
        return self.left_packet_hash == self.right_packet_hash

    def to_dict(self) -> dict[str, object]:
        return {
            "contracts": {"left": self.left_contract, "right": self.right_contract},
            "dynamic_changed": self.dynamic_changed,
            "graph_revision_changed": self.graph_revision_changed,
            "identical": self.identical,
            "joins": {
                "added": list(self.joins_added),
                "changed": list(self.joins_changed),
                "removed": list(self.joins_removed),
            },
            "objects": {
                "added": list(self.objects_added),
                "changed": list(self.objects_changed),
                "removed": list(self.objects_removed),
            },
            "packet_hashes": {
                "left": self.left_packet_hash,
                "right": self.right_packet_hash,
            },
            "query_changed": self.query_changed,
            "scope_changed": self.scope_changed,
            "stable_changed": self.stable_changed,
        }


@dataclass(frozen=True, slots=True)
class ContextPacketImpact:
    status: str
    graph: str
    packet_revision: str
    current_revision: str
    exact: bool
    matched_changes: tuple[GraphChange, ...]
    reason: str

    @property
    def affected(self) -> bool | None:
        if self.status == "affected":
            return True
        if self.status in {"current", "unaffected"}:
            return False
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "affected": self.affected,
            "current_revision": self.current_revision,
            "exact": self.exact,
            "graph": self.graph,
            "matched_changes": [change.to_dict() for change in self.matched_changes],
            "packet_revision": self.packet_revision,
            "reason": self.reason,
            "status": self.status,
        }


def load_context_packet(path: Path) -> ContextPacketSnapshot:
    try:
        payload = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContextFailure(
            "context_packet_not_found",
            f"Context packet not found: {path}",
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ContextFailure(
            "invalid_context_packet",
            f"Could not read context packet JSON: {path}",
        ) from exc
    if not isinstance(payload, dict):
        raise ContextFailure("invalid_context_packet", "Context packet root must be an object.")
    return context_packet_from_dict(payload)


def context_packet_from_dict(payload: dict[str, object]) -> ContextPacketSnapshot:
    contract = payload.get("contract_version")
    if not isinstance(contract, str) or contract not in _SUPPORTED_CONTRACTS:
        raise ContextFailure(
            "unsupported_context_packet",
            "Unsupported or missing TAREL context contract.",
        )
    stable = _object(payload.get("stable"), "stable")
    dynamic = _object(payload.get("dynamic"), "dynamic")
    stable_hash = canonical_hash(stable)
    dynamic_hash = canonical_hash(dynamic)
    packet_hash = canonical_hash(
        {
            "contract_version": contract,
            "dynamic_hash": dynamic_hash,
            "stable_hash": stable_hash,
        }
    )
    if contract == CONTEXT_CONTRACT_VERSION:
        identity = _object(payload.get("identity"), "identity")
        expected = {
            "dynamic_hash": dynamic_hash,
            "packet_hash": packet_hash,
            "stable_hash": stable_hash,
        }
        if identity != expected:
            raise ContextFailure(
                "context_packet_hash_mismatch",
                "Context packet content does not match its identity hashes.",
            )
    return ContextPacketSnapshot(
        contract_version=contract,
        stable=stable,
        dynamic=dynamic,
        stable_hash=stable_hash,
        dynamic_hash=dynamic_hash,
        packet_hash=packet_hash,
    )


def diff_context_packets(
    left: ContextPacketSnapshot,
    right: ContextPacketSnapshot,
) -> ContextPacketDiff:
    left_objects = _entities(left.stable, "objects")
    right_objects = _entities(right.stable, "objects")
    left_joins = _entities(left.stable, "joins")
    right_joins = _entities(right.stable, "joins")
    return ContextPacketDiff(
        left_contract=left.contract_version,
        right_contract=right.contract_version,
        left_packet_hash=left.packet_hash,
        right_packet_hash=right.packet_hash,
        stable_changed=left.stable_hash != right.stable_hash,
        dynamic_changed=left.dynamic_hash != right.dynamic_hash,
        graph_revision_changed=_graph_revision(left.stable) != _graph_revision(right.stable),
        scope_changed=left.stable.get("scope") != right.stable.get("scope"),
        query_changed=left.dynamic.get("query") != right.dynamic.get("query"),
        objects_added=tuple(sorted(right_objects.keys() - left_objects.keys())),
        objects_removed=tuple(sorted(left_objects.keys() - right_objects.keys())),
        objects_changed=_changed_entities(left_objects, right_objects),
        joins_added=tuple(sorted(right_joins.keys() - left_joins.keys())),
        joins_removed=tuple(sorted(left_joins.keys() - right_joins.keys())),
        joins_changed=_changed_entities(left_joins, right_joins),
    )


def context_packet_impact(
    packet: ContextPacketSnapshot,
    graph: GraphDocument,
    report: GraphRefreshReport | None,
) -> ContextPacketImpact:
    packet_graph, packet_revision = _packet_graph(packet.stable)
    if packet_graph != graph.name:
        raise ContextFailure(
            "context_graph_mismatch",
            f"Context packet belongs to graph {packet_graph}, not {graph.name}.",
        )
    current_revision = graph_revision(graph)
    if packet_revision == current_revision:
        return ContextPacketImpact(
            status="current",
            graph=graph.name,
            packet_revision=packet_revision,
            current_revision=current_revision,
            exact=True,
            matched_changes=(),
            reason="Packet already uses the current graph revision.",
        )
    if (
        report is None
        or report.before_revision != packet_revision
        or report.after_revision != current_revision
    ):
        return ContextPacketImpact(
            status="unknown",
            graph=graph.name,
            packet_revision=packet_revision,
            current_revision=current_revision,
            exact=False,
            matched_changes=(),
            reason="No single persisted change report connects the packet to the current graph.",
        )

    packet_node_ids, packet_edge_ids = _packet_entity_ids(packet.stable)
    matched: list[GraphChange] = []
    for change in report.changes:
        if change.entity_type == "graph":
            matched.append(change)
            continue
        node_ids = set(change.related_ids)
        if change.object_id:
            node_ids.add(change.object_id)
        if change.entity_type == "node":
            node_ids.add(change.target_id)
        edge_ids = {change.target_id} if change.entity_type == "edge" else set()
        if node_ids & packet_node_ids or edge_ids & packet_edge_ids:
            matched.append(change)
    status = "affected" if matched else "unaffected"
    return ContextPacketImpact(
        status=status,
        graph=graph.name,
        packet_revision=packet_revision,
        current_revision=current_revision,
        exact=True,
        matched_changes=tuple(sorted(matched, key=lambda item: (item.reference, item.kind))),
        reason=(
            "Packet contains objects, fields, or joins touched by the refresh."
            if matched
            else "The graph changed, but none of this packet's selected entities were touched."
        ),
    )


def context_packet_graph_identity(packet: ContextPacketSnapshot) -> tuple[str, str]:
    return _packet_graph(packet.stable)


def _object(value: object, field: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ContextFailure(
            "invalid_context_packet",
            f"Context packet field {field} must be an object.",
        )
    return value


def _entities(section: dict[str, object], field: str) -> dict[str, dict[str, object]]:
    value = section.get(field)
    if not isinstance(value, list):
        raise ContextFailure(
            "invalid_context_packet",
            f"Context packet field stable.{field} must be an array.",
        )
    entities: dict[str, dict[str, object]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise ContextFailure(
                "invalid_context_packet",
                f"Context packet field stable.{field} contains a non-object.",
            )
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ContextFailure(
                "invalid_context_packet",
                f"Context packet field stable.{field} contains an invalid id.",
            )
        if identifier in entities:
            raise ContextFailure(
                "invalid_context_packet",
                f"Context packet field stable.{field} contains duplicate id: {identifier}",
            )
        entities[identifier] = item
    return entities


def _changed_entities(
    left: dict[str, dict[str, object]],
    right: dict[str, dict[str, object]],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            identifier
            for identifier in left.keys() & right.keys()
            if left[identifier] != right[identifier]
        )
    )


def _graph_revision(stable: dict[str, object]) -> object:
    graph = stable.get("graph")
    return graph.get("revision") if isinstance(graph, dict) else None


def _packet_graph(stable: dict[str, object]) -> tuple[str, str]:
    graph = stable.get("graph")
    if not isinstance(graph, dict):
        raise ContextFailure("invalid_context_packet", "Context packet graph must be an object.")
    name = graph.get("name")
    revision = graph.get("revision")
    if not isinstance(name, str) or not name or not isinstance(revision, str) or not revision:
        raise ContextFailure(
            "invalid_context_packet",
            "Context packet graph requires a name and revision.",
        )
    return name, revision


def _packet_entity_ids(stable: dict[str, object]) -> tuple[set[str], set[str]]:
    objects = _entities(stable, "objects")
    joins = _entities(stable, "joins")
    node_ids = set(objects)
    for item in objects.values():
        fields = item.get("fields")
        if not isinstance(fields, list):
            raise ContextFailure(
                "invalid_context_packet",
                "Context packet object fields must be an array.",
            )
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("id"), str):
                raise ContextFailure(
                    "invalid_context_packet",
                    "Context packet contains an invalid field id.",
                )
            node_ids.add(str(field["id"]))
    return node_ids, set(joins)
