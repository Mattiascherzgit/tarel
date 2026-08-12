from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.annotations.apply import apply_annotation_proposal
from tarel.annotations.contracts import AnnotationFailure, AnnotationProposalEnvelope
from tarel.connectors.contracts import CatalogField, CatalogObject, CatalogResult
from tarel.graph.build import build_graph_from_catalog
from tarel.knowledge.contracts import KnowledgeFailure
from tarel.sdk import Tarel
from tarel.ui.presentation import browser_graph
from tarel.workspaces.contracts import WorkspaceDocument, WorkspaceSystem


class KnowledgeTests(TestCase):
    def test_scopes_are_resolved_narrowly_and_in_stable_order(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = _sdk(root)
            for document_id, scope in (
                ("all", "global"),
                ("system", "system:commercial"),
                ("graph", "graph:warehouse"),
                ("schema", "schema:warehouse:mart"),
                ("object", "object:warehouse:mart.FactSales"),
            ):
                path = root / f"{document_id}.md"
                path.write_text(f"# {document_id}\n\nReference {document_id}.\n", encoding="utf-8")
                sdk.knowledge.add(
                    document_id,
                    path,
                    scope=scope,
                    workspace="enterprise" if scope.startswith("system:") else None,
                    state="validated",
                )

            fact = sdk.knowledge.resolve(
                "warehouse",
                "mart.FactSales",
                workspace="enterprise",
            )
            customer = sdk.knowledge.resolve(
                "warehouse",
                "mart.Customer",
                workspace="enterprise",
            )
            sdk.runtime.workspace_store().save(
                WorkspaceDocument(
                    name="other-enterprise",
                    systems=(WorkspaceSystem(name="commercial", graphs=("warehouse",)),),
                )
            )
            other_workspace = sdk.knowledge.resolve(
                "warehouse",
                "mart.FactSales",
                workspace="other-enterprise",
            )

        self.assertEqual(
            [item.reference.id for item in fact.documents],
            ["all", "system", "graph", "schema", "object"],
        )
        self.assertEqual(
            [item.reference.id for item in customer.documents],
            ["all", "system", "graph", "schema"],
        )
        self.assertEqual(fact.documents[1].reference.scope.workspace, "enterprise")
        self.assertNotIn("system", [item.reference.id for item in other_workspace.documents])

    def test_annotation_context_is_opt_in_bounded_and_auditable(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = _sdk(root)
            broad = root / "terms.md"
            broad.write_text("Reviewed terminology for commercial analytics.", encoding="utf-8")
            narrow = root / "sales.md"
            narrow.write_text("One row per invoice line. " * 20, encoding="utf-8")
            sdk.knowledge.add("terms", broad, scope="global", state="validated")
            sdk.knowledge.add(
                "sales",
                narrow,
                scope="object:warehouse:mart.FactSales",
                state="draft",
            )

            plain = sdk.annotation.plan_graph(
                "warehouse",
                objects={"mart.FactSales"},
            )[0]
            scoped = sdk.annotation.plan_graph(
                "warehouse",
                objects={"mart.FactSales"},
                knowledge="scoped",
                max_knowledge_characters=80,
            )[0]
            resolved = sdk.knowledge.resolve(
                "warehouse",
                "mart.FactSales",
                max_characters=80,
            )
            explicit = sdk.knowledge.resolve(
                "warehouse",
                "mart.FactSales",
                documents=("terms",),
                max_characters=80,
            )

        self.assertEqual(plain.context_documents, ())
        self.assertNotIn("knowledge_context", plain.request.messages[1].content)
        self.assertEqual(scoped.id, plain.id)
        self.assertEqual([item.id for item in scoped.context_documents], ["sales"])
        self.assertTrue(scoped.context_documents[0].truncated)
        self.assertEqual(resolved.omitted, ("terms",))
        self.assertEqual(
            [item.reference.id for item in explicit.documents],
            ["terms", "sales"],
        )
        self.assertFalse(explicit.documents[0].reference.truncated)
        self.assertIn("knowledge_context", scoped.request.messages[1].content)
        self.assertIn("untrusted reference data", scoped.request.messages[0].content)

    def test_applied_proposal_records_document_revisions_without_content_or_path(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = _sdk(root)
            path = root / "commercial-terms.md"
            path.write_text("Net sales exclude tax.", encoding="utf-8")
            sdk.knowledge.add("terms", path, scope="global", state="validated")
            task = sdk.annotation.plan_graph(
                "warehouse",
                objects={"mart.FactSales"},
                knowledge="scoped",
            )[0]
            graph = sdk.graph.load("warehouse")
            proposal = _proposal(task)
            updated = apply_annotation_proposal(
                graph,
                AnnotationProposalEnvelope.from_dict(proposal),
                source="agent",
            )
            payload = browser_graph(updated)
            fact = next(item for item in payload["review"] if item["label"] == "mart.FactSales")
            persisted = sdk.runtime.knowledge_store().path("terms").read_text(encoding="utf-8")

        references = fact["context_documents"]
        self.assertEqual(references[0]["id"], "terms")
        self.assertEqual(references[0]["state"], "validated")
        self.assertNotIn("content", references[0])
        self.assertNotIn(str(root), persisted)
        self.assertIn('"source_name": "commercial-terms.md"', persisted)

    def test_system_scope_requires_a_matching_workspace(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = _sdk(root)
            path = root / "terms.txt"
            path.write_text("Terms", encoding="utf-8")
            with self.assertRaisesRegex(KnowledgeFailure, "requires --workspace"):
                sdk.knowledge.add("terms", path, scope="system:commercial")

    def test_stale_document_reference_cannot_be_recorded_as_current_provenance(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = _sdk(root)
            path = root / "terms.md"
            path.write_text("Original definition.", encoding="utf-8")
            sdk.knowledge.add("terms", path, scope="global", state="validated")
            task = sdk.annotation.plan_graph(
                "warehouse",
                objects={"mart.FactSales"},
                knowledge="scoped",
            )[0]
            path.write_text("Changed definition.", encoding="utf-8")
            sdk.knowledge.add(
                "terms",
                path,
                scope="global",
                state="validated",
                replace=True,
            )

            with self.assertRaisesRegex(KnowledgeFailure, "stale or inconsistent"):
                sdk.annotation.apply("warehouse", _proposal(task))

    def test_document_evidence_uses_an_exact_machine_readable_reference(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            sdk = _sdk(root)
            path = root / "terms.md"
            path.write_text("Reviewed definition.", encoding="utf-8")
            sdk.knowledge.add("terms", path, scope="global", state="validated")
            task = sdk.annotation.plan_graph(
                "warehouse",
                objects={"mart.FactSales"},
                knowledge="scoped",
            )[0]
            proposal = _proposal(task)
            evidence = proposal["annotation"]["evidence"][0]
            evidence["source"] = "document excerpt"
            evidence["reference"] = (
                f"knowledge_document: terms@{task.context_documents[0].revision}"
            )

            with self.assertRaisesRegex(AnnotationFailure, "exact ID@REVISION"):
                sdk.annotation.apply("warehouse", proposal)


def _sdk(root: Path) -> Tarel:
    sdk = Tarel(root / "state")
    catalog = CatalogResult(
        connector="test",
        source_type="database",
        catalog="DemoDW",
        dialect="ansi-sql",
        objects=(
            CatalogObject(
                namespace="mart",
                name="FactSales",
                kind="table",
                fields=(CatalogField("SalesAmount", 1, "decimal", False),),
            ),
            CatalogObject(
                namespace="mart",
                name="Customer",
                kind="table",
                fields=(CatalogField("CustomerId", 1, "integer", False),),
            ),
        ),
    )
    sdk.runtime.graph_store().save(build_graph_from_catalog("warehouse", catalog))
    sdk.runtime.workspace_store().save(
        WorkspaceDocument(
            name="enterprise",
            systems=(WorkspaceSystem(name="commercial", graphs=("warehouse",)),),
        )
    )
    return sdk


def _proposal(task) -> dict[str, object]:
    evidence = {
        "reason": "The reference document defines the term.",
        "reference": f"terms@{task.context_documents[0].revision}",
        "source": "knowledge_document",
        "value": None,
    }
    return {
        "annotation": {
            "confidence": 0.9,
            "confidence_reason": "The supplied document defines the grain.",
            "description": "Sales fact rows.",
            "evidence": [evidence],
            "fields": [
                {
                    "confidence": 0.9,
                    "confidence_reason": "The field and document identify the measure.",
                    "description": "Sales amount.",
                    "evidence": [evidence],
                    "name": "SalesAmount",
                    "role": "measure",
                    "semantic_type": "monetary_amount",
                    "synonyms": [],
                    "warnings": [],
                }
            ],
            "grain": "One sales row.",
            "role": "fact",
            "synonyms": [],
            "warnings": [],
        },
        "context_documents": [item.to_dict() for item in task.context_documents],
        "target_id": task.target_id,
        "task_id": task.id,
    }
