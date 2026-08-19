"""Small experimental dispatch over exercised semantic-model readers."""

from __future__ import annotations

from tarel.graph.contracts import GraphDocument
from tarel.semantics.contracts import SemanticFailure, SemanticImportDocument
from tarel.semantics.cube import read_cube_import
from tarel.semantics.ossie import read_ossie_import
from tarel.semantics.sml import read_sml_import
from tarel.semantics.source import SemanticSourceBundle

_ALIASES = {
    "apache-ossie": "apache-ossie",
    "cube": "cube",
    "cube-yaml": "cube",
    "ossie": "apache-ossie",
    "sml": "sml",
}


def normalize_semantic_format(value: str) -> str:
    try:
        return _ALIASES[value.casefold()]
    except KeyError as exc:
        raise SemanticFailure(
            "unsupported_semantic_format",
            f"Unsupported semantic format: {value}",
        ) from exc


def semantic_source_suffixes(format_name: str) -> frozenset[str]:
    normalized = normalize_semantic_format(format_name)
    if normalized == "apache-ossie":
        return frozenset({".json", ".yaml", ".yml"})
    return frozenset({".yaml", ".yml"})


def read_semantic_import(
    name: str,
    *,
    graph: GraphDocument,
    source: SemanticSourceBundle,
    format_name: str,
) -> SemanticImportDocument:
    normalized = normalize_semantic_format(format_name)
    if normalized == "apache-ossie":
        item = source.only_file(format_name="Apache Ossie")
        return read_ossie_import(
            name,
            graph=graph,
            content=item.content,
            media_type=source.snapshot.media_type,
        )
    if normalized == "sml":
        return read_sml_import(name, graph=graph, source=source)
    if normalized == "cube":
        return read_cube_import(name, graph=graph, source=source)
    raise AssertionError(f"Unhandled semantic format: {normalized}")
