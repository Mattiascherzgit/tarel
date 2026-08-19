"""Experimental reader for Semantic Modeling Language project YAML."""

from __future__ import annotations

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


def read_sml_import(
    name: str,
    *,
    graph: GraphDocument,
    source: SemanticSourceBundle,
) -> SemanticImportDocument:
    artifacts = tuple((_mapping(item), item) for item in source.files)
    diagnostics: list[SemanticDiagnostic] = []
    by_type: dict[str, list[tuple[dict[str, Any], SemanticSourceFile]]] = {}
    for artifact, item in artifacts:
        object_type = _required_string(artifact, "object_type", item.path)
        by_type.setdefault(object_type, []).append((artifact, item))

    datasets = tuple(
        _dataset(artifact, item, diagnostics)
        for artifact, item in by_type.get("dataset", ())
    )
    metric_artifacts = (
        *by_type.get("metric", ()),
        *by_type.get("metric_calc", ()),
    )
    metrics = tuple(
        _metric(artifact, item, diagnostics) for artifact, item in metric_artifacts
    )
    model_artifacts = tuple(by_type.get("model", ()))
    if not model_artifacts:
        model_artifacts = (({"unique_name": name, "object_type": "model"}, source.files[0]),)
        diagnostics.append(
            SemanticDiagnostic(
                level="warning",
                code="sml_model_synthesized",
                message="No SML model object was present; TAREL synthesized an import container.",
                source_reference=source.files[0].path,
            )
        )
    models = tuple(
        _model(artifact, item, datasets, metrics, diagnostics)
        for artifact, item in model_artifacts
    )

    interpreted = {"catalog", "dataset", "metric", "metric_calc", "model"}
    for object_type, values in sorted(by_type.items()):
        if object_type in interpreted:
            continue
        for _artifact, item in values:
            diagnostics.append(
                SemanticDiagnostic(
                    level="warning",
                    code="preserved_sml_object",
                    message=(
                        f"SML {object_type} objects are preserved but not normalized in this slice."
                    ),
                    source_reference=item.path,
                )
            )

    catalog = next(iter(by_type.get("catalog", ())), None)
    version = str(catalog[0].get("version")) if catalog else "unspecified"
    if version in {"", "None"}:
        version = "unspecified"
    if catalog:
        _unknown_keys(
            catalog[0],
            {"description", "object_type", "unique_name", "version"},
            catalog[1].path,
            diagnostics,
        )
    models = bind_semantic_models(models, graph, diagnostics)
    document = SemanticImportDocument(
        name=name,
        graph_name=graph.name,
        format_name="sml",
        format_version=version,
        snapshot=source.snapshot,
        models=models,
        diagnostics=tuple(diagnostics),
    )
    validate_semantic_import(document)
    return document


def _dataset(
    artifact: dict[str, Any],
    item: SemanticSourceFile,
    diagnostics: list[SemanticDiagnostic],
) -> SemanticDataset:
    name = _required_string(artifact, "unique_name", item.path)
    dataset_id = _id("dataset", name)
    columns = _object_list(artifact.get("columns", []), f"{item.path}#/columns")
    fields = tuple(
        _field(column, item, dataset_id, index, diagnostics)
        for index, column in enumerate(columns)
    )
    _unknown_keys(
        artifact,
        {
            "columns",
            "connection_id",
            "description",
            "object_type",
            "table",
            "unique_name",
        },
        item.path,
        diagnostics,
    )
    table = _required_string(artifact, "table", item.path)
    return SemanticDataset(
        id=dataset_id,
        name=name,
        source_reference=item.path,
        source=table,
        fields=fields,
        description=_optional_string(artifact.get("description"), item.path),
    )


