"""Strict JSON/YAML loading shared by dependency-light semantic readers."""

from __future__ import annotations

import json
from typing import Any

from tarel.semantics.contracts import SemanticFailure


def load_structured_mapping(
    content: str,
    *,
    error_code: str,
    label: str,
) -> dict[str, Any]:
    try:
        payload = json.loads(
            content,
            object_pairs_hook=lambda pairs: _unique_json_object(
                pairs,
                error_code=error_code,
                label=label,
            ),
        )
    except json.JSONDecodeError:
        payload = _load_yaml(content, error_code=error_code, label=label)
    if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
        raise SemanticFailure(error_code, f"{label} document root must be an object.")
    return payload


def _unique_json_object(
    pairs: list[tuple[str, object]],
    *,
    error_code: str,
    label: str,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SemanticFailure(
                error_code,
                f"{label} JSON contains a duplicate key: {key}",
            )
        result[key] = value
    return result


def _load_yaml(content: str, *, error_code: str, label: str) -> object:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise SemanticFailure(
            "semantic_yaml_unavailable",
            "YAML import requires the optional dependency: pip install 'tarel[semantic]'.",
        ) from exc

    class UniqueKeyLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[object, object]:
        loader.flatten_mapping(node)
        mapping: dict[object, object] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise SemanticFailure(
                    error_code,
                    f"{label} YAML mapping keys must be scalar values.",
                ) from exc
            if duplicate:
                raise SemanticFailure(
                    error_code,
                    f"{label} YAML contains a duplicate key: {key}",
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping,
    )
    try:
        return yaml.load(content, Loader=UniqueKeyLoader)
    except SemanticFailure:
        raise
    except yaml.YAMLError as exc:
        raise SemanticFailure(error_code, f"Could not parse {label} YAML.") from exc
