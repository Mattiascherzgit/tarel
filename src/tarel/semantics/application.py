"""Application use cases for source-faithful semantic imports."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from tarel.graph.store import FileGraphStore
from tarel.runtime import TarelRuntime
from tarel.semantics.contracts import (
    SemanticFailure,
    SemanticImportDocument,
    SemanticSourceEdit,
    semantic_target_values,
    validate_semantic_import,
)
from tarel.semantics.readers import (
    normalize_semantic_format,
    read_semantic_import,
    semantic_source_suffixes,
)
from tarel.semantics.source import read_semantic_source
from tarel.semantics.store import FileSemanticImportStore


@dataclass(frozen=True, slots=True)
class SemanticImportResult:
    document: SemanticImportDocument
    path: Path
    changed: bool


def import_semantic_use_case(
    name: str,
    *,
    graph_name: str,
    source_path: Path,
    format_name: str = "apache-ossie",
    replace_existing: bool = False,
    runtime: TarelRuntime | None = None,
) -> SemanticImportResult:
    normalized_format = normalize_semantic_format(format_name)
    source = read_semantic_source(
        source_path,
        suffixes=semantic_source_suffixes(normalized_format),
    )
    graph = _graph_store(runtime).load(graph_name)
    document = read_semantic_import(
        name,
        graph=graph,
        source=source,
        format_name=normalized_format,
    )
    store = _semantic_store(runtime)
    if store.exists(name):
        current = store.load(name)
        if current.graph_name != graph_name:
            raise SemanticFailure(
                "semantic_import_graph_mismatch",
                f"Semantic import {name} already belongs to graph {current.graph_name}.",
            )
        source_changed = current.snapshot.sha256 != document.snapshot.sha256
        format_changed = current.format_name != document.format_name
        if source_changed or format_changed:
            if not replace_existing:
                raise SemanticFailure(
                    "semantic_import_exists",
                    f"Semantic import {name} has different source content or format; "
                    "use --replace.",
                )
            if current.edits:
                raise SemanticFailure(
                    "semantic_import_has_edits",
                    "A source replacement cannot discard existing semantic edits. "
                    "Remove or migrate them explicitly first.",
                )
        elif current.edits:
            document = replace(document, edits=current.edits)
            validate_semantic_import(document)
        if document == current:
            return SemanticImportResult(
                document=current,
                path=store.path(name),
                changed=False,
            )
    path = store.save(document)
    return SemanticImportResult(document=document, path=path, changed=True)


def list_semantic_imports_use_case(
    *,
    graph_name: str | None = None,
    runtime: TarelRuntime | None = None,
) -> tuple[SemanticImportDocument, ...]:
    store = _semantic_store(runtime)
    documents = tuple(store.load(name) for name in store.list())
    if graph_name is not None:
        documents = tuple(item for item in documents if item.graph_name == graph_name)
    return documents


def load_semantic_import_use_case(
    name: str,
    *,
    runtime: TarelRuntime | None = None,
) -> SemanticImportDocument:
    return _semantic_store(runtime).load(name)


def edit_semantic_source_use_case(
    name: str,
    target_id: str,
    patch: dict[str, object],
    *,
    reason: str,
    expected_revision: str | None = None,
    runtime: TarelRuntime | None = None,
) -> SemanticImportResult:
    store = _semantic_store(runtime)
    document = store.load(name)
    if expected_revision is not None and expected_revision != document.revision:
        raise SemanticFailure(
            "stale_semantic_import",
            "The semantic import changed after it was loaded. Reload it before editing.",
        )
    if not reason.strip():
        raise SemanticFailure("semantic_edit_reason_required", "A semantic edit needs a reason.")
    unknown = set(patch) - {"description", "synonyms"}
    if unknown:
        raise SemanticFailure(
            "invalid_semantic_edit",
            "Unsupported semantic edit fields: " + ", ".join(sorted(unknown)),
        )
    if not patch:
        raise SemanticFailure("invalid_semantic_edit", "Semantic edit patch must not be empty.")
    description, synonyms = semantic_target_values(document, target_id)
    if "description" in patch:
        description = _description(patch["description"])
    if "synonyms" in patch:
        synonyms = _synonyms(patch["synonyms"])
    current_description, current_synonyms = semantic_target_values(document, target_id)
    if (description, synonyms) == (current_description, current_synonyms):
        raise SemanticFailure(
            "semantic_edit_no_change",
            "Semantic edit does not change the target.",
        )
    edit = SemanticSourceEdit(
        target_id=target_id,
        description=description,
        synonyms=synonyms,
        reason=reason.strip(),
    )
    changed = replace(document, edits=(*document.edits, edit))
    path = store.save(changed)
    return SemanticImportResult(document=changed, path=path, changed=True)


def read_semantic_patch(path_value: str) -> dict[str, object]:
    try:
        raw = (
            sys.stdin.read()
            if path_value == "-"
            else Path(path_value).read_text(encoding="utf-8")
        )
        payload = json.loads(raw)
    except FileNotFoundError as exc:
        raise SemanticFailure(
            "semantic_patch_not_found",
            f"Semantic patch not found: {path_value}",
        ) from exc
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SemanticFailure(
            "invalid_semantic_edit",
            "Could not read semantic patch JSON.",
        ) from exc
    if not isinstance(payload, dict):
        raise SemanticFailure("invalid_semantic_edit", "Semantic patch root must be an object.")
    return payload


def _description(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SemanticFailure(
            "invalid_semantic_edit",
            "Semantic description must be a string or null.",
        )
    return value.strip() or None


def _synonyms(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SemanticFailure(
            "invalid_semantic_edit",
            "Semantic synonyms must be an array of strings.",
        )
    result = tuple(item.strip() for item in value)
    if any(not item for item in result) or len(result) != len(set(result)):
        raise SemanticFailure(
            "invalid_semantic_edit",
            "Semantic synonyms must be unique non-empty strings.",
        )
    return result


def _graph_store(runtime: TarelRuntime | None) -> FileGraphStore:
    return FileGraphStore() if runtime is None else runtime.graph_store()


def _semantic_store(runtime: TarelRuntime | None) -> FileSemanticImportStore:
    return FileSemanticImportStore() if runtime is None else runtime.semantic_import_store()
