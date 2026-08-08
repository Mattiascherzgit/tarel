from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
from unittest import TestCase

from tarel.graph.contracts import GraphDocument, GraphNode
from tarel.lineage.cli import _render_upstream_trace
from tarel.lineage.contracts import (
    LineageClaim,
    LineageDefinition,
    LineageDocument,
    LineageEvidence,
    LineageFailure,
    LineageReview,
    LineageStep,
    LineageWriteSource,
    LineageWriteUnit,
    validate_lineage_document,
)
from tarel.lineage.traversal import trace_upstream

_REPORT = "powerbi.AdventureWorks.Report.SalesOverview.TotalSalesCard"
_MEASURE = "powerbi.AdventureWorks.Model.FactSales.TotalSales"
_SEMANTIC_FIELD = "powerbi.AdventureWorks.Model.FactSales.SalesAmount"
_PHYSICAL_FIELD = "DemoDW.mart.FactSales.SalesAmount"
_PHYSICAL_TABLE = "DemoDW.mart.FactSales"


class LineageTraversalTests(TestCase):
    def test_report_field_traces_deterministically_to_physical_origins(self) -> None:
        report, etl, graph = _lineage_fixture()

        first = trace_upstream((report, etl), (graph,), _REPORT)
        second = trace_upstream((etl, report), (graph,), _REPORT)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            [
                (hop.depth, hop.source.reference, hop.target.reference, hop.relation)
                for hop in first.hops
            ],
            [
                (1, _MEASURE, _REPORT, "reads_from"),
                (2, _SEMANTIC_FIELD, _MEASURE, "reads_from"),
                (3, _PHYSICAL_FIELD, _SEMANTIC_FIELD, "reads_from"),
                (4, _PHYSICAL_TABLE, _PHYSICAL_FIELD, "field_of"),
                (5, "DemoDW.dbo.DimDate", _PHYSICAL_TABLE, "derived_from"),
                (5, "DemoDW.dbo.RawSales", _PHYSICAL_TABLE, "derived_from"),
            ],
        )
        self.assertEqual(
            tuple(item.reference for item in first.origins),
            ("DemoDW.dbo.DimDate", "DemoDW.dbo.RawSales"),
        )
        write_hops = [hop for hop in first.hops if hop.relation == "derived_from"]
        self.assertEqual(
            {hop.via_definition for hop in write_hops},
            {"DemoDW.etl.LoadFactSales"},
        )
        self.assertEqual(
            {hop.process_steps for hop in write_hops},
            {("Load sales mart",)},
        )
        self.assertIn(
            "Physical lineage widens from a field to object-level procedure lineage.",
            first.warnings,
        )
        field_boundary = next(hop for hop in first.hops if hop.relation == "field_of")
        self.assertEqual(field_boundary.granularity_change, "field_to_object")
        self.assertFalse(first.truncated)

    def test_state_filter_excludes_draft_procedure_lineage(self) -> None:
        report, etl, graph = _lineage_fixture()
        draft_unit = replace(etl.write_units[0], state="draft", reviews=())
        draft_etl = replace(etl, write_units=(draft_unit,))
        validate_lineage_document(draft_etl)

        default_trace = trace_upstream((report, draft_etl), (graph,), _REPORT)
        validated_trace = trace_upstream(
            (report, draft_etl),
            (graph,),
            _REPORT,
            states=frozenset({"validated"}),
        )

        self.assertEqual(
            tuple(item.reference for item in default_trace.origins),
            ("DemoDW.dbo.DimDate", "DemoDW.dbo.RawSales"),
        )
        self.assertIn(
            "The trace contains lineage that has not been human-validated.",
            default_trace.warnings,
        )
        self.assertEqual(
            tuple(item.reference for item in validated_trace.origins),
            (_PHYSICAL_TABLE,),
        )
        self.assertNotIn("derived_from", {hop.relation for hop in validated_trace.hops})
        self.assertNotIn(
            "The trace contains lineage that has not been human-validated.",
            validated_trace.warnings,
        )

    def test_ambiguous_short_reference_is_rejected(self) -> None:
        first = _graph("first", "FirstDW", (GraphNode("sales", "table", "mart.Sales", {}),))
        second = _graph(
            "second",
            "SecondDW",
            (GraphNode("sales", "table", "mart.Sales", {}),),
        )

        with self.assertRaises(LineageFailure) as raised:
            trace_upstream((), (second, first), "mart.Sales")

        self.assertEqual(raised.exception.code, "ambiguous_lineage_reference")
        self.assertIn("FirstDW.mart.Sales", str(raised.exception))
        self.assertIn("SecondDW.mart.Sales", str(raised.exception))

    def test_ambiguous_claim_target_fails_closed(self) -> None:
        report = _document(
            "ambiguous-report",
            (_definition("report", "Sales report", "powerbi.Sales.Report"),),
            claims=(_claim("report-sales", "report", "mart.Sales", 1),),
        )
        first = _graph("first", "FirstDW", (GraphNode("sales", "table", "mart.Sales", {}),))
        second = _graph(
            "second",
            "SecondDW",
            (GraphNode("sales", "table", "mart.Sales", {}),),
        )

        with self.assertRaises(LineageFailure) as raised:
            trace_upstream((report,), (second, first), "powerbi.Sales.Report")

        self.assertEqual(raised.exception.code, "ambiguous_lineage_reference")

    def test_parallel_edges_use_evidence_as_a_deterministic_tie_breaker(self) -> None:
        _, etl, graph = _lineage_fixture()
        first = etl.write_units[0]
        second = replace(
            first,
            id="fact-sales-second-write",
            evidence=_evidence("sqlserver:DemoDW:etl.LoadFactSales", 8),
            sources=(
                replace(
                    first.sources[0],
                    evidence=_evidence("sqlserver:DemoDW:etl.LoadFactSales", 10),
                ),
            ),
        )
        first = replace(first, sources=(first.sources[0],))
        parallel_etl = replace(etl, write_units=(second, first))
        validate_lineage_document(parallel_etl)

        trace = trace_upstream((parallel_etl,), (graph,), _PHYSICAL_TABLE)
        write_hops = [hop for hop in trace.hops if hop.relation == "derived_from"]

        self.assertEqual(
            [
                (hop.write_evidence.line_start, hop.evidence.line_start)
                for hop in write_hops
                if hop.write_evidence and hop.evidence
            ],
            [(2, 4), (8, 10)],
        )

    def test_unresolved_procedure_source_remains_visible(self) -> None:
        _, etl, graph = _lineage_fixture()
        unresolved = LineageWriteSource(
            target="legacy.RawSales",
            role="business_data",
            via=(),
            evidence=_evidence("sqlserver:DemoDW:etl.LoadFactSales", 4),
        )
        unit = replace(etl.write_units[0], sources=(unresolved,))
        unresolved_etl = replace(etl, write_units=(unit,))
        validate_lineage_document(unresolved_etl)

        trace = trace_upstream((unresolved_etl,), (graph,), _PHYSICAL_TABLE)

        self.assertEqual(tuple(item.reference for item in trace.origins), ("legacy.RawSales",))
        self.assertEqual(trace.origins[0].kind, "unresolved")
        self.assertIn("Unresolved upstream reference: legacy.RawSales", trace.warnings)

    def test_cycle_is_reported_without_repeating_the_walk(self) -> None:
        _, etl, graph = _lineage_fixture()
        first = etl.write_units[0]
        reverse = replace(
            first,
            id="reverse-write",
            target="DemoDW.dbo.RawSales",
            sources=(
                LineageWriteSource(
                    target=_PHYSICAL_TABLE,
                    role="business_data",
                    via=(),
                    evidence=_evidence("fixture:reverse", 3),
                ),
            ),
        )
        first = replace(first, sources=(first.sources[0],))
        cyclic = replace(etl, write_units=(first, reverse))
        validate_lineage_document(cyclic)

        trace = trace_upstream((cyclic,), (graph,), _PHYSICAL_TABLE)

        self.assertEqual(len(trace.hops), 2)
        self.assertIn(f"Lineage cycle detected at: {_PHYSICAL_TABLE}", trace.warnings)
        self.assertEqual(trace.origins, ())

    def test_text_output_adds_a_deduplicated_origin_view(self) -> None:
        report, etl, graph = _lineage_fixture()
        trace = trace_upstream((report, etl), (graph,), _REPORT)
        output = StringIO()

        with redirect_stdout(output):
            _render_upstream_trace(trace.to_dict(), output_format="text")

        rendered = output.getvalue()
        self.assertIn("Origin view: 2 unique origins", rendered)
        self.assertEqual(rendered.count("- DemoDW.dbo.DimDate [table]"), 1)
        self.assertEqual(rendered.count("- DemoDW.dbo.RawSales [table]"), 1)
        self.assertIn("roles: business_data", rendered)
        self.assertIn("roles: lookup", rendered)
        self.assertEqual(rendered.count("procedures: DemoDW.etl.LoadFactSales"), 2)


