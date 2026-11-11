import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cpu_efficiency_lab.adb import AdbError
from cpu_efficiency_lab.screen_control import ScreenState

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("repeat_pinned_dynamic", ROOT / "scripts" / "repeat_pinned_dynamic.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RepeatPinnedDynamicTests(unittest.TestCase):
    def _run_mocked(self, prefix, subprocess_results):
        before = ScreenState(1800000, "Awake", True)
        with patch.object(module, "AdbClient") as client, \
             patch.object(module, "_validate_device_ready"), \
             patch.object(module, "read_screen_state", return_value=before), \
             patch.object(module, "keep_screen_awake") as keep, \
             patch.object(module, "restore_screen_timeout", return_value=before) as restore, \
             patch.object(module.subprocess, "call", side_effect=subprocess_results) as call, \
             patch.object(sys, "argv", ["repeat", "--serial", "phone", "--output", str(prefix)]):
            try:
                status = module.main()
                error = None
            except Exception as exc:
                status, error = None, exc
            self.assertTrue(keep.called)
            restore.assert_called_once_with(client.return_value, 1800000, module.DEFAULT_MATRIX_TIMEOUT_MS)
            return status, error, call.call_args_list

    def test_two_rounds_reverse_order_and_restore_screen(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "run"
            status, error, calls = self._run_mocked(prefix, [0, 0, 0])
            self.assertIsNone(error)
            self.assertEqual(status, 0)
            self.assertEqual(len(calls), 3)
            self.assertNotIn("--reverse-order", calls[0].args[0])
            self.assertIn("--reverse-order", calls[1].args[0])
            self.assertIn("plot_pinned_repeats.py", calls[2].args[0][1])
            state = json.loads(Path(str(prefix) + ".screen-state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["restored"])
            self.assertEqual(state["rounds_completed"], 2)

    def test_aborted_second_round_still_restores_screen_and_skips_plots(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "run"
            status, error, calls = self._run_mocked(prefix, [0, 1])
            self.assertIsNone(status)
            self.assertIsInstance(error, AdbError)
            self.assertEqual(len(calls), 2)
            state = json.loads(Path(str(prefix) + ".screen-state.json").read_text(encoding="utf-8"))
            self.assertTrue(state["restored"])
            self.assertEqual(state["rounds_completed"], 1)
            self.assertIn("round 2 aborted", state["run_error"])


if __name__ == "__main__":
    unittest.main()