def _field(
    artifact: dict[str, Any],
    item: SemanticSourceFile,
    dataset_id: str,
    index: int,
    diagnostics: list[SemanticDiagnostic],
) -> SemanticField:
    reference = f"{item.path}#/columns/{index}"
    name = _required_string(artifact, "name", reference)
    expressions: list[SemanticExpression] = []
    sql = artifact.get("sql")
    if sql is None:
        expressions.append(SemanticExpression(dialect="sml", expression=name))
    else:
        expressions.append(
            SemanticExpression(
                dialect="sml",
                expression=_required_value_string(sql, f"{reference}/sql"),
            )
        )
    for dialect_index, dialect in enumerate(
        _object_list(artifact.get("dialects", []), f"{reference}/dialects")
    ):
        expressions.append(
            SemanticExpression(
                dialect=_required_string(
                    dialect,
                    "dialect",
                    f"{reference}/dialects/{dialect_index}",
                ),
                expression=_required_string(
                    dialect,
                    "sql",
                    f"{reference}/dialects/{dialect_index}",
                ),
            )
        )
        _unknown_keys(
            dialect,
            {"dialect", "sql"},
            f"{reference}/dialects/{dialect_index}",
            diagnostics,
        )
    _unknown_keys(
        artifact,
        {"data_type", "description", "dialects", "name", "sql"},
        reference,
        diagnostics,
    )
    return SemanticField(
        id=f"{dataset_id}/{_id('field', name)}",
        name=name,
        source_reference=reference,
        description=_optional_string(artifact.get("description"), reference),
        data_type=_optional_string(artifact.get("data_type"), reference),
        expressions=tuple(expressions),
    )


def _metric(
    artifact: dict[str, Any],
    item: SemanticSourceFile,
    diagnostics: list[SemanticDiagnostic],
) -> SemanticMetric:
    name = _required_string(artifact, "unique_name", item.path)
    expression = artifact.get("expression")
    if expression is None:
        method = str(artifact.get("calculation_method") or "value").upper()
        dataset = _required_string(artifact, "dataset", item.path)
        column = _required_string(artifact, "column", item.path)
        expression = f"{method}({dataset}.{column})"
    _unknown_keys(
        artifact,
        {
            "calculation_method",
            "column",
            "dataset",
            "description",
            "expression",
            "format",
            "object_type",
            "unique_name",
        },
        item.path,
        diagnostics,
    )
    if "format" in artifact:
        _preserved(item.path, "SML metric formatting is preserved but not normalized.", diagnostics)
    return SemanticMetric(
        id=_id("metric", name),
        name=name,
        source_reference=item.path,
        description=_optional_string(artifact.get("description"), item.path),
        expressions=(
            SemanticExpression(
                dialect="sml",
                expression=_required_value_string(expression, f"{item.path}#/expression"),
            ),
        ),
    )


