"""Safety and byte-integrity checks for read-only source collection."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from cpu_efficiency_lab import scheduler_sources


class SchedulerSourcesTests(unittest.TestCase):
    def setUp(self):
        self.identity = {name: "" for name in scheduler_sources.SCHEDULER_IDENTITY_PROPERTIES}
        self.identity.update({"ro.product.model": "model-x", "ro.product.device": "device-x",
                              "ro.product.first_api_level": "35"})

    def test_collect_preserves_raw_bytes_and_records_missing(self):
        raw = b'{"data":"\xff"}\r\n'
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(scheduler_sources, "AdbClient"), \
                patch.object(scheduler_sources, "read_identity", return_value=self.identity), \
                patch.object(scheduler_sources, "read_source",
                             side_effect=[("present", raw, None)] + [("missing", b"", None)] * 7):
            output = Path(directory) / "new"
            report = scheduler_sources.collect("serial", "model-x", "device-x", output)
            first = report["sources"][0]
            self.assertEqual((output / first["saved_as"]).read_bytes(), raw)
            self.assertEqual(first["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(first["size_bytes"], len(raw))
            self.assertEqual([s["status"] for s in report["sources"]], ["present"] + ["missing"] * 7)
            self.assertEqual(json.loads((output / "manifest.json").read_text(encoding="utf-8")), report)

    def test_identity_mismatch_creates_nothing(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(scheduler_sources, "AdbClient"), \
                patch.object(scheduler_sources, "read_identity", return_value=self.identity), \
                patch.object(scheduler_sources, "read_source") as read:
            output = Path(directory) / "new"
            with self.assertRaises(ValueError):
                scheduler_sources.collect("serial", "wrong", "device-x", output)
            self.assertFalse(output.exists())
            read.assert_not_called()

    def test_existing_output_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(scheduler_sources, "AdbClient"), \
                patch.object(scheduler_sources, "read_identity", return_value=self.identity), \
                patch.object(scheduler_sources, "read_source") as read:
            output = Path(directory) / "existing"
            output.mkdir()
            (output / "keep").write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                scheduler_sources.collect("serial", "model-x", "device-x", output)
            self.assertEqual((output / "keep").read_bytes(), b"original")
            read.assert_not_called()

    def test_source_paths_use_launch_api_and_reject_shell_input(self):
        self.assertIn("/system/etc/task_profiles/task_profiles_35.json", scheduler_sources.source_paths("35"))
        self.assertIn("/vendor/etc/perf/targetresourceconfigs.xml", scheduler_sources.source_paths("35"))
        self.assertIn("/vendor/etc/perf/targetconfig.xml", scheduler_sources.source_paths("35"))
        for invalid in ("", "0", "35;reboot", "../35"):
            with self.assertRaises(ValueError):
                scheduler_sources.source_paths(invalid)

    def test_remote_failure_is_not_reported_as_missing(self):
        client = type("Client", (), {
            "adb_path": "adb", "serial": "serial",
            "run_root_script": lambda self, script: "PRESENT\n" + "0" * 64 + "  /vendor/etc/cgroups.json\n1\n",
        })()
        with patch.object(scheduler_sources.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 1, b"", b"permission denied")):
            status, content, error = scheduler_sources.read_source(client, "/vendor/etc/cgroups.json")
        self.assertEqual((status, content), ("error", b""))
        self.assertEqual(error, "permission denied")

    def test_missing_is_determined_without_trusting_exec_out_status(self):
        client = type("Client", (), {
            "adb_path": "adb", "serial": "serial",
            "run_root_script": lambda self, script: "MISSING\n",
        })()
        with patch.object(scheduler_sources.subprocess, "run") as run:
            self.assertEqual(scheduler_sources.read_source(client, "/vendor/etc/cgroups.json"),
                             ("missing", b"", None))
            run.assert_not_called()

    def test_exec_out_silent_failure_fails_integrity_check(self):
        data = b"expected data"
        client = type("Client", (), {
            "adb_path": "adb", "serial": "serial",
            "run_root_script": lambda self, script: "PRESENT\n" + hashlib.sha256(data).hexdigest() + "  /file\n13\n",
        })()
        with patch.object(scheduler_sources.subprocess, "run",
                          return_value=subprocess.CompletedProcess([], 0, b"", b"")):
            status, _, error = scheduler_sources.read_source(client, "/vendor/etc/cgroups.json")
        self.assertEqual(status, "error")
        self.assertIn("differ", error)


if __name__ == "__main__":
    unittest.main()
