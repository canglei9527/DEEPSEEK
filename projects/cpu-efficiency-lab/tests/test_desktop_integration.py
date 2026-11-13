"""Optional real-Tk integration tests; run explicitly on a Windows desktop."""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

from cpu_efficiency_lab import desktop


@unittest.skipUnless(os.environ.get('CPU_LAB_TK_TEST') == '1', 'set CPU_LAB_TK_TEST=1 for real Tk verification')
class DesktopIntegrationTests(unittest.TestCase):
    def test_real_controls_launch_commands_and_update_preview(self):
        root = desktop.Tk()
        try:
            with mock.patch.object(desktop.Desktop, 'refresh_devices'):
                app = desktop.Desktop(root)
            with tempfile.TemporaryDirectory() as directory:
                app.output_parent.set(directory)
                app.single_path.set(str(Path('data/eeff.tsv').resolve()))
                app.combo_path.set(str(Path('data/eeff_combo.tsv').resolve()))
                app.export_path.set(str(Path('data/scheduler-sources-exe-20261112-204339').resolve()))
                root.update()
                curves = next(button for button in app.task_buttons if button.cget('text') == '重新分析')
                curves.invoke()
                self.wait_task(root, app)
                self.assertEqual(len(app.current_efficiency['cores']), 8)
                self.assertGreater(len(app.preview.find_all()), 25)
                self.assertGreater(len(app.combo_preview.find_all()), 25)
                self.assertEqual(app.core_metric.cget('text').split()[0], 'CPU0')
                cpu7 = next(button for button in app.core_buttons if button.cget('text') == 'CPU7')
                cpu7.invoke()
                self.assertIn('CPU7', app.core_metric.cget('text'))
                self.assertTrue((app.latest_result / 'index.html').is_file())
                efficiency = app.latest_result
                with mock.patch.object(desktop.os, 'startfile', create=True) as opened:
                    app.open_latest()
                    opened.assert_called_once_with(str(efficiency / 'index.html'))
                app.generate_scheduler()
                self.wait_task(root, app)
                self.assertTrue((app.latest_result / 'report.html').is_file())
                with mock.patch.object(desktop.messagebox, 'askyesno', return_value=True):
                    app.generate_module()
                self.wait_task(root, app)
                self.assertTrue((app.latest_result / 'module.zip').is_file())
                self.assertEqual(app.status.get(), '完成')
                app.show_efficiency(efficiency)
                self.assertEqual(app.latest_result, efficiency)
                self.assertTrue(all(button.instate(['disabled']) for button in app.permanently_disabled))
                frame = app.tabs.master
                self.assertLess(app.log.winfo_y(), frame.winfo_height())
                self.assertGreater(app.log.winfo_height(), 70)
                self.assertEqual(app.tabs.index(app.tabs.select()), 0)
        finally:
            root.destroy()

    def wait_task(self, root, app):
        deadline = time.monotonic() + 30
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(.02)
        root.update()
        self.assertFalse(app.busy, 'background command timed out')
        self.assertEqual(app.status.get(), '完成', app.log.get('1.0', 'end'))


if __name__ == '__main__':
    unittest.main()
