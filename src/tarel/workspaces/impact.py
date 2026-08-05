"""Project graph changes onto existing workspace organization."""

from __future__ import annotations

from dataclasses import dataclass

from tarel.graph.refresh import GraphRefreshReport
from tarel.workspaces.contracts import WorkspaceDocument


@dataclass(frozen=True, slots=True)
class WorkspaceChangeImpact:
    workspace: str
    system: str
    areas: tuple[str, ...]
    zones: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "areas": list(self.areas),
            "system": self.system,
            "workspace": self.workspace,
            "zones": list(self.zones),
        }


def workspace_change_impacts(
    workspace: WorkspaceDocument,
    graph_name: str,
    report: GraphRefreshReport,
) -> tuple[WorkspaceChangeImpact, ...]:
    if not report.changes:
        return ()
    namespaces = {change.namespace for change in report.changes if change.namespace is not None}
    object_ids = {change.object_id for change in report.changes if change.object_id is not None}
    impacts: list[WorkspaceChangeImpact] = []
    for system in workspace.systems:
        if graph_name not in system.graphs:
            continue
        areas = tuple(
            sorted(
                area.name
                for area in system.areas
                if any(
                    schema.graph == graph_name and schema.namespace in namespaces
                    for schema in area.schemas
                )
            )
        )
        zones = tuple(
            sorted(
                zone.name
                for zone in system.zones
                if any(
                    member.graph == graph_name and member.object_id in object_ids
                    for member in zone.members
                )
            )
        )
        impacts.append(
            WorkspaceChangeImpact(
                workspace=workspace.name,
                system=system.name,
                areas=areas,
                zones=zones,
            )
        )
    return tuple(impacts)