def _lineage_fixture() -> tuple[LineageDocument, LineageDocument, GraphDocument]:
    report_definitions = (
        _definition("report", "Total Sales card", _REPORT),
        _definition("measure", "Total Sales", _MEASURE),
        _definition("semantic-field", "Sales Amount", _SEMANTIC_FIELD),
    )
    report = _document(
        "adventureworks-report",
        report_definitions,
        claims=(
            _claim("report-measure", "report", _MEASURE, 1),
            _claim("measure-field", "measure", _SEMANTIC_FIELD, 2),
            _claim("field-physical", "semantic-field", _PHYSICAL_FIELD, 3),
        ),
    )
    procedure = _definition(
        "load-fact-sales",
        "LoadFactSales",
        "DemoDW.etl.LoadFactSales",
        kind="procedure",
        language="tsql",
    )
    etl = _document(
        "adventureworks-etl",
        (procedure,),
        steps=(
            LineageStep(
                id="load-sales-step",
                external_id="load-sales-step",
                name="Load sales mart",
                definition_id=procedure.id,
                depends_on=(),
            ),
        ),
        write_units=(
            LineageWriteUnit(
                id="fact-sales-write",
                definition_id=procedure.id,
                operation="insert",
                target=_PHYSICAL_TABLE,
                state="validated",
                evidence=_evidence("sqlserver:DemoDW:etl.LoadFactSales", 2),
                sources=(
                    LineageWriteSource(
                        target="DemoDW.dbo.RawSales",
                        role="business_data",
                        via=("staged_sales",),
                        evidence=_evidence("sqlserver:DemoDW:etl.LoadFactSales", 4),
                    ),
                    LineageWriteSource(
                        target="DemoDW.dbo.DimDate",
                        role="lookup",
                        via=(),
                        evidence=_evidence("sqlserver:DemoDW:etl.LoadFactSales", 5),
                    ),
                ),
                reviews=(_validated_review(),),
            ),
        ),
    )
    graph = _graph(
        "adventureworks",
        "DemoDW",
        (
            GraphNode(
                id="fact-sales",
                type="table",
                label="mart.FactSales",
                metadata={"technical_description": "Curated sales fact table."},
            ),
            GraphNode(
                id="fact-sales.amount",
                type="field",
                label="SalesAmount",
                metadata={"object_id": "fact-sales"},
            ),
            GraphNode("raw-sales", "table", "dbo.RawSales", {}),
            GraphNode("dim-date", "table", "dbo.DimDate", {}),
        ),
    )
    return report, etl, graph


