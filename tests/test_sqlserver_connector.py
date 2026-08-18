import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from tarel.connectors.contracts import ConnectorFailure, ObjectProfileRequest
from tarel.connectors.host import load_manifest
from tarel.connectors.sqlserver.connector import SqlServerConnector


class _ProfileCursor:
    def __init__(self, *, observed_rows: int) -> None:
        self.observed_rows = observed_rows
        self.query = ""
        self.queries: list[str] = []

    def execute(self, query: str, parameters: object = None) -> None:
        self.query = query
        self.queries.append(query)

    def fetchone(self) -> dict[str, object] | None:
        if "columns.name AS field_name" in self.query:
            raise AssertionError("Field discovery must use fetchall().")
        if "AS row_count" in self.query:
            return {"row_count": self.observed_rows}
        if "COUNT_BIG(DISTINCT value)" in self.query and "[Status]" in self.query:
            return {
                "distinct_count": 2,
                "max_length": 6,
                "max_value": "OPEN",
                "min_length": 4,
                "min_value": "CLOSED",
                "non_null_count": 3,
                "null_count": 0,
            }
        if "COUNT_BIG(DISTINCT value)" in self.query and "[RecordId]" in self.query:
            return {
                "distinct_count": 3,
                "max_length": None,
                "max_value": 3,
                "min_length": None,
                "min_value": 1,
                "non_null_count": 3,
                "null_count": 0,
            }
        raise AssertionError(f"Unexpected fetchone query: {self.query}")

    def fetchall(self) -> list[dict[str, object]]:
        if "columns.name AS field_name" in self.query:
            return [
                {
                    "column_id": 1,
                    "field_name": "RecordId",
                    "is_primary_key": 1,
                    "max_length": 4,
                    "precision": 10,
                    "scale": 0,
                    "type_name": "int",
                },
                {
                    "column_id": 2,
                    "field_name": "Status",
                    "is_primary_key": 0,
                    "max_length": 20,
                    "precision": 0,
                    "scale": 0,
                    "type_name": "nvarchar",
                },
            ]
        if "GROUP BY value" in self.query and "[Status]" in self.query:
            return [
                {"value": "OPEN", "value_count": 2},
                {"value": "CLOSED", "value_count": 1},
            ]
        if "GROUP BY value" in self.query and "[RecordId]" in self.query:
            return [
                {"value": 1, "value_count": 1},
                {"value": 2, "value_count": 1},
                {"value": 3, "value_count": 1},
            ]
        raise AssertionError(f"Unexpected fetchall query: {self.query}")

    def close(self) -> None:
        pass


class _ProfileConnection:
    def __init__(self, cursor: _ProfileCursor) -> None:
        self.profile_cursor = cursor

    def cursor(self, *, as_dict: bool) -> _ProfileCursor:
        if not as_dict:
            raise AssertionError("SQL Server profiles require dictionary rows.")
        return self.profile_cursor

    def close(self) -> None:
        pass


class SqlServerConnectorTests(TestCase):
    def test_complete_profile_uses_primary_key_order_and_opted_in_domains(self) -> None:
        cursor = _ProfileCursor(observed_rows=3)
        connector = SqlServerConnector(load_manifest("sqlserver"))
        driver = SimpleNamespace(connect=lambda **_arguments: _ProfileConnection(cursor))

        with patch.dict(sys.modules, {"pymssql": driver}):
            result = connector.profile_object(_request(row_limit=10, include_values=True))

        status = next(item for item in result.columns if item.name == "Status")
        self.assertTrue(result.complete)
        self.assertEqual(result.ordered_by, ("RecordId",))
        self.assertEqual(status.min_length, 4)
        self.assertEqual(status.max_length, 6)
        self.assertEqual(
            tuple((item.value, item.count) for item in status.values),
            (("OPEN", 2), ("CLOSED", 1)),
        )
        self.assertTrue(status.values_complete)
        self.assertTrue(any("TOP (11)" in query for query in cursor.queries))
        self.assertTrue(all("ORDER BY [RecordId]" in query for query in cursor.queries[1:]))
        field_query = next(
            query for query in cursor.queries if "columns.name AS field_name" in query
        )
        self.assertIn("columns.precision", field_query)
        self.assertIn("columns.scale", field_query)

    def test_incomplete_profile_never_emits_a_partial_domain(self) -> None:
        cursor = _ProfileCursor(observed_rows=4)
        connector = SqlServerConnector(load_manifest("sqlserver"))
        driver = SimpleNamespace(connect=lambda **_arguments: _ProfileConnection(cursor))

        with patch.dict(sys.modules, {"pymssql": driver}):
            result = connector.profile_object(_request(row_limit=3, include_values=True))

        self.assertFalse(result.complete)
        self.assertFalse(result.includes_values)
        self.assertEqual(result.rows_profiled, 3)
        self.assertTrue(all(not item.values for item in result.columns))
        self.assertFalse(any("GROUP BY value" in query for query in cursor.queries))

    def test_profile_limits_fail_before_loading_the_optional_driver(self) -> None:
        connector = SqlServerConnector(load_manifest("sqlserver"))

        with self.assertRaises(ConnectorFailure) as raised:
            connector.profile_object(_request(row_limit=0, include_values=False))

        self.assertEqual(raised.exception.code, "invalid_profile_row_limit")


def _request(*, row_limit: int, include_values: bool) -> ObjectProfileRequest:
    return ObjectProfileRequest(
        url="mssql+pymssql://user:password@localhost/Demo",
        database="Demo",
        namespace="sales",
        object_name="Orders",
        row_limit=row_limit,
        include_values=include_values,
    )