def _model(
    artifact: dict[str, Any],
    item: SemanticSourceFile,
    datasets: tuple[SemanticDataset, ...],
    metrics: tuple[SemanticMetric, ...],
    diagnostics: list[SemanticDiagnostic],
) -> SemanticModel:
    name = _required_string(artifact, "unique_name", item.path)
    model_id = _id("model", name)
    dataset_names = {dataset.name for dataset in datasets}
    relationships: list[SemanticRelationship] = []
    raw_relationships = _object_list(
        artifact.get("relationships", []),
        f"{item.path}#/relationships",
    )
    for index, relationship in enumerate(raw_relationships):
        reference = f"{item.path}#/relationships/{index}"
        from_value = _mapping_value(relationship.get("from"), f"{reference}/from")
        to_value = _mapping_value(relationship.get("to"), f"{reference}/to")
        from_dataset = _required_string(from_value, "dataset", f"{reference}/from")
        to_dataset = to_value.get("dataset")
        if not isinstance(to_dataset, str) or to_dataset not in dataset_names:
            _preserved(
                reference,
                "SML relationship targets a logical dimension and remains source evidence.",
                diagnostics,
            )
            continue
        from_fields = _string_list(from_value.get("join_columns"), f"{reference}/from")
        to_fields = _string_list(to_value.get("join_columns"), f"{reference}/to")
        if len(from_fields) != len(to_fields):
            _preserved(
                reference,
                "SML relationship has no aligned physical target columns.",
                diagnostics,
            )
            continue
        relationship_name = _required_string(relationship, "unique_name", reference)
        relationships.append(
            SemanticRelationship(
                id=f"{model_id}/{_id('relationship', relationship_name)}",
                name=relationship_name,
                source_reference=reference,
                from_dataset=from_dataset,
                to_dataset=to_dataset,
                from_fields=from_fields,
                to_fields=to_fields,
            )
        )
    referenced_metrics = {
        _required_string(metric, "unique_name", f"{item.path}#/metrics/{index}")
        for index, metric in enumerate(
            _object_list(artifact.get("metrics", []), f"{item.path}#/metrics")
        )
    }
    missing_metrics = referenced_metrics - {metric.name for metric in metrics}
    if missing_metrics:
        diagnostics.append(
            SemanticDiagnostic(
                level="warning",
                code="sml_metric_definition_missing",
                message=(
                    f"{len(missing_metrics)} referenced SML metrics have no definition "
                    "in the bundle."
                ),
                source_reference=f"{item.path}#/metrics",
            )
        )
    if artifact.get("dimensions"):
        _preserved(
            f"{item.path}#/dimensions",
            "SML logical dimensions and hierarchies remain source evidence in this slice.",
            diagnostics,
        )
    _unknown_keys(
        artifact,
        {
            "description",
            "dimensions",
            "metrics",
            "object_type",
            "relationships",
            "unique_name",
            "visible",
        },
        item.path,
        diagnostics,
    )
    return SemanticModel(
        id=model_id,
        name=name,
        source_reference=item.path,
        datasets=tuple(
            SemanticDataset(
                id=f"{model_id}/{dataset.id}",
                name=dataset.name,
                source_reference=dataset.source_reference,
                source=dataset.source,
                fields=tuple(
                    SemanticField(
                        id=f"{model_id}/{field.id}",
                        name=field.name,
                        source_reference=field.source_reference,
                        description=field.description,
                        synonyms=field.synonyms,
                        data_type=field.data_type,
                        is_time=field.is_time,
                        expressions=field.expressions,
                    )
                    for field in dataset.fields
                ),
                description=dataset.description,
                synonyms=dataset.synonyms,
                primary_key=dataset.primary_key,
            )
            for dataset in datasets
        ),
        relationships=tuple(relationships),
        metrics=tuple(
            SemanticMetric(
                id=f"{model_id}/{metric.id}",
                name=metric.name,
                source_reference=metric.source_reference,
                description=metric.description,
                synonyms=metric.synonyms,
                data_type=metric.data_type,
                expressions=metric.expressions,
            )
            for metric in metrics
        ),
        description=_optional_string(artifact.get("description"), item.path),
    )


def _mapping(item: SemanticSourceFile) -> dict[str, Any]:
    return load_structured_mapping(
        item.content,
        error_code="invalid_sml",
        label=f"SML {item.path}",
    )


def _required_string(data: dict[str, Any], key: str, reference: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SemanticFailure("invalid_sml", f"SML string is required: {reference}#/{key}")
    return value.strip()


def _required_value_string(value: object, reference: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticFailure("invalid_sml", f"SML string is required: {reference}")
    return value.strip()


def _optional_string(value: object, reference: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SemanticFailure("invalid_sml", f"SML value must be a string: {reference}")
    return value.strip() or None


def _object_list(value: object, reference: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SemanticFailure("invalid_sml", f"SML value must be an object array: {reference}")
    return tuple(value)


def _string_list(value: object, reference: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SemanticFailure("invalid_sml", f"SML value must be a string array: {reference}")
    result = tuple(item.strip() for item in value)
    if any(not item for item in result) or len(result) != len(set(result)):
        raise SemanticFailure("invalid_sml", f"SML values must be unique: {reference}")
    return result


def _mapping_value(value: object, reference: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SemanticFailure("invalid_sml", f"SML value must be an object: {reference}")
    return value


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
                message=f"Unsupported SML key remains in the source snapshot: {key}",
                source_reference=f"{reference}#/{key}",
            )
        )


def _preserved(
    reference: str,
    message: str,
    diagnostics: list[SemanticDiagnostic],
) -> None:
    diagnostics.append(
        SemanticDiagnostic(
            level="warning",
            code="preserved_construct",
            message=message,
            source_reference=reference,
        )
    )


def _id(kind: str, name: str) -> str:
    return f"{kind}:{quote(name, safe='')}"
