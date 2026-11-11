import json
import tempfile
import unittest
from pathlib import Path

from cpu_efficiency_lab.cli import _parser, _saved_screen_timeout


class CliTests(unittest.TestCase):
    def test_preflight_arguments(self):
        args = _parser().parse_args(["preflight", "--serial", "phone"])
        self.assertEqual(args.command, "preflight")
        self.assertEqual(args.serial, "phone")

    def test_capture_requires_output(self):
        with self.assertRaises(SystemExit):
            _parser().parse_args(["capture-power", "--serial", "phone"])

    def test_cpu_sweep_requires_explicit_write_confirmation_at_runtime(self):
        args = _parser().parse_args(["cpu-sweep", "--serial", "phone", "--output", "data/run"])
        self.assertFalse(args.confirm_device_writes)

    def test_matrix_sweep_requires_hotplug_and_write_confirmations(self):
        args = _parser().parse_args(["matrix-sweep", "--serial", "phone", "--output", "data/matrix"])
        self.assertFalse(args.confirm_device_writes)
        self.assertFalse(args.confirm_core_hotplug)
        self.assertFalse(args.confirm_independent_recovery)
        self.assertEqual(args.minimum_battery_percent, 60)

    def test_saved_screen_timeout_accepts_known_appended_transcript(self):
        record = {
            "serial": "phone",
            "original_screen_off_timeout_ms": 1800000,
            "requested_temporary_screen_off_timeout_ms": 2147483647,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(record) + "\n\nAPPLY_OUTPUT\nSCREEN_TIMEOUT|1800000|2147483647\n", encoding="utf-8")
            self.assertEqual(_saved_screen_timeout(path, "phone"), 1800000)
            self.assertIsNone(_saved_screen_timeout(path, "other-phone"))

    def test_saved_screen_timeout_rejects_unrecognized_trailing_data(self):
        record = {
            "serial": "phone",
            "original_screen_off_timeout_ms": 1800000,
            "requested_temporary_screen_off_timeout_ms": 2147483647,
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps(record) + "\nUNEXPECTED", encoding="utf-8")
            self.assertIsNone(_saved_screen_timeout(path, "phone"))


if __name__ == "__main__":
    unittest.main()
