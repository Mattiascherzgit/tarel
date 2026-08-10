"""Agent-facing grounding contract over context and reviewed lineage."""

from __future__ import annotations

import json
from dataclasses import dataclass

from tarel.context import ContextResult
from tarel.context_output import canonical_hash
from tarel.graph.contracts import GraphNode
from tarel.lineage.contracts import LineageEvidence
from tarel.lineage.traversal import LineageReference, UpstreamTrace

GROUNDING_CONTRACT_VERSION = "tarel.grounding.v0.1"


@dataclass(frozen=True, slots=True)
class SourceTarget:
    """A non-secret execution target for objects selected into context."""

    graph: str
    revision: str
    connector: str
    source_type: str
    catalog: str
    dialect: str | None
    object_ids: tuple[str, ...]
    read_only: bool = True
    source: str | None = None
    source_revision: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog": self.catalog,
            "connector": self.connector,
            "dialect": self.dialect,
            "graph": self.graph,
            "object_ids": list(self.object_ids),
            "read_only": self.read_only,
            "revision": self.revision,
            "source_type": self.source_type,
            "source": self.source,
            "source_revision": self.source_revision,
        }


@dataclass(frozen=True, slots=True)
class LineageTarget:
    """One explicitly selected lineage document and its immutable revision."""

    name: str
    revision: str
    source_kind: str
    source_name: str

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "revision": self.revision,
            "source_kind": self.source_kind,
            "source_name": self.source_name,
        }


