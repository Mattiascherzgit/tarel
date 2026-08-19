"""Apache Ossie reader and deterministic bindings to an existing TAREL graph."""

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
    SourceSnapshot,
    validate_semantic_import,
)
from tarel.semantics.structured import load_structured_mapping


def read_ossie_import(
    name: str,
    *,
    graph: GraphDocument,
    content: str,
    media_type: str,
) -> SemanticImportDocument:
    root = load_structured_mapping(
        content,
        error_code="invalid_ossie",
        label="Ossie",
    )
    version = _required_string(root, "version", "/version")
    snapshot = SourceSnapshot.from_content(content, media_type=media_type)
    diagnostics: list[SemanticDiagnostic] = []

    raw_models = root.get("semantic_model")
    if raw_models is None:
        if "ontology" in root:
            diagnostics.append(
                SemanticDiagnostic(
                    level="error",
                    code="unsupported_ossie_ontology",
                    message=(
                        "This first reader supports Ossie semantic_model documents; "
                        "ontology documents are preserved but not mapped."
                    ),
                    source_reference="/ontology",
                )
            )
            models: tuple[SemanticModel, ...] = ()
        else:
            raise SemanticFailure(
                "invalid_ossie",
                "Ossie document requires a semantic_model array.",
            )
    else:
        models = _parse_models(raw_models, diagnostics)

    _diagnose_unknown_keys(
        root,
        {"$schema", "description", "name", "ontology", "requires", "semantic_model", "version"},
        "/",
        diagnostics,
    )
    models = bind_semantic_models(models, graph, diagnostics)
    document = SemanticImportDocument(
        name=name,
        graph_name=graph.name,
        format_name="apache-ossie",
        format_version=version,
        snapshot=snapshot,
        models=models,
        diagnostics=tuple(diagnostics),
    )
    validate_semantic_import(document)
    return document


def _parse_models(
    value: object,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticModel, ...]:
    models = _object_list(value, "/semantic_model")
    _require_unique_names(models, "/semantic_model")
    parsed: list[SemanticModel] = []
    for index, item in enumerate(models):
        reference = f"/semantic_model/{index}"
        name = _required_string(item, "name", f"{reference}/name")
        model_id = _id("model", name)
        datasets = _parse_datasets(item.get("datasets", []), model_id, reference, diagnostics)
        relationships = _parse_relationships(
            item.get("relationships", []),
            model_id,
            reference,
            diagnostics,
        )
        metrics = _parse_metrics(item.get("metrics", []), model_id, reference, diagnostics)
        dataset_names = {dataset.name for dataset in datasets}
        for relationship in relationships:
            missing = {
                relationship.from_dataset,
                relationship.to_dataset,
            } - dataset_names
            if missing:
                diagnostics.append(
                    SemanticDiagnostic(
                        level="error",
                        code="unknown_relationship_dataset",
                        message=(
                            "Relationship references an unknown dataset: "
                            + ", ".join(sorted(missing))
                        ),
                        source_reference=relationship.source_reference,
                    )
                )
        if item.get("custom_extensions"):
            _preserved_diagnostic(
                f"{reference}/custom_extensions",
                "Ossie custom extensions remain in the exact source snapshot.",
                diagnostics,
            )
        _diagnose_unknown_keys(
            item,
            {
                "ai_context",
                "custom_extensions",
                "datasets",
                "description",
                "metrics",
                "name",
                "relationships",
            },
            reference,
            diagnostics,
        )
        parsed.append(
            SemanticModel(
                id=model_id,
                name=name,
                source_reference=reference,
                datasets=datasets,
                relationships=relationships,
                metrics=metrics,
                description=_optional_string(item.get("description"), f"{reference}/description"),
                synonyms=_synonyms(
                    item.get("ai_context"),
                    f"{reference}/ai_context",
                    diagnostics,
                ),
            )
        )
    return tuple(parsed)


