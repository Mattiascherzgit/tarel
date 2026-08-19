"""Read-only projections that keep source semantics distinct from TAREL claims."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from tarel.semantics.contracts import (
    SemanticDataset,
    SemanticImportDocument,
)

TargetValues = tuple[
    str | None,
    tuple[str, ...],
    str | None,
    tuple[str, ...],
    int,
]


class SemanticTarget(Protocol):
    id: str
    name: str
    source_reference: str
    description: str | None
    synonyms: tuple[str, ...]


def semantic_import_catalog(
    documents: Iterable[SemanticImportDocument],
) -> list[dict[str, object]]:
    return [
        {
            **document.summary_dict(),
            "diagnostic_items": [item.to_dict() for item in document.diagnostics],
        }
        for document in sorted(documents, key=lambda item: item.name.casefold())
    ]


def semantic_model_catalog(
    documents: Iterable[SemanticImportDocument],
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for document in sorted(documents, key=lambda item: item.name.casefold()):
        revision, values = _projection_context(document)
        for model in document.models:
            payload.append(
                {
                    **_target_payload(document, model, "model", revision, values),
                    "datasets": [
                        _dataset_payload(document, item, revision, values)
                        for item in model.datasets
                    ],
                    "metrics": [
                        {
                            **_target_payload(
                                document,
                                item,
                                "metric",
                                revision,
                                values,
                            ),
                            "data_type": item.data_type,
                            "expressions": [value.to_dict() for value in item.expressions],
                        }
                        for item in model.metrics
                    ],
                    "relationships": [
                        {
                            **_target_payload(
                                document,
                                item,
                                "relationship",
                                revision,
                                values,
                            ),
                            "from_dataset": item.from_dataset,
                            "from_fields": list(item.from_fields),
                            "graph_edge_id": item.graph_edge_id,
                            "to_dataset": item.to_dataset,
                            "to_fields": list(item.to_fields),
                        }
                        for item in model.relationships
                    ],
                }
            )
    return payload


def _dataset_payload(
    document: SemanticImportDocument,
    dataset: SemanticDataset,
    revision: str,
    values: dict[str, TargetValues],
) -> dict[str, object]:
    return {
        **_target_payload(document, dataset, "dataset", revision, values),
        "fields": [
            {
                **_target_payload(document, field, "field", revision, values),
                "data_type": field.data_type,
                "expressions": [item.to_dict() for item in field.expressions],
                "graph_node_id": field.graph_node_id,
                "is_time": field.is_time,
            }
            for field in dataset.fields
        ],
        "graph_node_id": dataset.graph_node_id,
        "primary_key": list(dataset.primary_key),
        "source": dataset.source,
    }


def semantic_node_bindings(
    documents: Iterable[SemanticImportDocument],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    bindings: dict[tuple[str, str], list[dict[str, object]]] = {}
    for document in documents:
        revision, values = _projection_context(document)
        for model in document.models:
            for dataset in model.datasets:
                if dataset.graph_node_id is not None:
                    bindings.setdefault(
                        (document.graph_name, dataset.graph_node_id),
                        [],
                    ).append(
                        {
                            **_target_payload(
                                document,
                                dataset,
                                "dataset",
                                revision,
                                values,
                            ),
                            "primary_key": list(dataset.primary_key),
                            "source": dataset.source,
                        }
                    )
                for field in dataset.fields:
                    if field.graph_node_id is not None:
                        bindings.setdefault(
                            (document.graph_name, field.graph_node_id),
                            [],
                        ).append(
                            {
                                **_target_payload(
                                    document,
                                    field,
                                    "field",
                                    revision,
                                    values,
                                ),
                                "data_type": field.data_type,
                                "expressions": [item.to_dict() for item in field.expressions],
                                "is_time": field.is_time,
                            }
                        )
    for values in bindings.values():
        values.sort(key=lambda item: (str(item["import_name"]), str(item["target_id"])))
    return bindings


def semantic_edge_bindings(
    documents: Iterable[SemanticImportDocument],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    bindings: dict[tuple[str, str], list[dict[str, object]]] = {}
    for document in documents:
        revision, values = _projection_context(document)
        for model in document.models:
            for relationship in model.relationships:
                if relationship.graph_edge_id is None:
                    continue
                bindings.setdefault(
                    (document.graph_name, relationship.graph_edge_id),
                    [],
                ).append(
                    {
                        **_target_payload(
                            document,
                            relationship,
                            "relationship",
                            revision,
                            values,
                        ),
                        "from_dataset": relationship.from_dataset,
                        "from_fields": list(relationship.from_fields),
                        "to_dataset": relationship.to_dataset,
                        "to_fields": list(relationship.to_fields),
                    }
                )
    for values in bindings.values():
        values.sort(key=lambda item: (str(item["import_name"]), str(item["target_id"])))
    return bindings


def _target_payload(
    document: SemanticImportDocument,
    target: SemanticTarget,
    kind: str,
    revision: str,
    values: dict[str, TargetValues],
) -> dict[str, object]:
    (
        original_description,
        original_synonyms,
        description,
        synonyms,
        patch_count,
    ) = values[target.id]
    return {
        "description": description,
        "import_name": document.name,
        "import_revision": revision,
        "kind": kind,
        "name": target.name,
        "original": {
            "description": original_description,
            "synonyms": list(original_synonyms),
        },
        "patch_count": patch_count,
        "source_reference": target.source_reference,
        "synonyms": list(synonyms),
        "target_id": target.id,
    }


def _projection_context(
    document: SemanticImportDocument,
) -> tuple[str, dict[str, TargetValues]]:
    values = {
        target.id: (
            target.description,
            target.synonyms,
            target.description,
            target.synonyms,
            0,
        )
        for target in _semantic_targets(document)
    }
    for edit in document.edits:
        original_description, original_synonyms, _description, _synonyms, count = values[
            edit.target_id
        ]
        values[edit.target_id] = (
            original_description,
            original_synonyms,
            edit.description,
            edit.synonyms,
            count + 1,
        )
    return document.revision, values


def _semantic_targets(document: SemanticImportDocument) -> list[SemanticTarget]:
    targets: list[SemanticTarget] = []
    for model in document.models:
        targets.append(model)
        for dataset in model.datasets:
            targets.append(dataset)
            targets.extend(dataset.fields)
        targets.extend(model.metrics)
        targets.extend(model.relationships)
    return targets
