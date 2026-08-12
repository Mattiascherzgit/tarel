"""Annotation task and proposal contracts shared by agents and API providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tarel.graph.contracts import AnnotationEvidence
from tarel.knowledge.contracts import KnowledgeFailure, KnowledgeReference
from tarel.providers.contracts import StructuredRequest


class AnnotationFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AnnotationTask:
    id: str
    graph_name: str
    target_id: str
    target_label: str
    request: StructuredRequest
    context_documents: tuple[KnowledgeReference, ...] = ()
    protected_values: tuple[str, ...] = field(default=(), repr=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "graph_name": self.graph_name,
            "id": self.id,
            "messages": [message.to_dict() for message in self.request.messages],
            "response_schema": self.request.schema,
            "schema_name": self.request.schema_name,
            "context_documents": [item.to_dict() for item in self.context_documents],
            "target_id": self.target_id,
            "target_label": self.target_label,
            "submission_template": {
                "annotation": "<response matching response_schema>",
                "context_documents": [item.to_dict() for item in self.context_documents],
                "target_id": self.target_id,
                "task_id": self.id,
            },
        }


@dataclass(frozen=True, slots=True)
class FieldAnnotationProposal:
    name: str
    description: str
    role: str | None
    semantic_type: str | None
    synonyms: tuple[str, ...]
    warnings: tuple[str, ...]
    confidence: float
    confidence_reason: str
    evidence: tuple[AnnotationEvidence, ...]


@dataclass(frozen=True, slots=True)
class ObjectAnnotationProposal:
    description: str
    role: str | None
    grain: str | None
    synonyms: tuple[str, ...]
    warnings: tuple[str, ...]
    confidence: float
    confidence_reason: str
    evidence: tuple[AnnotationEvidence, ...]
    fields: tuple[FieldAnnotationProposal, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ObjectAnnotationProposal:
        fields = data.get("fields")
        if not isinstance(fields, list):
            raise AnnotationFailure("invalid_proposal", "Proposal fields must be an array.")
        return cls(
            description=_required_string(data, "description"),
            role=_optional_string(data.get("role")),
            grain=_optional_string(data.get("grain")),
            synonyms=_strings(data.get("synonyms"), "synonyms"),
            warnings=_strings(data.get("warnings"), "warnings"),
            confidence=_confidence(data.get("confidence")),
            confidence_reason=_required_string(data, "confidence_reason"),
            evidence=_evidence(data.get("evidence")),
            fields=tuple(_field(item) for item in fields),
        )


@dataclass(frozen=True, slots=True)
class AnnotationProposalEnvelope:
    task_id: str
    target_id: str
    annotation: ObjectAnnotationProposal
    context_documents: tuple[KnowledgeReference, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AnnotationProposalEnvelope:
        annotation = data.get("annotation")
        if not isinstance(annotation, dict):
            raise AnnotationFailure("invalid_proposal", "Proposal requires an annotation object.")
        context_documents = data.get("context_documents", [])
        if not isinstance(context_documents, list):
            raise AnnotationFailure(
                "invalid_proposal",
                "Proposal context_documents must be an array.",
            )
        try:
            references = tuple(KnowledgeReference.from_dict(item) for item in context_documents)
        except (KnowledgeFailure, AttributeError) as exc:
            raise AnnotationFailure(
                "invalid_proposal",
                "Proposal contains an invalid knowledge reference.",
            ) from exc
        return cls(
            task_id=_required_string(data, "task_id"),
            target_id=_required_string(data, "target_id"),
            annotation=ObjectAnnotationProposal.from_dict(annotation),
            context_documents=references,
        )


@dataclass(frozen=True, slots=True)
class AnnotationRunResult:
    planned: int
    annotated: int
    failed: int

    def to_dict(self) -> dict[str, int]:
        return {"annotated": self.annotated, "failed": self.failed, "planned": self.planned}


def _field(value: Any) -> FieldAnnotationProposal:
    if not isinstance(value, dict):
        raise AnnotationFailure("invalid_proposal", "Every field proposal must be an object.")
    return FieldAnnotationProposal(
        name=_required_string(value, "name"),
        description=_required_string(value, "description"),
        role=_optional_string(value.get("role")),
        semantic_type=_optional_string(value.get("semantic_type")),
        synonyms=_strings(value.get("synonyms"), "field synonyms"),
        warnings=_strings(value.get("warnings"), "field warnings"),
        confidence=_confidence(value.get("confidence")),
        confidence_reason=_required_string(value, "confidence_reason"),
        evidence=_evidence(value.get("evidence")),
    )


def _evidence(value: Any) -> tuple[AnnotationEvidence, ...]:
    if not isinstance(value, list) or not value:
        raise AnnotationFailure("invalid_proposal", "Proposal evidence must be a non-empty array.")
    result: list[AnnotationEvidence] = []
    for item in value:
        if not isinstance(item, dict):
            raise AnnotationFailure("invalid_proposal", "Every evidence item must be an object.")
        result.append(
            AnnotationEvidence(
                source=_required_string(item, "source"),
                reference=_required_string(item, "reference"),
                value=_optional_string(item.get("value")),
                reason=_optional_string(item.get("reason")),
            )
        )
    return tuple(result)


def _required_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AnnotationFailure("invalid_proposal", f"Proposal field must be a string: {key}")
    return value.strip()


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnnotationFailure("invalid_proposal", "Optional proposal field must be a string.")
    return value.strip() or None


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AnnotationFailure(
            "invalid_proposal",
            f"Proposal {label} must be an array of strings.",
        )
    return tuple(item.strip() for item in value if item.strip())


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnnotationFailure("invalid_proposal", "Proposal confidence must be a number.")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise AnnotationFailure("invalid_proposal", "Proposal confidence must be between 0 and 1.")
    return result