def _parse_datasets(
    value: object,
    model_id: str,
    model_reference: str,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticDataset, ...]:
    datasets = _object_list(value, f"{model_reference}/datasets")
    _require_unique_names(datasets, f"{model_reference}/datasets")
    parsed: list[SemanticDataset] = []
    for index, item in enumerate(datasets):
        reference = f"{model_reference}/datasets/{index}"
        name = _required_string(item, "name", f"{reference}/name")
        dataset_id = f"{model_id}/{_id('dataset', name)}"
        fields = _parse_fields(item.get("fields", []), dataset_id, reference, diagnostics)
        if item.get("unique_keys"):
            _preserved_diagnostic(
                f"{reference}/unique_keys",
                "Unique keys are preserved but not normalized in this first slice.",
                diagnostics,
            )
        _diagnose_unknown_keys(
            item,
            {
                "ai_context",
                "custom_extensions",
                "description",
                "fields",
                "name",
                "primary_key",
                "source",
                "unique_keys",
            },
            reference,
            diagnostics,
        )
        if item.get("custom_extensions"):
            _preserved_diagnostic(
                f"{reference}/custom_extensions",
                "Dataset custom extensions remain in the exact source snapshot.",
                diagnostics,
            )
        parsed.append(
            SemanticDataset(
                id=dataset_id,
                name=name,
                source_reference=reference,
                source=_required_string(item, "source", f"{reference}/source"),
                fields=fields,
                description=_optional_string(item.get("description"), f"{reference}/description"),
                synonyms=_synonyms(
                    item.get("ai_context"),
                    f"{reference}/ai_context",
                    diagnostics,
                ),
                primary_key=_string_list(item.get("primary_key", []), f"{reference}/primary_key"),
            )
        )
    return tuple(parsed)


def _parse_fields(
    value: object,
    dataset_id: str,
    dataset_reference: str,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticField, ...]:
    fields = _object_list(value, f"{dataset_reference}/fields")
    _require_unique_names(fields, f"{dataset_reference}/fields")
    parsed: list[SemanticField] = []
    for index, item in enumerate(fields):
        reference = f"{dataset_reference}/fields/{index}"
        name = _required_string(item, "name", f"{reference}/name")
        expressions = _parse_expressions(item.get("expression"), reference, diagnostics)
        dimension = item.get("dimension")
        is_time = None
        if dimension is not None:
            dimension_object = _mapping(dimension, f"{reference}/dimension")
            is_time = _optional_bool(
                dimension_object.get("is_time"),
                f"{reference}/dimension/is_time",
            )
            _diagnose_unknown_keys(
                dimension_object,
                {"is_time"},
                f"{reference}/dimension",
                diagnostics,
            )
        if item.get("custom_extensions"):
            _preserved_diagnostic(
                f"{reference}/custom_extensions",
                "Field custom extensions remain in the exact source snapshot.",
                diagnostics,
            )
        _diagnose_unknown_keys(
            item,
            {
                "ai_context",
                "custom_extensions",
                "datatype",
                "description",
                "dimension",
                "expression",
                "name",
            },
            reference,
            diagnostics,
        )
        parsed.append(
            SemanticField(
                id=f"{dataset_id}/{_id('field', name)}",
                name=name,
                source_reference=reference,
                description=_optional_string(item.get("description"), f"{reference}/description"),
                synonyms=_synonyms(
                    item.get("ai_context"),
                    f"{reference}/ai_context",
                    diagnostics,
                ),
                data_type=_optional_string(item.get("datatype"), f"{reference}/datatype"),
                is_time=is_time,
                expressions=expressions,
            )
        )
    return tuple(parsed)


def _parse_metrics(
    value: object,
    model_id: str,
    model_reference: str,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticMetric, ...]:
    metrics = _object_list(value, f"{model_reference}/metrics")
    _require_unique_names(metrics, f"{model_reference}/metrics")
    parsed: list[SemanticMetric] = []
    for index, item in enumerate(metrics):
        reference = f"{model_reference}/metrics/{index}"
        name = _required_string(item, "name", f"{reference}/name")
        if item.get("custom_extensions"):
            _preserved_diagnostic(
                f"{reference}/custom_extensions",
                "Metric custom extensions remain in the exact source snapshot.",
                diagnostics,
            )
        _diagnose_unknown_keys(
            item,
            {
                "ai_context",
                "custom_extensions",
                "datatype",
                "description",
                "expression",
                "name",
            },
            reference,
            diagnostics,
        )
        parsed.append(
            SemanticMetric(
                id=f"{model_id}/{_id('metric', name)}",
                name=name,
                source_reference=reference,
                description=_optional_string(item.get("description"), f"{reference}/description"),
                synonyms=_synonyms(
                    item.get("ai_context"),
                    f"{reference}/ai_context",
                    diagnostics,
                ),
                data_type=_optional_string(item.get("datatype"), f"{reference}/datatype"),
                expressions=_parse_expressions(item.get("expression"), reference, diagnostics),
            )
        )
    return tuple(parsed)


