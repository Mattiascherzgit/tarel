"""Content-addressed local cache for validated provider lineage workfiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tarel.lineage.contracts import LineageFailure

_CACHE_VERSION = "tarel.lineage-analysis-cache.v0.1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class LineageAnalysisCacheIdentity:
    content_hash: str
    definition_kind: str
    language: str
    analyzer_version: str
    provider: str
    model: str | None
    review_passes: int
    max_output_tokens: int | None
    reasoning_effort: str | None

    @property
    def key(self) -> str:
        raw = json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "analyzer_version": self.analyzer_version,
            "content_hash": self.content_hash,
            "definition_kind": self.definition_kind,
            "language": self.language,
            "max_output_tokens": self.max_output_tokens,
            "model": self.model,
            "provider": self.provider,
            "reasoning_effort": self.reasoning_effort,
            "review_passes": self.review_passes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LineageAnalysisCacheIdentity:
        expected = {
            "analyzer_version",
            "content_hash",
            "definition_kind",
            "language",
            "max_output_tokens",
            "model",
            "provider",
            "reasoning_effort",
            "review_passes",
        }
        if set(data) != expected:
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                "Analysis-cache identity fields are invalid.",
            )
        content_hash = _text(data, "content_hash")
        if not _SHA256.fullmatch(content_hash):
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                "Analysis-cache content hash must be SHA-256.",
            )
        review_passes = data.get("review_passes")
        max_output_tokens = data.get("max_output_tokens")
        if (
            isinstance(review_passes, bool)
            or not isinstance(review_passes, int)
            or review_passes < 0
        ):
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                "Analysis-cache review passes must be an integer.",
            )
        if max_output_tokens is not None and (
            isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int)
            or max_output_tokens < 1
        ):
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                "Analysis-cache output-token limit must be an integer or null.",
            )
        return cls(
            content_hash=content_hash,
            definition_kind=_text(data, "definition_kind"),
            language=_text(data, "language"),
            analyzer_version=_text(data, "analyzer_version"),
            provider=_text(data, "provider"),
            model=_optional_text(data.get("model")),
            review_passes=review_passes,
            max_output_tokens=max_output_tokens,
            reasoning_effort=_optional_text(data.get("reasoning_effort")),
        )


class FileLineageAnalysisCache:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd() / ".tarel" / "lineage-analysis-cache"

    def load(self, identity: LineageAnalysisCacheIdentity) -> dict[str, object] | None:
        path = self.path(identity)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                f"Could not read lineage analysis cache entry: {identity.key}",
            ) from exc
        if not isinstance(payload, dict) or set(payload) != {
            "analysis",
            "contract_version",
            "identity",
        }:
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                "Lineage analysis cache entry fields are invalid.",
            )
        if payload.get("contract_version") != _CACHE_VERSION:
            raise LineageFailure(
                "unsupported_lineage_analysis_cache",
                "Unsupported lineage analysis cache contract.",
            )
        stored_identity = payload.get("identity")
        analysis = payload.get("analysis")
        if not isinstance(stored_identity, dict) or not isinstance(analysis, dict):
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                "Lineage analysis cache payload is invalid.",
            )
        if LineageAnalysisCacheIdentity.from_dict(stored_identity) != identity:
            raise LineageFailure(
                "invalid_lineage_analysis_cache",
                "Lineage analysis cache identity does not match its key.",
            )
        return analysis

    def save(
        self,
        identity: LineageAnalysisCacheIdentity,
        analysis: dict[str, object],
    ) -> Path:
        path = self.path(identity)
        if path.exists():
            self.load(identity)
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "analysis": analysis,
                "contract_version": _CACHE_VERSION,
                "identity": identity.to_dict(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=".analysis-",
            suffix=".tmp",
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
            try:
                os.link(temporary, path)
            except FileExistsError:
                self.load(identity)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise LineageFailure(
                "lineage_analysis_cache_save_failed",
                f"Could not save lineage analysis cache entry: {identity.key}",
            ) from exc
        return path

    def path(self, identity: LineageAnalysisCacheIdentity) -> Path:
        return self.root / f"{identity.key}.json"


def _text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LineageFailure(
            "invalid_lineage_analysis_cache",
            f"Analysis-cache field must be a non-empty string: {key}",
        )
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise LineageFailure(
            "invalid_lineage_analysis_cache",
            "Optional analysis-cache field must be a non-empty string or null.",
        )
    return value