def _document(
    name: str,
    definitions: tuple[LineageDefinition, ...],
    *,
    steps: tuple[LineageStep, ...] = (),
    claims: tuple[LineageClaim, ...] = (),
    write_units: tuple[LineageWriteUnit, ...] = (),
) -> LineageDocument:
    document = LineageDocument(
        name=name,
        source_kind="test",
        source_name=name,
        source_reference=f"test:{name}",
        source_revision="2" * 64,
        workflow_id=f"workflow-{name}",
        workflow_name=name,
        definitions=definitions,
        steps=steps,
        claims=claims,
        write_units=write_units,
    )
    validate_lineage_document(document)
    return document


def _definition(
    identifier: str,
    name: str,
    qualified_name: str,
    *,
    kind: str = "query",
    language: str = "dax",
) -> LineageDefinition:
    return LineageDefinition(
        id=identifier,
        external_id=identifier,
        kind=kind,
        name=name,
        qualified_name=qualified_name,
        language=language,
        source_reference=f"fixture:{identifier}",
        content_hash="0" * 64,
        revision="1" * 64,
    )


def _claim(
    identifier: str,
    definition_id: str,
    target: str,
    line: int,
) -> LineageClaim:
    return LineageClaim(
        id=identifier,
        definition_id=definition_id,
        operation="read",
        target=target,
        state="validated",
        evidence=_evidence(f"fixture:{definition_id}", line),
        reviews=(_validated_review(),),
    )


def _evidence(reference: str, line: int) -> LineageEvidence:
    return LineageEvidence(
        source="fixture",
        reference=reference,
        reason="The fixture explicitly binds this upstream reference.",
        line_start=line,
        line_end=line,
    )


def _validated_review() -> LineageReview:
    return LineageReview(
        decision="validate",
        reason="Checked against the fixture definition.",
    )


def _graph(
    name: str,
    catalog: str,
    nodes: tuple[GraphNode, ...],
) -> GraphDocument:
    return GraphDocument(
        name=name,
        connector="fixture",
        source_type="sql",
        catalog=catalog,
        dialect="tsql",
        nodes=nodes,
        edges=(),
    )
