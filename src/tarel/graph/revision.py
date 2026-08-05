"""Deterministic revision identifiers for complete graph documents."""

from __future__ import annotations

import hashlib
import json

from tarel.graph.contracts import GraphDocument


def graph_revision(graph: GraphDocument) -> str:
    payload = json.dumps(
        graph.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
