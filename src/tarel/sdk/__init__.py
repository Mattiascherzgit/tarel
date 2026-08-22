"""Small embedded Python surface over TAREL application use cases."""

from tarel.context_caching import ContextCacheParts
from tarel.entity_resolution.contracts import (
    EntityResolutionCandidate,
    EntityResolutionEvidence,
    EntityResolutionMatch,
    EntityResolutionProvenance,
    EntityResolutionRule,
)
from tarel.grounding import GroundingAsset, GroundingBundle, LineageTarget, SourceTarget
from tarel.knowledge.contracts import KnowledgeContext, KnowledgeDocument, KnowledgeReference
from tarel.runtime import TarelRuntime
from tarel.sdk.client import Tarel
from tarel.sources.application import SourceCheck
from tarel.sources.contracts import SourceProfile
from tarel.workspaces.scope import ScopeSelection as WorkspaceScope

__all__ = [
    "ContextCacheParts",
    "EntityResolutionCandidate",
    "EntityResolutionEvidence",
    "EntityResolutionMatch",
    "EntityResolutionProvenance",
    "EntityResolutionRule",
    "GroundingAsset",
    "GroundingBundle",
    "LineageTarget",
    "KnowledgeContext",
    "KnowledgeDocument",
    "KnowledgeReference",
    "SourceTarget",
    "SourceCheck",
    "SourceProfile",
    "Tarel",
    "TarelRuntime",
    "WorkspaceScope",
]
