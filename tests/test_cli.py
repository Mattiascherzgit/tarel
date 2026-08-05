from contextlib import redirect_stdout
from io import StringIO
from unittest import TestCase

from tarel import __version__
from tarel.cli import main


class CliTests(TestCase):
    def test_version_command(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["version"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue().strip(), __version__)

    def test_empty_command_prints_help(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("analytics context", output.getvalue().lower())
