import json
from contextlib import redirect_stdout
from importlib.util import find_spec
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tarel.cli import main
from tarel.connectors.contracts import ConnectorFailure
from tarel.connectors.host import check_connector, load_manifest


class ConnectorTests(TestCase):
    def test_sqlserver_manifest_is_read_only(self) -> None:
        manifest = load_manifest("sqlserver")

        self.assertEqual(
            manifest.capabilities,
            ("probe", "discover_catalog", "sample_rows", "probe_relationships"),
        )
        self.assertEqual(manifest.permissions, ("read",))
        self.assertEqual(manifest.dialect, "tsql")

    def test_connector_check_has_deterministic_json(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["connector", "check", "sqlserver", "--format", "json"])

        payload = json.loads(output.getvalue())
        expected_available = find_spec("pymssql") is not None
        self.assertEqual(exit_code, 0 if expected_available else 1)
        self.assertEqual(payload["available"], expected_available)
        self.assertEqual(
            payload["capabilities"],
            ["probe", "discover_catalog", "sample_rows", "probe_relationships"],
        )
        self.assertEqual(payload["dialect"], "tsql")
        self.assertEqual(payload["permissions"], ["read"])

    def test_scaffold_creates_an_inactive_connector_candidate(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "postgres"

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["connector", "scaffold", "postgres", "--output", str(target)]
                )

            self.assertEqual(exit_code, 0)
            self.assertTrue((target / "CONNECTOR_TASK.md").is_file())
            self.assertTrue((target / "postgres" / "manifest.toml").is_file())
            with self.assertRaises(ConnectorFailure):
                check_connector("postgres")
