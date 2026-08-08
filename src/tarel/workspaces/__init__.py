"""Organizational views across one or more TAREL graphs."""

from tarel.workspaces.contracts import (
    Area,
    SchemaReference,
    WorkspaceDocument,
    WorkspaceFailure,
    WorkspaceRelationship,
    WorkspaceRelationshipEndpoint,
    WorkspaceSystem,
    Zone,
    ZoneMember,
)
from tarel.workspaces.scope import ResolvedScope, ResolvedScopeObject, ScopeSelection

__all__ = [
    "Area",
    "ResolvedScope",
    "ResolvedScopeObject",
    "SchemaReference",
    "ScopeSelection",
    "WorkspaceDocument",
    "WorkspaceFailure",
    "WorkspaceRelationship",
    "WorkspaceRelationshipEndpoint",
    "WorkspaceSystem",
    "Zone",
    "ZoneMember",
]