@dataclass(frozen=True, slots=True)
class GroundingAsset:
    """One exactly resolved object or field and its non-secret source target."""

    reference: str
    node: GraphNode
    fields: tuple[GraphNode, ...]
    source: SourceTarget

    def to_dict(self) -> dict[str, object]:
        return {
            "fields": [item.to_dict() for item in self.fields],
            "node": self.node.to_dict(),
            "reference": self.reference,
            "source": self.source.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class GroundingBundle:
    """Deterministic inputs for a BI agent turn.

    ``stable`` is suitable for a reusable prompt prefix. ``dynamic`` contains
    the current question, retrieval decisions, optional lineage matches, and
    an optional exact upstream trace.
    """

    context: ContextResult
    sources: tuple[SourceTarget, ...]
    lineages: tuple[LineageTarget, ...] = ()
    lineage_matches: tuple[LineageReference, ...] = ()
    trace: UpstreamTrace | None = None
    contract_version: str = GROUNDING_CONTRACT_VERSION

    def stable_dict(self) -> dict[str, object]:
        return {
            "context": self.context.stable_dict(),
            "lineages": [item.to_dict() for item in self.lineages],
            "sources": [item.to_dict() for item in self.sources],
        }

    def dynamic_dict(self) -> dict[str, object]:
        return {
            "context": self.context.dynamic_dict(),
            "lineage_matches": [item.to_dict() for item in self.lineage_matches],
            "trace": _trace_dict(self.trace) if self.trace else None,
        }

    def identity_dict(self) -> dict[str, str]:
        stable_hash = self.stable_hash
        dynamic_hash = self.dynamic_hash
        return {
            "bundle_hash": canonical_hash(
                {
                    "contract_version": self.contract_version,
                    "dynamic_hash": dynamic_hash,
                    "stable_hash": stable_hash,
                }
            ),
            "dynamic_hash": dynamic_hash,
            "stable_hash": stable_hash,
        }

    @property
    def stable_hash(self) -> str:
        return canonical_hash(self.stable_dict())

    @property
    def dynamic_hash(self) -> str:
        return canonical_hash(self.dynamic_dict())

    @property
    def bundle_hash(self) -> str:
        return self.identity_dict()["bundle_hash"]

    def to_dict(self) -> dict[str, object]:
        return {
            "contract_version": self.contract_version,
            "stable": self.stable_dict(),
            "dynamic": self.dynamic_dict(),
            "identity": self.identity_dict(),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    def stable_prompt(self) -> str:
        """Render a query-independent, cache-friendly semantic prefix."""
        lines = [
            "# TAREL Grounding Prefix",
            f"Contract: {self.contract_version}",
            f"Stable hash: {self.stable_hash}",
            "Use only the supplied source targets, semantic objects, joins, and reviewed claims.",
            "Never infer credentials, endpoints, or physical connections from this context.",
            "",
            "## Source targets",
        ]
        for source in self.sources:
            dialect = source.dialect or "unknown"
            lines.append(
                f"- {source.graph}: source={source.source or 'unregistered'}; "
                f"catalog={source.catalog}; connector={source.connector}; "
                f"source_type={source.source_type}; dialect={dialect}; "
                f"read_only={'yes' if source.read_only else 'no'}; revision={source.revision}"
            )
            if source.source_revision:
                lines.append(f"  Source revision: {source.source_revision}")
            lines.append(f"  Objects: {', '.join(source.object_ids) or 'none'}")

        lines.extend(("", "## Semantic objects"))
        for item in sorted(self.context.objects, key=lambda candidate: candidate.id):
            state = item.annotation_state or "unannotated"
            lines.append(f"### {item.label} [{item.type}; annotation={state}; id={item.id}]")
            if item.description:
                lines.append(item.description)
            if item.role:
                lines.append(f"Role: {item.role}")
            if item.grain:
                lines.append(f"Grain: {item.grain}")
            lines.extend(f"Warning: {warning}" for warning in item.warnings)
            for field in sorted(item.fields, key=lambda candidate: candidate.id):
                nullable = " nullable" if field.nullable else ""
                details = [value for value in (field.role, field.semantic_type) if value]
                detail = f" [{', '.join(details)}]" if details else ""
                field_state = field.annotation_state or "unannotated"
                lines.append(
                    f"- {field.name}: {field.data_type}{nullable}{detail}; "
                    f"annotation={field_state}; id={field.id}"
                )
                if field.description:
                    lines.append(f"  {field.description}")

        lines.extend(("", "## Reviewed joins"))
        if not self.context.joins:
            lines.append("- none")
        for join in sorted(self.context.joins, key=lambda candidate: candidate.id):
            lines.append(
                f"- {join.from_object}({', '.join(join.from_fields)}) -> "
                f"{join.to_object}({', '.join(join.to_fields)}) "
                f"[{join.kind}; state={join.state}; origin={join.origin}; id={join.id}]"
            )

        lines.extend(("", "## Lineage sources"))
        if not self.lineages:
            lines.append("- none selected")
        for lineage in self.lineages:
            lines.append(
                f"- {lineage.name}: source_kind={lineage.source_kind}; "
                f"source_name={lineage.source_name}; revision={lineage.revision}"
            )
        return "\n".join(lines) + "\n"

    def dynamic_prompt(self) -> str:
        """Render current-turn retrieval, evidence, omissions, and trace."""
        packet = self.context
        lines = [
            "# TAREL Grounding Request",
            f"Dynamic hash: {self.dynamic_hash}",
            f"Bundle hash: {self.bundle_hash}",
            f"Question: {packet.query}",
            f"Retrieval: {packet.retrieval_mode}; terms={', '.join(packet.terms)}",
            "",
            "## Selection",
        ]
        for item in packet.objects:
            score = f"; score={item.search_score}" if item.search_score is not None else ""
            lines.append(
                f"- {item.label}: {item.selection}; distance={item.distance}{score}; "
                f"omitted_fields={item.omitted_fields}"
            )
        reasons = ", ".join(packet.omissions.reasons) or "none"
        lines.extend(
            (
                "",
                "## Omissions",
                f"- objects={packet.omissions.objects}; fields={packet.omissions.fields}; "
                f"joins={packet.omissions.joins}; paths={packet.omissions.paths}; "
                f"reasons={reasons}",
                "",
                "## Lineage matches",
            )
        )
        if not self.lineage_matches:
            lines.append("- none")
        for item in self.lineage_matches:
            description = f"; {item.description}" if item.description else ""
            state = f"; annotation={item.annotation_state}" if item.annotation_state else ""
            lines.append(
                f"- {item.reference} [{item.kind}; source={item.source}{state}]{description}"
            )

        lines.extend(("", "## Upstream trace"))
        if self.trace is None:
            lines.append("- not requested")
            return "\n".join(lines) + "\n"
        lines.append(f"Start: {self.trace.start.reference}")
        lines.append(
            "Origins: " + (", ".join(item.reference for item in self.trace.origins) or "none")
        )
        for hop in self.trace.hops:
            lines.append(
                f"- depth={hop.depth}: {hop.source.reference} -> {hop.target.reference} "
                f"[{hop.relation}; state={hop.state}; lineage={hop.lineage or 'graph'}]"
            )
        lines.extend(f"Warning: {warning}" for warning in self.trace.warnings)
        if self.trace.truncated:
            lines.append("Warning: trace truncated by max_hops")
        return "\n".join(lines) + "\n"


def _trace_dict(trace: UpstreamTrace) -> dict[str, object]:
    """Project trace evidence without volatile source-file references."""
    return {
        "hops": [
            {
                "depth": hop.depth,
                "evidence": _evidence_dict(hop.evidence),
                "granularity_change": hop.granularity_change,
                "id": hop.id,
                "lineage": hop.lineage,
                "process_steps": list(hop.process_steps),
                "relation": hop.relation,
                "reviews": [item.to_dict() for item in hop.reviews],
                "role": hop.role,
                "source": hop.source.to_dict(),
                "state": hop.state,
                "target": hop.target.to_dict(),
                "via": list(hop.via),
                "via_definition": hop.via_definition,
                "write_evidence": _evidence_dict(hop.write_evidence),
            }
            for hop in trace.hops
        ],
        "origins": [item.to_dict() for item in trace.origins],
        "query": trace.query,
        "start": trace.start.to_dict(),
        "truncated": trace.truncated,
        "warnings": list(trace.warnings),
    }


def _evidence_dict(evidence: LineageEvidence | None) -> dict[str, object] | None:
    if evidence is None:
        return None
    return {
        "line_end": evidence.line_end,
        "line_start": evidence.line_start,
        "reason": evidence.reason,
        "source": evidence.source,
    }
