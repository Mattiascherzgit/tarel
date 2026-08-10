"""Harness-neutral prompt blocks derived from a deterministic context packet."""

from __future__ import annotations

from dataclasses import dataclass

from tarel.context_output import ContextResult, canonical_json


@dataclass(frozen=True, slots=True)
class ContextCacheParts:
    """Stable and request-specific blocks for a consumer-managed prompt cache."""

    stable_json: str
    dynamic_json: str
    stable_hash: str
    dynamic_hash: str
    packet_hash: str

    @property
    def cache_key(self) -> str:
        return self.stable_hash

    def to_dict(self) -> dict[str, str]:
        return {
            "cache_key": self.cache_key,
            "dynamic_hash": self.dynamic_hash,
            "dynamic_json": self.dynamic_json,
            "packet_hash": self.packet_hash,
            "stable_hash": self.stable_hash,
            "stable_json": self.stable_json,
        }


def split_context_packet(packet: ContextResult) -> ContextCacheParts:
    """Split a packet without adding provider-specific cache instructions."""
    identity = packet.identity_dict()
    stable_json = canonical_json(
        {
            "contract_version": packet.contract_version,
            "stable": packet.stable_dict(),
            "stable_hash": identity["stable_hash"],
        }
    )
    dynamic_json = canonical_json(
        {
            "dynamic": packet.dynamic_dict(),
            "dynamic_hash": identity["dynamic_hash"],
            "packet_hash": identity["packet_hash"],
            "stable_hash": identity["stable_hash"],
        }
    )
    return ContextCacheParts(
        stable_json=stable_json,
        dynamic_json=dynamic_json,
        stable_hash=identity["stable_hash"],
        dynamic_hash=identity["dynamic_hash"],
        packet_hash=identity["packet_hash"],
    )
