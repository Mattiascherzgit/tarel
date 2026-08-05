import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from test_context import _context_graph

from tarel.cli import main
from tarel.context import ContextFailure, compile_context
from tarel.context_packets import (
    context_packet_from_dict,
    diff_context_packets,
    load_context_packet,
)


class ContextPacketTests(TestCase):
    def test_round_trip_is_identical_and_validates_identity(self) -> None:
        packet = compile_context(
            _context_graph(include_geography_fk=True),
            "sales city",
            seed_limit=1,
            max_objects=3,
        )
        payload = packet.to_dict()

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "packet.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_context_packet(path)

        comparison = diff_context_packets(loaded, context_packet_from_dict(payload))
        self.assertTrue(comparison.identical)
        self.assertFalse(comparison.stable_changed)
        self.assertFalse(comparison.dynamic_changed)

    def test_tampered_packet_is_rejected(self) -> None:
        payload = compile_context(_context_graph(), "sales").to_dict()
        payload["dynamic"]["query"] = "changed after hashing"

        with self.assertRaisesRegex(ContextFailure, "identity hashes"):
            context_packet_from_dict(payload)

    def test_diff_distinguishes_dynamic_and_stable_changes(self) -> None:
        payload = compile_context(_context_graph(), "sales").to_dict()
        dynamic = json.loads(json.dumps(payload))
        dynamic["dynamic"]["query"] = "sales by year"
        _rehash(dynamic)
        stable = json.loads(json.dumps(payload))
        stable["stable"]["scope"]["namespace"] = "sales"
        _rehash(stable)

        dynamic_diff = diff_context_packets(
            context_packet_from_dict(payload),
            context_packet_from_dict(dynamic),
        )
        stable_diff = diff_context_packets(
            context_packet_from_dict(payload),
            context_packet_from_dict(stable),
        )

        self.assertTrue(dynamic_diff.query_changed)
        self.assertTrue(dynamic_diff.dynamic_changed)
        self.assertFalse(dynamic_diff.stable_changed)
        self.assertTrue(stable_diff.scope_changed)
        self.assertTrue(stable_diff.stable_changed)
        self.assertFalse(stable_diff.dynamic_changed)

    def test_graph_revision_change_invalidates_stable_identity(self) -> None:
        payload = compile_context(_context_graph(), "sales").to_dict()
        changed = json.loads(json.dumps(payload))
        changed["stable"]["graph"]["revision"] = "f" * 64
        _rehash(changed)

        comparison = diff_context_packets(
            context_packet_from_dict(payload),
            context_packet_from_dict(changed),
        )

        self.assertTrue(comparison.graph_revision_changed)
        self.assertTrue(comparison.stable_changed)
        self.assertFalse(comparison.dynamic_changed)

    def test_cli_diff_reports_identical_packets(self) -> None:
        payload = compile_context(_context_graph(), "sales").to_dict()
        with TemporaryDirectory() as temporary_directory:
            left = Path(temporary_directory) / "left.json"
            right = Path(temporary_directory) / "right.json"
            rendered = json.dumps(payload)
            left.write_text(rendered, encoding="utf-8")
            right.write_text(rendered, encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["context", "diff", str(left), str(right), "--format", "json"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(json.loads(output.getvalue())["identical"])


def _rehash(payload: dict[str, object]) -> None:
    from tarel.context_output import canonical_hash

    stable_hash = canonical_hash(payload["stable"])
    dynamic_hash = canonical_hash(payload["dynamic"])
    payload["identity"] = {
        "dynamic_hash": dynamic_hash,
        "packet_hash": canonical_hash(
            {
                "contract_version": payload["contract_version"],
                "dynamic_hash": dynamic_hash,
                "stable_hash": stable_hash,
            }
        ),
        "stable_hash": stable_hash,
    }
