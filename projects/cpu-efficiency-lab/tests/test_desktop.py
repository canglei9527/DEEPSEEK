import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cpu_efficiency_lab import desktop


class DesktopTests(unittest.TestCase):
    def test_adb_device_list_excludes_offline_and_unauthorized(self):
        ready, states = desktop.parse_adb_devices(
            "List of devices attached\n"
            "usb1\tdevice product:phone\n"
            "emulator-5554 device model:phone\n"
            "usb2\tunauthorized\n"
            "usb3\toffline\n"
        )
        self.assertEqual(ready, ["usb1", "emulator-5554"])
        self.assertEqual(states[-2:], [("usb2", "unauthorized"), ("usb3", "offline")])

    def test_output_name_stays_in_selected_folder_and_refuses_existing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(desktop.fresh_child(directory, "curve"), Path(directory) / "curve")
            (Path(directory) / "existing").mkdir()
            for name in ("existing", "../outside", "..", "a/b", "a\\b", ""):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    desktop.fresh_child(directory, name)

    def test_source_and_frozen_commands_use_cli_dispatch(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(desktop.cli_command(["preflight", "--serial", "a"])[-5:],
                             ["cpu_efficiency_lab.desktop", "--cli", "preflight", "--serial", "a"])
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(desktop.cli_command(["preflight"]), [sys.executable, "--cli", "preflight"])

    def test_new_offline_dispatches_preserve_arguments(self):
        for option, module in (("--efficiency-report", "efficiency_report"), ("--ksu-module", "ksu_module")):
            with self.subTest(option=option), mock.patch("cpu_efficiency_lab." + module + ".main", return_value=0) as handler:
                self.assertEqual(desktop.main([option, "--output", "new"]), 0)
                handler.assert_called_once_with(["--output", "new"])

    def test_generation_uses_visible_paths_and_updates_pending_result(self):
        app = desktop.Desktop.__new__(desktop.Desktop)
        app.single_path = mock.Mock(get=mock.Mock(return_value="single.tsv"))
        app.combo_path = mock.Mock(get=mock.Mock(return_value="combo.tsv"))
        app.export_path = mock.Mock(get=mock.Mock(return_value="export"))
        app.new_result = mock.Mock(return_value=Path("new-report"))
        app.start_result = mock.Mock()
        app.generate_efficiency()
        command, output, kind = app.start_result.call_args.args
        self.assertEqual(kind, "efficiency")
        self.assertEqual(command[-7:], ["--efficiency-report", "--single", "single.tsv", "--combo", "combo.tsv", "--output", "new-report"])
        self.assertEqual(output, Path("new-report"))
        app.generate_scheduler()
        self.assertEqual(app.start_result.call_args.args[0][-5:], ["scheduler-report", "--input", "export", "--output", "new-report"])

    def test_preflight_output_is_reused_by_measurement_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            app = desktop.Desktop.__new__(desktop.Desktop)
            run = Path(directory) / "preflight-run"
            run.mkdir()
            preflight = run / "preflight.json"
            preflight.write_text("{}", encoding="utf-8")
            app.serial = mock.Mock(return_value="phone")
            app.new_result = mock.Mock(return_value=run)
            app.preflight_path = mock.Mock(get=mock.Mock(return_value=str(preflight)))
            app.start = mock.Mock()
            app.output = mock.Mock(return_value="new-plan.json")
            with mock.patch.object(desktop, "cli_command", side_effect=lambda args: args):
                app.preflight()
                self.assertEqual(app.start.call_args.args[0], [
                    "preflight", "--serial", "phone", "--output", str(preflight),
                ])
                app.preflight_path.set.assert_called_once_with(str(preflight))
                app.plan()
            self.assertEqual(app.start.call_args.args[0], [
                "two-hour-plan", "--preflight", str(preflight), "--output", "new-plan.json",
            ])

    def test_busy_state_keeps_recovery_buttons_disabled(self):
        app = desktop.Desktop.__new__(desktop.Desktop)
        first, gated = mock.Mock(), mock.Mock()
        app.task_buttons = [first, gated]
        app.permanently_disabled = {gated}
        app.progress = mock.Mock()
        app.set_busy(True)
        first.state.assert_called_with(["disabled"])
        app.set_busy(False)
        first.state.assert_called_with(["!disabled"])
        gated.state.assert_called_with(["disabled"])
        app.progress.start.assert_called_once()
        app.progress.stop.assert_called_once()

    def test_cli_dispatch_preserves_recovery_gate_without_device_access(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stderr(io.StringIO()) as error:
            output = Path(directory) / "new-run"
            code = desktop.main(["--cli", "two-hour-sweep", "--serial", "unused", "--output", str(output),
                                 "--confirm-device-writes", "--confirm-core-hotplug",
                                 "--confirm-independent-recovery", "--confirm-screen-off"])
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            self.assertTrue(error.getvalue().strip())

    def test_historical_dispatch_preserves_arguments(self):
        with mock.patch("cpu_efficiency_lab.historical_report.main") as report:
            report.side_effect = lambda: self.assertEqual(sys.argv[1:], ["--single", "a", "--combo", "b", "--output", "c"])
            original = sys.argv
            self.assertEqual(desktop.main(["--historical-report", "--single", "a", "--combo", "b", "--output", "c"]), 0)
            self.assertIs(sys.argv, original)

    def test_scheduler_report_gui_uses_selected_export_and_new_output(self):
        app = desktop.Desktop.__new__(desktop.Desktop)
        app.output = mock.Mock(return_value="new-output")
        app.start = mock.Mock()
        with mock.patch.object(desktop.filedialog, "askdirectory", return_value="export-dir"), \
                mock.patch.object(desktop, "cli_command", side_effect=lambda args: args):
            app.scheduler_report()
        app.start.assert_called_once_with([
            "scheduler-report", "--input", "export-dir", "--output", "new-output",
        ])

    def test_scheduler_source_dispatch_preserves_identity_check_inputs(self):
        args = ["--serial", "phone", "--expected-model", "model", "--expected-device", "device", "--output", "new"]
        with mock.patch("cpu_efficiency_lab.scheduler_sources.main", return_value=0) as sources:
            self.assertEqual(desktop.main(["--scheduler-sources", *args]), 0)
            sources.assert_called_once_with(args)

    def test_scheduler_source_gui_uses_user_confirmed_identity(self):
        app = desktop.Desktop.__new__(desktop.Desktop)
        app.root = object()
        app.serial = mock.Mock(return_value="phone")
        app.output = mock.Mock(return_value="new-output")
        app.start = mock.Mock()
        with mock.patch.object(desktop.simpledialog, "askstring", side_effect=[" model ", " device "]):
            app.scheduler_sources()
        self.assertEqual(app.start.call_args.args[0][-9:], [
            "--scheduler-sources", "--serial", "phone", "--expected-model", "model",
            "--expected-device", "device", "--output", "new-output",
        ])


if __name__ == "__main__":
    unittest.main()
