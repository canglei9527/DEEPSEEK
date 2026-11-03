"""Replay provider capacity errors without submitting a real model request."""
import copy
import json
from dataclasses import asdict
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_quota_resume import (
    CAPACITY_MAX_ATTEMPTS, CAPACITY_PROMPT, RecoveryPolicy, RecoveryStore,
    QuotaResumeService, capacity_retry_delay, classify_failure, failure_identity,
    Recovery, STATE_NAME,
)
from test_codex_quota_resume import FakeClock, FakeDesktop, FakeTools, THREAD, failed_thread


def capacity_state(turn_id="capacity-0"):
    return failed_thread(turn_id=turn_id, error={
        "message": "Selected model is at capacity. Please try a different model.",
        "codexErrorInfo": "serverOverloaded", "additionalDetails": None,
    })


class CapacityClassificationTests(unittest.TestCase):
    def test_legacy_dispatched_waiting_record_cannot_send_again_after_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            item = Recovery(key="thread/turn", thread_id="thread", turn_id="turn",
                            title="fixture", kind="capacity", attempts=1, state="waiting")
            row = asdict(item); row.pop("continuation_turn_id")
            (Path(directory) / STATE_NAME).write_text(json.dumps(
                {"version": 1, "enabled": True, "items": [row]}), encoding="utf-8")
            store = RecoveryStore(Path(directory))
            self.assertEqual(store.items[item.key].state, "uncertain")

    def test_real_error_shape_and_variants(self):
        for info in ("serverOverloaded", "flexUnavailable", {"serverOverloaded": {}}):
            self.assertEqual(classify_failure({"codexErrorInfo": info}), "capacity")
        for error in ({"message": capacity_state()["turns"][-1]["error"]["message"]},
                      {"code": 429}, {"codexErrorInfo": "httpConnectionFailed"}):
            self.assertIsNone(classify_failure(error))

    def test_backoff_is_capped(self):
        self.assertEqual([capacity_retry_delay(n) for n in range(7)],
                         [180, 360, 720, 1440, 1800, 1800, 1800])

    def test_manual_pause_and_interrupt_are_never_eligible(self):
        for status in ("paused", "blocked", "budgetLimited", "complete"):
            state = capacity_state(); state["goal"] = {"status": status}
            self.assertIsNone(failure_identity(state))
        state = capacity_state(); state["turns"][-1]["status"] = "interrupted"
        self.assertIsNone(failure_identity(state))


class CapacityServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock, self.tools = FakeClock(), FakeTools()
        self.service = QuotaResumeService(Path(self.temp.name), clock=self.clock,
            desktop_factory=FakeDesktop, tools_factory=lambda **kw: self.tools)
        self.addCleanup(self.service.close)
        self.service.set_enabled(True); self.service._connect()
        self.desktop = self.service._desktop
        self.desktop.states[THREAD] = capacity_state()
        self.service._on_state(THREAD, self.desktop.states[THREAD])

    def item(self, turn_id="capacity-0"):
        return self.service.store.items[f"{THREAD}/{turn_id}"]

    def due(self):
        self.clock.now = self.item().reset_at

    def test_waits_three_minutes_then_resumes_same_thread_without_account_poll(self):
        item = self.item()
        self.assertEqual(item.reset_at, item.created_at + 180)
        self.clock.now = item.reset_at - 1
        self.service._check_pending(self.clock())
        self.assertFalse(self.desktop.sent)
        self.due(); self.service._check_pending(self.clock())
        self.assertEqual(len(self.desktop.sent), 1)
        self.assertEqual(self.desktop.sent[0][:3], (THREAD, "capacity-0", CAPACITY_PROMPT))
        self.assertEqual(item.state, "resumed")
        self.assertEqual(self.tools.usage_calls, 0)

    def test_waiting_does_not_rewrite_journal_on_unchanged_checks(self):
        self.service._check_pending(self.clock())
        with patch.object(self.service.store, "save", wraps=self.service.store.save) as save:
            for _ in range(10):
                self.service._check_pending(self.clock())
            save.assert_not_called()
        self.assertFalse(self.desktop.sent)

    def test_acknowledgement_without_new_turn_never_requeues_same_failure(self):
        self.desktop.confirm_new_turn = False
        self.due(); self.service._check_pending(self.clock())
        self.assertEqual(self.item().state, "resuming")
        self.clock.now += 7200
        self.service._check_pending(self.clock())
        self.assertEqual(len(self.desktop.sent), 1)

    def test_lost_reply_is_not_retried_including_after_restart(self):
        self.desktop.lost_reply = True
        self.due(); self.service._check_pending(self.clock())
        self.assertEqual(self.item().state, "uncertain")
        self.assertEqual(RecoveryStore(self.service.home).items[self.item().key].state, "uncertain")
        self.clock.now += 7200; self.service._check_pending(self.clock())
        self.assertEqual(len(self.desktop.sent), 1)

    def test_manual_stop_new_input_and_cancel_prevent_dispatch(self):
        for change in ("stop", "input", "pause", "cancel", "disable"):
            with self.subTest(change=change):
                state = capacity_state()
                if change == "stop": state["turns"][-1]["status"] = "interrupted"
                if change == "input": state["turns"] = [{"id": "user-next", "status": "inProgress"}]
                if change == "pause": state["goal"] = {"status": "paused"}
                self.desktop.states[THREAD] = state
                # Reset only the fixture so each branch tests a fresh pending record.
                item = self.item(); item.state = "waiting"
                if change == "cancel": self.service.exclude(item.key)
                elif change == "disable": self.service.set_enabled(False)
                else: self.service._on_state(THREAD, state)
                self.due(); self.service._check_pending(self.clock())
                self.assertFalse(self.desktop.sent)

    def test_synchronous_new_capacity_failures_carry_count_and_stop_at_limit(self):
        dispatched = []
        def resume(thread_id, turn_id, prompt, *, should_continue):
            self.assertTrue(should_continue())
            dispatched.append(turn_id)
            new_id = f"capacity-{len(dispatched)}"
            state = capacity_state(new_id)
            self.desktop.states[THREAD] = state
            # The receiver may report a failure before the dispatch ack returns.
            self.service._on_state(THREAD, state)
            return {"accepted": True}
        self.desktop.resume = resume
        for n in range(CAPACITY_MAX_ATTEMPTS):
            item = self.item(f"capacity-{n}")
            self.assertEqual(item.attempts, n)
            self.assertEqual(item.reset_at, item.created_at + capacity_retry_delay(n))
            self.clock.now = item.reset_at
            self.service._check_pending(self.clock())
        self.service._check_pending(self.clock())
        self.assertEqual(len(dispatched), CAPACITY_MAX_ATTEMPTS)
        self.assertEqual(self.item(f"capacity-{CAPACITY_MAX_ATTEMPTS}").state, "exhausted")
        self.assertEqual(len(set(dispatched)), len(dispatched))
        state = capacity_state("fresh-user-request")
        self.service._on_state(THREAD, state)
        self.assertEqual(self.item("fresh-user-request").attempts, 0)

    def test_runtime_active_before_new_turn_is_not_an_interrupt(self):
        item = self.item(); self.service.policy.claim(item.key, capacity_state(), self.clock())
        state = capacity_state(); state["status"] = {"type": "active"}
        self.service._on_state(THREAD, state)
        self.assertEqual(item.state, "resuming")
