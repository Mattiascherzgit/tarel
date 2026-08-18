import json
import sys
from contextlib import redirect_stdout
from importlib.metadata import EntryPoint
from importlib.util import find_spec
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from tarel.cli import main
from tarel.connectors.contracts import ConnectorFailure
from tarel.connectors.host import check_connector, load_connector, load_manifest


class ConnectorTests(TestCase):
    def test_sqlserver_manifest_is_read_only(self) -> None:
        manifest = load_manifest("sqlserver")

        self.assertEqual(
            manifest.capabilities,
            (
                "probe",
                "discover_catalog",
                "sample_rows",
                "profile_object",
                "probe_relationships",
            ),
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
            [
                "probe",
                "discover_catalog",
                "sample_rows",
                "profile_object",
                "probe_relationships",
            ],
        )
        self.assertEqual(payload["dialect"], "tsql")
        self.assertEqual(payload["permissions"], ["read"])

    def test_scaffold_creates_an_inactive_connector_candidate(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "postgres"

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["connector", "scaffold", "postgres", "--output", str(target)])

            self.assertEqual(exit_code, 0)
            self.assertTrue((target / "CONNECTOR_TASK.md").is_file())
            self.assertTrue((target / "pyproject.toml").is_file())
            self.assertTrue((target / "tarel_connector_postgres" / "manifest.toml").is_file())
            with self.assertRaises(ConnectorFailure):
                check_connector("postgres")

    def test_reviewed_connector_package_loads_only_through_its_entry_point(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "postgres"
            with redirect_stdout(StringIO()):
                exit_code = main(["connector", "scaffold", "postgres", "--output", str(target)])
            entry_point = EntryPoint(
                name="postgres",
                value="tarel_connector_postgres.connector:create_connector",
                group="tarel.connectors",
            )
            installed = Mock()
            installed.select.return_value = (entry_point,)
            sys.path.insert(0, str(target))
            try:
                with patch("tarel.connectors.host.entry_points", return_value=installed):
                    manifest = load_manifest("postgres")
                    connector = load_connector("postgres")
            finally:
                sys.path.remove(str(target))
                sys.modules.pop("postgres", None)
                sys.modules.pop("postgres.connector", None)

        self.assertEqual(exit_code, 0)
        self.assertEqual(manifest.name, "postgres")
        self.assertEqual(connector.manifest, manifest)
        self.assertGreaterEqual(installed.select.call_count, 1)

    def test_installed_connector_ambiguity_fails_closed(self) -> None:
        first = EntryPoint(
            name="vendor",
            value="first.connector:create_connector",
            group="tarel.connectors",
        )
        second = EntryPoint(
            name="vendor",
            value="second.connector:create_connector",
            group="tarel.connectors",
        )
        installed = Mock()
        installed.select.return_value = (first, second)

        with (
            patch("tarel.connectors.host.entry_points", return_value=installed),
            self.assertRaises(ConnectorFailure) as raised,
        ):
            load_manifest("vendor")

        self.assertEqual(raised.exception.code, "ambiguous_connector")
