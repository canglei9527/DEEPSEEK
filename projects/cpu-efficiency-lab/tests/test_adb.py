import unittest
from unittest.mock import patch

from cpu_efficiency_lab.adb import AdbClient, AdbError


class AdbTests(unittest.TestCase):
    @patch("cpu_efficiency_lab.adb.subprocess.run")
    def test_root_script_is_quoted_as_one_remote_command(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "uid=0\r\n"
        run.return_value.stderr = ""
        client = AdbClient("serial", adb_path="adb")
        output = client.run_root_script("printf 'hello world'", timeout_s=3)
        self.assertEqual(output, "uid=0\n")
        command = run.call_args.args[0]
        self.assertEqual(command[0:3], ["adb", "-s", "serial"])
        self.assertEqual(command[3], "shell")
        self.assertIn("su -c", command[4])
        self.assertIn("hello world", command[4])
        self.assertEqual(run.call_args.kwargs["timeout"], 3)

    @patch("cpu_efficiency_lab.adb.subprocess.run")
    def test_long_root_script_is_sent_over_stdin_without_pty(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "uid=0\r\n"
        run.return_value.stderr = ""
        script = "printf ok\n" + "#x\n" * 20000
        output = AdbClient("serial", adb_path="adb").run_root_script_stdin(script, timeout_s=9)
        self.assertEqual(output, "uid=0\n")
        self.assertEqual(run.call_args.args[0], ["adb", "-s", "serial", "shell", "-T", "su -c sh"])
        self.assertEqual(run.call_args.kwargs["input"], script + "\n")
        self.assertEqual(run.call_args.kwargs["timeout"], 9)

    @patch("cpu_efficiency_lab.adb.subprocess.run")
    def test_nonzero_adb_status_is_an_error(self, run):
        run.return_value.returncode = 1
        run.return_value.stdout = ""
        run.return_value.stderr = "device offline"
        with self.assertRaises(AdbError):
            AdbClient("serial", adb_path="adb").run(["get-state"])


if __name__ == "__main__":
    unittest.main()
