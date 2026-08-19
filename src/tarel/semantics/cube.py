"""Experimental reader for Cube YAML data-model projects."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from tarel.graph.contracts import GraphDocument
from tarel.semantics.bindings import bind_semantic_models
from tarel.semantics.contracts import (
    SemanticDataset,
    SemanticDiagnostic,
    SemanticExpression,
    SemanticFailure,
    SemanticField,
    SemanticImportDocument,
    SemanticMetric,
    SemanticModel,
    SemanticRelationship,
    validate_semantic_import,
)
from tarel.semantics.source import SemanticSourceBundle, SemanticSourceFile
from tarel.semantics.structured import load_structured_mapping

_JOIN_EXPRESSION = re.compile(
    r"^\s*\{CUBE\}\.([A-Za-z_][A-Za-z0-9_$]*)\s*=\s*"
    r"\{([A-Za-z_][A-Za-z0-9_$]*)\.([A-Za-z_][A-Za-z0-9_$]*)\}\s*$"
)


def read_cube_import(
    name: str,
    *,
    graph: GraphDocument,
    source: SemanticSourceBundle,
) -> SemanticImportDocument:
    diagnostics: list[SemanticDiagnostic] = []
    raw_cubes: list[tuple[dict[str, Any], SemanticSourceFile, int]] = []
    for item in source.files:
        root = load_structured_mapping(
            item.content,
            error_code="invalid_cube",
            label=f"Cube {item.path}",
        )
        cubes = _object_list(root.get("cubes", []), f"{item.path}#/cubes")
        raw_cubes.extend((cube, item, index) for index, cube in enumerate(cubes))
        views = _object_list(root.get("views", []), f"{item.path}#/views")
        if views:
            diagnostics.append(
                SemanticDiagnostic(
                    level="warning",
                    code="preserved_cube_views",
                    message=(
                        f"{len(views)} Cube views remain in the source snapshot; "
                        "view projection is not normalized yet."
                    ),
                    source_reference=f"{item.path}#/views",
                )
            )
        _unknown_keys(root, {"cubes", "views"}, item.path, diagnostics)

    cube_names = [
        _required_string(cube, "name", f"{item.path}#/cubes/{index}")
        for cube, item, index in raw_cubes
    ]
    if len(cube_names) != len(set(cube_names)):
        raise SemanticFailure("invalid_cube", "Cube names must be unique across the project.")
    model_id = _id("model", name)
    datasets = tuple(
        _dataset(cube, item, index, model_id, diagnostics)
        for cube, item, index in raw_cubes
    )
    dataset_names = {dataset.name for dataset in datasets}
    relationships = tuple(
        relationship
        for cube, item, index in raw_cubes
        for relationship in _relationships(
            cube,
            item,
            index,
            model_id,
            dataset_names,
            diagnostics,
        )
    )
    metrics = tuple(
        metric
        for cube, item, index in raw_cubes
        for metric in _metrics(cube, item, index, model_id, diagnostics)
    )
    if not datasets:
        diagnostics.append(
            SemanticDiagnostic(
                level="error",
                code="cube_models_missing",
                message="Cube project contains no cubes.",
                source_reference="/",
            )
        )
    model = SemanticModel(
        id=model_id,
        name=name,
        source_reference="/",
        datasets=datasets,
        relationships=relationships,
        metrics=metrics,
    )
    models = bind_semantic_models((model,), graph, diagnostics)
    document = SemanticImportDocument(
        name=name,
        graph_name=graph.name,
        format_name="cube",
        format_version="yaml",
        snapshot=source.snapshot,
        models=models,
        diagnostics=tuple(diagnostics),
    )
    validate_semantic_import(document)
    return document


def _dataset(
    cube: dict[str, Any],
    item: SemanticSourceFile,
    index: int,
    model_id: str,
    diagnostics: list[SemanticDiagnostic],
) -> SemanticDataset:
    reference = f"{item.path}#/cubes/{index}"
    name = _required_string(cube, "name", reference)
    dataset_id = f"{model_id}/{_id('dataset', name)}"
    dimensions = _object_list(cube.get("dimensions", []), f"{reference}/dimensions")
    fields = tuple(
        _field(dimension, dataset_id, reference, dimension_index, diagnostics)
        for dimension_index, dimension in enumerate(dimensions)
    )
    primary_key = tuple(
        field.name
        for field, dimension in zip(fields, dimensions, strict=True)
        if dimension.get("primary_key") is True
    )
    source_value = cube.get("sql_table")
    if source_value is None:
        source_value = cube.get("sql")
    source_expression = _required_value_string(source_value, f"{reference}/sql")
    _unknown_keys(
        cube,
        {
            "description",
            "dimensions",
            "joins",
            "measures",
            "name",
            "sql",
            "sql_table",
        },
        reference,
        diagnostics,
    )
    return SemanticDataset(
        id=dataset_id,
        name=name,
        source_reference=reference,
        source=source_expression,
        fields=fields,
        description=_optional_string(cube.get("description"), reference),
        primary_key=primary_key,
    )


def _field(
    dimension: dict[str, Any],
    dataset_id: str,
    dataset_reference: str,
    index: int,
    diagnostics: list[SemanticDiagnostic],
) -> SemanticField:
    reference = f"{dataset_reference}/dimensions/{index}"
    name = _required_string(dimension, "name", reference)
    expression = _required_string(dimension, "sql", reference)
    _unknown_keys(
        dimension,
        {"description", "name", "primary_key", "sql", "type"},
        reference,
        diagnostics,
    )
    return SemanticField(
        id=f"{dataset_id}/{_id('field', name)}",
        name=name,
        source_reference=reference,
        description=_optional_string(dimension.get("description"), reference),
        data_type=_optional_string(dimension.get("type"), reference),
        expressions=(SemanticExpression(dialect="cube-sql", expression=expression),),
    )


def _metrics(
    cube: dict[str, Any],
    item: SemanticSourceFile,
    cube_index: int,
    model_id: str,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticMetric, ...]:
    reference = f"{item.path}#/cubes/{cube_index}"
    cube_name = _required_string(cube, "name", reference)
    measures = _object_list(cube.get("measures", []), f"{reference}/measures")
    metrics: list[SemanticMetric] = []
    for index, measure in enumerate(measures):
        measure_reference = f"{reference}/measures/{index}"
        name = _required_string(measure, "name", measure_reference)
        measure_type = _required_string(measure, "type", measure_reference)
        expression = measure.get("sql")
        if expression is None and measure_type == "count":
            expression = "COUNT(*)"
        elif expression is None:
            expression = f"CUBE_MEASURE({measure_type})"
            diagnostics.append(
                SemanticDiagnostic(
                    level="warning",
                    code="cube_measure_expression_opaque",
                    message="Cube measure has no SQL expression; its type marker is preserved.",
                    source_reference=measure_reference,
                )
            )
        _unknown_keys(
            measure,
            {"description", "name", "sql", "type"},
            measure_reference,
            diagnostics,
        )
        qualified_name = f"{cube_name}.{name}"
        metrics.append(
            SemanticMetric(
                id=f"{model_id}/{_id('metric', qualified_name)}",
                name=qualified_name,
                source_reference=measure_reference,
                description=_optional_string(measure.get("description"), measure_reference),
                expressions=(
                    SemanticExpression(
                        dialect="cube-sql",
                        expression=_required_value_string(expression, measure_reference),
                    ),
                ),
            )
        )
    return tuple(metrics)


def _relationships(
    cube: dict[str, Any],
    item: SemanticSourceFile,
    cube_index: int,
    model_id: str,
    dataset_names: set[str],
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticRelationship, ...]:
    reference = f"{item.path}#/cubes/{cube_index}"
    cube_name = _required_string(cube, "name", reference)
    joins = _object_list(cube.get("joins", []), f"{reference}/joins")
    relationships: list[SemanticRelationship] = []
    for index, join in enumerate(joins):
        join_reference = f"{reference}/joins/{index}"
        target_name = _required_string(join, "name", join_reference)
        expression = _required_string(join, "sql", join_reference)
        match = _JOIN_EXPRESSION.fullmatch(expression)
        if match is None or match.group(2) != target_name:
            diagnostics.append(
                SemanticDiagnostic(
                    level="warning",
                    code="cube_join_preserved",
                    message="Cube join SQL is not a simple exact field equality.",
                    source_reference=join_reference,
                )
            )
            continue
        if target_name not in dataset_names:
            diagnostics.append(
                SemanticDiagnostic(
                    level="error",
                    code="cube_join_target_missing",
                    message=f"Cube join target is not present in the source bundle: {target_name}",
                    source_reference=join_reference,
                )
            )
            continue
        relationship_name = f"{cube_name}_to_{target_name}"
        relationships.append(
            SemanticRelationship(
                id=f"{model_id}/{_id('relationship', relationship_name)}",
                name=relationship_name,
                source_reference=join_reference,
                from_dataset=cube_name,
                to_dataset=target_name,
                from_fields=(match.group(1),),
                to_fields=(match.group(3),),
                description=_optional_string(join.get("description"), join_reference),
            )
        )
        _unknown_keys(
            join,
            {"description", "name", "relationship", "sql"},
            join_reference,
            diagnostics,
        )
        if "relationship" in join:
            diagnostics.append(
                SemanticDiagnostic(
                    level="warning",
                    code="preserved_cube_cardinality",
                    message="Cube relationship cardinality remains source evidence.",
                    source_reference=f"{join_reference}/relationship",
                )
            )
    return tuple(relationships)


def _required_string(data: dict[str, Any], key: str, reference: str) -> str:
    return _required_value_string(data.get(key), f"{reference}/{key}")


def _required_value_string(value: object, reference: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticFailure("invalid_cube", f"Cube string is required: {reference}")
    return value.strip()


def _optional_string(value: object, reference: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SemanticFailure("invalid_cube", f"Cube value must be a string: {reference}")
    return value.strip() or None


def _object_list(value: object, reference: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SemanticFailure("invalid_cube", f"Cube value must be an object array: {reference}")
    return tuple(value)


def _unknown_keys(
    value: dict[str, Any],
    supported: set[str],
    reference: str,
    diagnostics: list[SemanticDiagnostic],
) -> None:
    for key in sorted(set(value) - supported):
        diagnostics.append(
            SemanticDiagnostic(
                level="warning",
                code="preserved_unknown_construct",
                message=f"Unsupported Cube key remains in the source snapshot: {key}",
                source_reference=f"{reference.rstrip('/')}/{key}",
            )
        )


def _id(kind: str, name: str) -> str:
    return f"{kind}:{quote(name, safe='')}"
