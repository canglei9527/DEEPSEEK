"""Headless tests for the quota view's polling and close lifecycle."""

import types
import unittest
from unittest.mock import Mock

from codex_resume_ui import ResumeWindow


class ResumeWindowLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.view = ResumeWindow(Mock(), self.service)
        self.window = Mock()
        self.window.winfo_viewable.return_value = True
        self.window.after.return_value = "timer-1"
        self.view._window = self.window
        self.view._refresh_snapshot = Mock()

    def test_capacity_and_legacy_rows_match_the_five_visible_columns(self):
        self.view.tree = Mock()
        self.view.summary_var = Mock()
        self.view._selection_changed = Mock()
        self.view._update_items([
            {"key": "capacity", "title": "fixture", "kind": "capacity", "state": "waiting"},
            {"key": "legacy", "title": "old", "state": "waiting"},
        ])
        rows = [call.kwargs["values"] for call in self.view.tree.insert.call_args_list]
        self.assertEqual(len(rows[0]), 5)
        self.assertEqual(rows[0][1:3], ("容量", "等待重试"))
        self.assertEqual(rows[1][1], "额度")

    def test_map_has_one_poll_timer_and_child_map_does_not_refresh(self):
        self.view._on_map(types.SimpleNamespace(widget=Mock()))
        self.view._refresh_snapshot.assert_not_called()
        event = types.SimpleNamespace(widget=self.window)
        self.view._on_map(event)
        self.view._on_map(event)
        self.view._refresh_snapshot.assert_called_once_with()
        self.window.after.assert_called_once_with(2000, self.view._tick)

    def test_hidden_tick_does_no_snapshot_read_or_poll(self):
        self.window.winfo_viewable.return_value = False
        self.view._timer_id = "timer-1"
        self.view._tick()
        self.assertIsNone(self.view._timer_id)
        self.view._refresh_snapshot.assert_not_called()
        self.window.after.assert_not_called()
        self.service.assert_not_called()

    def test_hide_cancels_refresh_and_restore_restarts_once(self):
        event = types.SimpleNamespace(widget=self.window)
        self.view._resume_refresh()
        self.view._on_unmap(event)
        self.window.after_cancel.assert_called_once_with("timer-1")
        self.assertIsNone(self.view._timer_id)
        self.view._on_map(event)
        self.assertEqual(self.view._refresh_snapshot.call_count, 2)
        self.assertEqual(self.window.after.call_count, 2)

    def test_close_destroys_only_the_child_window_and_cancels_timer(self):
        self.view._timer_id = "timer-1"
        self.view.close()
        self.window.after_cancel.assert_called_once_with("timer-1")
        self.window.destroy.assert_called_once_with()
        self.assertIsNone(self.view._window)
        self.assertEqual(self.service.mock_calls, [])
        self.view.close()
        self.window.destroy.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