def _parse_relationships(
    value: object,
    model_id: str,
    model_reference: str,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticRelationship, ...]:
    relationships = _object_list(value, f"{model_reference}/relationships")
    _require_unique_names(relationships, f"{model_reference}/relationships")
    parsed: list[SemanticRelationship] = []
    for index, item in enumerate(relationships):
        reference = f"{model_reference}/relationships/{index}"
        name = _required_string(item, "name", f"{reference}/name")
        if item.get("custom_extensions"):
            _preserved_diagnostic(
                f"{reference}/custom_extensions",
                "Relationship custom extensions remain in the exact source snapshot.",
                diagnostics,
            )
        _diagnose_unknown_keys(
            item,
            {
                "ai_context",
                "custom_extensions",
                "description",
                "from",
                "from_columns",
                "name",
                "to",
                "to_columns",
            },
            reference,
            diagnostics,
        )
        parsed.append(
            SemanticRelationship(
                id=f"{model_id}/{_id('relationship', name)}",
                name=name,
                source_reference=reference,
                from_dataset=_required_string(item, "from", f"{reference}/from"),
                to_dataset=_required_string(item, "to", f"{reference}/to"),
                from_fields=_string_list(item.get("from_columns"), f"{reference}/from_columns"),
                to_fields=_string_list(item.get("to_columns"), f"{reference}/to_columns"),
                description=_optional_string(item.get("description"), f"{reference}/description"),
                synonyms=_synonyms(
                    item.get("ai_context"),
                    f"{reference}/ai_context",
                    diagnostics,
                ),
            )
        )
    return tuple(parsed)


def _parse_expressions(
    value: object,
    parent_reference: str,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[SemanticExpression, ...]:
    if value is None:
        diagnostics.append(
            SemanticDiagnostic(
                level="error",
                code="missing_expression",
                message="Semantic field or metric has no expression.",
                source_reference=f"{parent_reference}/expression",
            )
        )
        return ()
    expression = _mapping(value, f"{parent_reference}/expression")
    dialects = _object_list(
        expression.get("dialects"),
        f"{parent_reference}/expression/dialects",
    )
    _diagnose_unknown_keys(
        expression,
        {"dialects"},
        f"{parent_reference}/expression",
        diagnostics,
    )
    parsed = []
    for index, item in enumerate(dialects):
        reference = f"{parent_reference}/expression/dialects/{index}"
        _diagnose_unknown_keys(
            item,
            {"dialect", "expression"},
            reference,
            diagnostics,
        )
        parsed.append(
            SemanticExpression(
                dialect=_required_string(item, "dialect", f"{reference}/dialect"),
                expression=_required_string(item, "expression", f"{reference}/expression"),
            )
        )
    return tuple(parsed)


def _synonyms(
    value: object,
    reference: str,
    diagnostics: list[SemanticDiagnostic],
) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        _preserved_diagnostic(
            reference,
            "Textual AI context remains in the exact source snapshot.",
            diagnostics,
        )
        return ()
    context = _mapping(value, reference)
    _diagnose_unknown_keys(context, {"synonyms"}, reference, diagnostics)
    return _string_list(context.get("synonyms", []), f"{reference}/synonyms")


def _diagnose_unknown_keys(
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
                message=f"Unsupported Ossie key is preserved in the source snapshot: {key}",
                source_reference=f"{reference.rstrip('/')}/{key}",
            )
        )


def _preserved_diagnostic(
    reference: str,
    message: str,
    diagnostics: list[SemanticDiagnostic],
) -> None:
    diagnostics.append(
        SemanticDiagnostic(
            level="info",
            code="preserved_construct",
            message=message,
            source_reference=reference,
        )
    )


def _require_unique_names(items: tuple[dict[str, Any], ...], reference: str) -> None:
    names = [
        _required_string(item, "name", f"{reference}/{index}/name")
        for index, item in enumerate(items)
    ]
    if len(names) != len(set(names)):
        raise SemanticFailure("invalid_ossie", f"Ossie names must be unique: {reference}")


def _required_string(data: dict[str, Any], key: str, reference: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SemanticFailure("invalid_ossie", f"Ossie value must be a string: {reference}")
    return value.strip()


def _optional_string(value: object, reference: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SemanticFailure(
            "invalid_ossie",
            f"Ossie value must be null or a string: {reference}",
        )
    return value.strip() or None


def _string_list(value: object, reference: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SemanticFailure(
            "invalid_ossie",
            f"Ossie value must be an array of strings: {reference}",
        )
    result = tuple(item.strip() for item in value)
    if any(not item for item in result) or len(result) != len(set(result)):
        raise SemanticFailure(
            "invalid_ossie",
            f"Ossie string values must be unique and non-empty: {reference}",
        )
    return result


def _object_list(value: object, reference: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise SemanticFailure(
            "invalid_ossie",
            f"Ossie value must be an array of objects: {reference}",
        )
    return tuple(value)


def _mapping(value: object, reference: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SemanticFailure("invalid_ossie", f"Ossie value must be an object: {reference}")
    return value


def _optional_bool(value: object, reference: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SemanticFailure(
            "invalid_ossie",
            f"Ossie value must be null or boolean: {reference}",
        )
    return value


def _id(kind: str, name: str) -> str:
    return f"{kind}:{quote(name, safe='')}"
