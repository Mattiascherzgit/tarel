"""Deterministic revision identifiers for complete lineage documents."""

from __future__ import annotations

import hashlib
import json

from tarel.lineage.contracts import LineageDocument


def lineage_revision(document: LineageDocument) -> str:
    payload = json.dumps(
        document.to_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
