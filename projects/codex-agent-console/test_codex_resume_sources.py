# -*- coding: utf-8 -*-
from contextlib import closing
import io
import json
from pathlib import Path
import queue
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest import mock

import codex_resume_sources as sources


def encoded(value):
    return (json.dumps(value) + "\n").encode()


class RecordingInput(io.BytesIO):
    def close(self):
        self.saved = self.getvalue()
        super().close()


class FakeProcess:
    def __init__(self, replies):
        self.stdin = RecordingInput()
        self.stdout = io.BytesIO(b"".join(encoded(value) for value in replies))
        self.killed = False

    def poll(self):
        return 0 if self.killed else None

    def kill(self):
        self.killed = True

    def wait(self, timeout):
        return 0


class ResumeSourcesTests(unittest.TestCase):
    def make_source(self, directory="unused", **kwargs):
        return sources.ResumeSources(directory, executable="bundled-codex.exe", **kwargs)

    def test_account_only_protocol_never_loads_or_starts_thread(self):
        process = FakeProcess([{"id": 1, "result": {}}, {"id": 2, "result": {
            "rateLimits": {"primary": {"usedPercent": 90}}, "ordinaryUsageAllowed": True}}])
        with mock.patch.object(subprocess, "Popen", return_value=process) as popen, \
                mock.patch("builtins.open", side_effect=AssertionError("No credential reads")):
            value = self.make_source().usage_limits("must-never-be-sent")
        self.assertTrue(value["ordinaryUsageAllowed"])
        requests = [json.loads(line) for line in process.stdin.saved.splitlines()]
        self.assertEqual([entry["method"] for entry in requests],
                         ["initialize", "initialized", "account/rateLimits/read"])
        self.assertNotIn("must-never-be-sent", process.stdin.saved.decode())
        self.assertTrue(process.killed)
        self.assertEqual(popen.call_args.args[0], ["bundled-codex.exe", "app-server"])
        self.assertEqual(popen.call_args.kwargs["env"]["CODEX_HOME"], "unused")
        self.assertEqual(popen.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_rpc_errors_malformed_schema_and_wrong_id_are_rejected(self):
        for response in [{"id": 2, "error": {"message": "sensitive"}},
                         {"id": 9, "result": {}}, {"id": 2, "result": []},
                         {"id": 2, "result": {}}]:
            with self.subTest(response=response):
                process = FakeProcess([{"id": 1, "result": {}}, response])
                with mock.patch.object(subprocess, "Popen", return_value=process):
                    with self.assertRaises(sources.ResumeSourcesError) as error:
                        self.make_source().usage_limits()
                self.assertNotIn("sensitive", str(error.exception))
                self.assertTrue(process.killed)

    def test_timeout_kills_process_with_bounded_deadline(self):
        process = FakeProcess([])
        started = time.monotonic()
        with mock.patch.object(subprocess, "Popen", return_value=process), \
                mock.patch.object(sources, "_read_messages"):
            with self.assertRaisesRegex(sources.ResumeSourcesError, "超时"):
                self.make_source(timeout=0.02).usage_limits()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(process.killed)

    def test_notifications_malformed_json_and_large_lines(self):
        messages = queue.Queue(maxsize=64)
        sources._read_messages(io.BytesIO(encoded({"method": "notice"}) + encoded({"id": 1, "result": {}})), messages)
        self.assertEqual(sources._wait_result(messages, 1, time.monotonic() + 1), {})
        for data in [b"not json\n", b"\xff\n", b"[]\n"]:
            messages = queue.Queue(maxsize=64)
            sources._read_messages(io.BytesIO(data), messages)
            with self.assertRaises(sources.ResumeSourcesError):
                sources._wait_result(messages, 1, time.monotonic() + 1)
        with mock.patch.object(sources, "_MAX_LINE_BYTES", 3):
            messages = queue.Queue(maxsize=64)
            sources._read_messages(io.BytesIO(b"12345\n"), messages)
            self.assertIsInstance(messages.get_nowait(), sources.ResumeSourcesError)

    def test_discovery_reads_metadata_only_top_level_unarchived_latest_50(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state_5.sqlite"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE threads(id TEXT, source TEXT, updated_at INT, archived INT, rollout_path TEXT, title TEXT, cwd TEXT)")
                connection.executemany("INSERT INTO threads VALUES (?,?,?,?,?,?,?)", [
                    (str(i), "vscode", i, 0, "rollout", "title", "cwd") for i in range(55)] + [
                    ("subagent", '{"subagent":{}}', 99, 0, "", "", ""),
                    ("archive", "vscode", 100, 1, "", "", ""),
                    ("cli", "cli", 110, 0, "", "", "")])
                connection.commit()
            before = path.read_bytes()
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("No credential or rollout reads")), \
                    mock.patch.object(Path, "read_bytes", side_effect=AssertionError("No credential or rollout reads")):
                rows = self.make_source(directory).list_threads()["threads"]
            self.assertEqual(len(rows), 50)
            self.assertEqual(rows[0]["id"], "54")
            self.assertEqual(rows[-1]["id"], "5")
            self.assertEqual(rows[0]["status"], {"type": "unknown"})
            self.assertEqual(path.read_bytes(), before)

    def test_discovery_minimal_schema_and_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_source(directory)
            with self.assertRaises(sources.ResumeSourcesError):
                source.list_threads()
            path = Path(directory) / "state_5.sqlite"
            self.assertFalse(path.exists())
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE threads(id TEXT, source TEXT, updated_at INT, archived INT, rollout_path TEXT)")
                connection.execute("INSERT INTO threads VALUES ('desktop','appServer',1,0,'p')")
                connection.commit()
            row = source.list_threads()["threads"][0]
            self.assertEqual(row["title"], "")

    def test_invalid_timeout_and_executable_fallback(self):
        for value in (0, -1, float("nan"), float("inf"), "15"):
            with self.assertRaises(sources.ResumeSourcesError):
                sources.ResumeSources(timeout=value)
        process = FakeProcess([{"id": 1, "result": {}}, {"id": 2, "result": {"rateLimitsByLimitId": {}}}])
        with mock.patch.object(sources, "_find_executable", return_value=Path("desktop.exe")) as finder, \
                mock.patch.object(subprocess, "Popen", return_value=process) as popen:
            sources.ResumeSources().usage_limits()
        finder.assert_called_once()
        self.assertEqual(popen.call_args.args[0][0], "desktop.exe")

    def test_process_start_failure_and_early_exit_are_public_errors(self):
        with mock.patch.object(subprocess, "Popen", side_effect=OSError("missing")):
            with self.assertRaises(sources.ResumeSourcesError):
                self.make_source().usage_limits()
        process = FakeProcess([])
        with mock.patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaises(sources.ResumeSourcesError):
                self.make_source().usage_limits()
        self.assertTrue(process.killed)


if __name__ == "__main__":
    unittest.main()
