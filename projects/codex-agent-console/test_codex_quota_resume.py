"""Safety fixtures for automatic recovery; no real thread is ever started."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from codex_quota_resume import (
    RECHECK_SECONDS,
    RESET_GRACE_SECONDS,
    STATE_NAME,
    Recovery,
    RecoveryPolicy,
    RecoveryStore,
    QuotaResumeService,
    failure_identity,
    is_quota_error,
    quota_gate,
)


NOW = 1_800_000_000.0
THREAD = "fixture-thread"
TURN = "fixture-quota-turn"
KEY = f"{THREAD}/{TURN}"


def failed_thread(*, turn_id=TURN, error=None):
    return {
        "id": THREAD,
        "name": "额度中断 fixture",
        "model": "fixture-model",
        "status": {"type": "idle"},
        "archived": False,
        "turns": [{
            "id": turn_id,
            "status": "failed",
            "error": error if error is not None else {
                "message": "Fixture only",
                "codexErrorInfo": "usageLimitExceeded",
            },
        }],
    }


def limits(*, allowed=True, primary=20, secondary=30, primary_reset=None,
           secondary_reset=None):
    return {
        "ordinaryUsageAllowed": allowed,
        "rateLimits": {
            "primary": {"usedPercent": primary, "resetsAt": primary_reset},
            "secondary": {"usedPercent": secondary, "resetsAt": secondary_reset},
        },
    }


class QuotaIdentityTests(unittest.TestCase):
    def test_only_structured_quota_codes_are_trusted(self):
        for error in (
            {"codexErrorInfo": "usageLimitExceeded"},
            {"codex_error_info": "usageLimitReached"},
            {"code": "usageLimitExceeded"},
            {"codexErrorInfo": {"usageLimitExceeded": {}}},
        ):
            with self.subTest(error=error):
                self.assertTrue(is_quota_error(error))
                self.assertEqual(failure_identity(failed_thread(error=error)), TURN)

    def test_error_prose_network_429_and_percentages_never_arm_recovery(self):
        for error in (
            None,
            "usageLimitExceeded",
            {"message": "usageLimitExceeded; usage limit reached; try after reset"},
            {"message": "You have hit your usage limit", "httpStatusCode": 429},
            {"code": 429},
            {"code": "429"},
            {"codexErrorInfo": "httpConnectionFailed", "httpStatusCode": 429},
            {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 429}}},
            {"usedPercent": 100, "resetsAt": NOW + 60},
            {"code": "usage_limit_exceeded"},
        ):
            with self.subTest(error=error):
                self.assertFalse(is_quota_error(error))
                if error is not None:
                    self.assertIsNone(failure_identity(failed_thread(error=error)))

    def test_failed_turn_is_required_even_when_quota_error_remains_attached(self):
        for turn_status in ("interrupted", "completed", "inProgress", "cancelled", ""):
            with self.subTest(turn_status=turn_status):
                state = failed_thread()
                state["turns"][-1]["status"] = turn_status
                self.assertIsNone(failure_identity(state))

    def test_archived_active_and_goal_stop_states_disarm_the_same_failure(self):
        variants = []
        archived = failed_thread()
        archived["archived"] = True
        variants.append(("archived", archived))
        active = failed_thread()
        active["status"] = {"type": "active"}
        variants.append(("active", active))
        for goal_status in (
            "paused", "blocked", "budgetLimited", "budget_limited", "complete", "completed"
        ):
            state = failed_thread()
            state["goal"] = {"status": goal_status}
            variants.append((f"goal {goal_status}", state))
        for label, state in variants:
            with self.subTest(state=label):
                self.assertIsNone(failure_identity(state))

    def test_new_user_turn_supersedes_an_older_quota_failure(self):
        state = failed_thread()
        state["turns"].append({
            "id": "fixture-manual-turn", "status": "completed", "error": None,
            "items": [{"type": "userMessage", "text": "新指令"}],
        })
        self.assertIsNone(failure_identity(state))

    def test_missing_or_malformed_latest_turn_does_not_search_back_to_failure(self):
        for turns in ([], None, [None], [failed_thread()["turns"][0], {}]):
            with self.subTest(turns=turns):
                state = failed_thread()
                state["turns"] = turns
                self.assertIsNone(failure_identity(state))


class QuotaGateTests(unittest.TestCase):
    def test_explicit_permission_and_available_included_windows_are_both_required(self):
        self.assertTrue(quota_gate(limits(), NOW)[0])
        for allowed in (False, None, 1, "true"):
            with self.subTest(allowed=allowed):
                self.assertFalse(quota_gate(limits(allowed=allowed), NOW)[0])
        for field in ("primary", "secondary"):
            with self.subTest(exhausted=field):
                data = limits()
                data["rateLimits"][field]["usedPercent"] = 100
                data["rateLimits"][field]["resetsAt"] = NOW + 60
                self.assertFalse(quota_gate(data, NOW)[0])

    def test_unknown_or_invalid_usage_is_not_permission_to_resume(self):
        for invalid in (None, True, "0", float("nan"), float("inf"), -1):
            with self.subTest(used=invalid):
                self.assertFalse(quota_gate(limits(primary=invalid), NOW)[0])
        for data in ({}, {"ordinaryUsageAllowed": True},
                     {"ordinaryUsageAllowed": True, "rateLimits": {}},
                     {"ordinaryUsageAllowed": True, "rateLimits": {"primary": None}}):
            with self.subTest(limits=data):
                self.assertFalse(quota_gate(data, NOW)[0])

    def test_reset_timestamp_only_schedules_a_read_and_never_proves_restoration(self):
        data = limits(primary=100, primary_reset=NOW - 1)
        allowed, next_check, _ = quota_gate(data, NOW)
        self.assertFalse(allowed)
        self.assertGreaterEqual(next_check, NOW + RECHECK_SECONDS)
        data["ordinaryUsageAllowed"] = False
        data["rateLimits"]["primary"]["usedPercent"] = 0
        self.assertFalse(quota_gate(data, NOW)[0])

    def test_primary_and_weekly_exhaustion_wait_for_the_later_reset(self):
        for first, second in ((NOW + 60, NOW + 3600), (NOW + 3600, NOW + 60)):
            with self.subTest(primary=first, secondary=second):
                allowed, next_check, _ = quota_gate(limits(
                    primary=100, secondary=100,
                    primary_reset=first, secondary_reset=second,
                ), NOW)
                self.assertFalse(allowed)
                self.assertEqual(next_check, max(first, second) + RESET_GRACE_SECONDS)

    def test_a_missing_reset_keeps_the_gate_closed(self):
        for missing in (None, True, "tomorrow", float("inf")):
            with self.subTest(reset=missing):
                allowed, next_check, _ = quota_gate(limits(
                    primary=100, primary_reset=missing,
                ), NOW)
                self.assertFalse(allowed)
                self.assertGreaterEqual(next_check, NOW + RECHECK_SECONDS)

    def test_current_per_model_limits_take_priority_over_legacy_available_bucket(self):
        data = limits()
        data["rateLimitsByLimitId"] = {
            "fixture-model-limit": {
                "normalModelSlug": "fixture-model",
                "primary": {"usedPercent": 100, "resetsAt": NOW + 60},
            },
        }
        self.assertFalse(quota_gate(data, NOW, "fixture-model")[0])

    def test_spending_controls_cannot_be_bypassed_by_ordinary_permission(self):
        data = limits()
        data["rateLimits"]["spendControlReached"] = True
        self.assertFalse(quota_gate(data, NOW)[0])


class RecoveryPolicyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-resume-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.store = RecoveryStore(self.home)
        self.policy = RecoveryPolicy(self.store)

    def arm(self):
        self.assertTrue(self.policy.observe(THREAD, failed_thread(), NOW))
        self.assertEqual(set(self.store.items), {KEY})
        return self.store.items[KEY]

    def test_only_a_trusted_quota_failure_is_enqueued(self):
        state = failed_thread(error={"message": "usageLimitExceeded", "code": 429})
        state["rateLimits"] = {"primary": {"usedPercent": 100}}
        self.assertFalse(self.policy.observe(THREAD, state, NOW))
        self.assertEqual(self.store.items, {})
        item = self.arm()
        self.assertEqual(item.state, "waiting")

    def test_manual_interrupt_goal_pause_new_turn_complete_and_archive_cancel_pending(self):
        variants = []
        interrupted = failed_thread()
        interrupted["turns"][-1]["status"] = "interrupted"
        variants.append(("interrupted", interrupted))
        paused = failed_thread()
        paused["goal"] = {"status": "paused"}
        variants.append(("goal paused", paused))
        new_turn = failed_thread()
        new_turn["turns"].append({"id": "fixture-manual-turn", "status": "inProgress"})
        variants.append(("new user turn", new_turn))
        completed = failed_thread()
        completed["turns"][-1]["status"] = "completed"
        variants.append(("normal completion", completed))
        archived = failed_thread()
        archived["archived"] = True
        variants.append(("archived", archived))
        for label, live in variants:
            with self.subTest(reason=label):
                store = RecoveryStore(self.home / label.replace(" ", "-"))
                policy = RecoveryPolicy(store)
                policy.observe(THREAD, failed_thread(), NOW)
                self.assertIsNone(policy.claim(KEY, live, NOW + 1))
                self.assertNotIn(store.items[KEY].state, ("waiting", "checking", "resuming"))
                self.assertFalse(policy.observe(THREAD, live, NOW + 2))

    def test_same_failure_with_a_changed_reset_time_is_not_a_new_recovery(self):
        item = self.arm()
        item.reset_at = NOW + 60
        self.assertTrue(self.policy.exclude(KEY, NOW + 1))
        newer = failed_thread()
        newer["turns"][-1]["error"]["resetsAt"] = NOW + 3600
        self.assertFalse(self.policy.observe(THREAD, newer, NOW + 2))
        self.assertEqual(len(self.store.items), 1)
        self.assertEqual(self.store.items[KEY].state, "cancelled")

    def test_user_cancellation_survives_restart_and_cannot_rearm_same_turn(self):
        self.arm()
        self.store.enabled = True
        self.assertTrue(self.policy.exclude(KEY, NOW + 1))
        self.store.save()
        restarted = RecoveryStore(self.home)
        self.assertTrue(restarted.enabled)
        policy = RecoveryPolicy(restarted)
        self.assertEqual(restarted.items[KEY].state, "cancelled")
        self.assertFalse(policy.observe(THREAD, failed_thread(), NOW + 2))
        self.assertIsNone(policy.claim(KEY, failed_thread(), NOW + 3))

    def test_claim_is_durable_before_a_request_can_be_dispatched(self):
        self.arm()
        claimed = self.policy.claim(KEY, failed_thread(), NOW + 1)
        self.assertIsNotNone(claimed)
        persisted = json.loads((self.home / STATE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(persisted["items"][0]["state"], "resuming")
        self.assertIsNone(self.policy.claim(KEY, failed_thread(), NOW + 2))

    def test_unknown_dispatch_after_restart_is_never_replayed(self):
        self.arm()
        self.policy.claim(KEY, failed_thread(), NOW + 1)
        restarted = RecoveryStore(self.home)
        policy = RecoveryPolicy(restarted)
        self.assertEqual(restarted.items[KEY].state, "uncertain")
        self.assertFalse(policy.observe(THREAD, failed_thread(), NOW + 2))
        self.assertIsNone(policy.claim(KEY, failed_thread(), NOW + 3))
        self.assertEqual(restarted.items[KEY].state, "uncertain")

    def test_failed_journal_write_never_returns_a_dispatch_claim(self):
        self.arm()
        with patch.object(self.store, "save", side_effect=OSError("fixture disk full")):
            with self.assertRaises(OSError):
                self.policy.claim(KEY, failed_thread(), NOW + 1)
        self.assertIsNone(self.policy.claim(KEY, failed_thread(), NOW + 2))

    def test_restart_of_a_check_in_progress_requires_review_instead_of_retry(self):
        item = self.arm()
        item.state = "checking"
        self.store.save()
        restarted = RecoveryStore(self.home)
        self.assertEqual(restarted.items[KEY].state, "uncertain")
        self.assertIsNone(RecoveryPolicy(restarted).claim(KEY, failed_thread(), NOW + 1))

    def test_missing_and_corrupt_journals_default_to_disabled(self):
        self.assertFalse(self.store.enabled)
        self.assertEqual(self.store.items, {})
        for raw in ("not json", "[]", '{"version":99,"enabled":true,"items":[]}',
                    '{"version":1,"enabled":true,"items":[{}]}'):
            with self.subTest(journal=raw):
                (self.home / STATE_NAME).write_text(raw, encoding="utf-8")
                restarted = RecoveryStore(self.home)
                self.assertFalse(restarted.enabled)
                self.assertEqual(restarted.items, {})

    def test_missing_or_invalid_items_field_is_a_corrupt_journal(self):
        for data in (
            {"version": 1, "enabled": True},
            {"version": 1, "enabled": True, "items": {}},
            {"version": 1, "enabled": True, "items": ""},
            {"version": 1, "enabled": True, "items": None},
        ):
            with self.subTest(journal=data):
                (self.home / STATE_NAME).write_text(json.dumps(data), encoding="utf-8")
                restarted = RecoveryStore(self.home)
                self.assertFalse(restarted.enabled)
                self.assertEqual(restarted.items, {})

    def test_claim_and_cancellation_never_mutate_the_live_thread_fixture(self):
        self.arm()
        live = failed_thread()
        original = copy.deepcopy(live)
        self.policy.claim(KEY, live, NOW + 1)
        self.assertEqual(live, original)

    def test_conflicting_duplicate_identity_is_a_corrupt_journal(self):
        self.arm()
        self.policy.exclude(KEY, NOW + 1)
        self.store.enabled = True
        self.store.save()
        path = self.home / STATE_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        duplicate = copy.deepcopy(data["items"][0])
        duplicate["state"] = "waiting"
        data["items"].append(duplicate)
        path.write_text(json.dumps(data), encoding="utf-8")
        restarted = RecoveryStore(self.home)
        self.assertFalse(restarted.enabled)
        self.assertEqual(restarted.items, {})

    def test_cancellation_and_dispatch_tombstones_survive_history_compaction(self):
        for terminal in ("cancelled", "uncertain", "resumed"):
            with self.subTest(terminal=terminal):
                home = self.home / terminal
                store = RecoveryStore(home)
                policy = RecoveryPolicy(store)
                policy.observe(THREAD, failed_thread(), NOW)
                store.items[KEY].state = terminal
                store.items[KEY].updated_at = NOW
                for index in range(205):
                    turn = f"fixture-other-{index}"
                    key = f"other-thread/{turn}"
                    store.items[key] = Recovery(
                        key=key, thread_id="other-thread", turn_id=turn,
                        title="Fixture history", state="cancelled",
                        created_at=NOW + index + 1, updated_at=NOW + index + 1,
                    )
                store.save()
                restarted = RecoveryStore(home)
                self.assertFalse(RecoveryPolicy(restarted).observe(THREAD, failed_thread(), NOW + 300))
                self.assertFalse(any(
                    item.thread_id == THREAD and item.state == "waiting"
                    for item in restarted.items.values()
                ))


class FakeClock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


class FakeTools:
    def __init__(self):
        self.limits = limits(primary=100, primary_reset=NOW + 60)
        self.list_calls = 0
        self.usage_calls = 0
        self.usage_error = None

    def list_threads(self):
        self.list_calls += 1
        return {"threads": [{"id": THREAD, "kind": "codex", "hostId": "local"}]}

    def usage_limits(self, *, thread_id):
        if thread_id != THREAD:
            raise AssertionError("Fixture must not query a real thread")
        self.usage_calls += 1
        if self.usage_error is not None:
            raise self.usage_error
        return copy.deepcopy(self.limits)


class FakeDesktop:
    """Deterministic adapter double; the real IPC adapter has separate tests."""

    def __init__(self, *, on_state, on_disconnect):
        self.on_state = on_state
        self.on_disconnect = on_disconnect
        self.states = {THREAD: failed_thread()}
        self.connected = False
        self.owner_available = True
        self.refreshes = []
        self.sent = []
        self.before_resume = None
        self.lost_reply = False
        self.confirm_new_turn = True

    def connect(self):
        self.connected = True

    def close(self):
        self.connected = False

    def follow(self, thread_id):
        state = self.refresh(thread_id)
        self.on_state(thread_id, state)
        return state

    def refresh(self, thread_id):
        self.refreshes.append(thread_id)
        if not self.connected:
            raise RuntimeError("Fixture desktop disconnected")
        if not self.owner_available:
            raise RuntimeError("Fixture has no Desktop owner")
        if thread_id not in self.states:
            raise RuntimeError("Fixture thread not loaded")
        return copy.deepcopy(self.states[thread_id])

    def resume(self, thread_id, expected_turn_id, prompt, *, should_continue):
        if self.before_resume is not None:
            self.before_resume()
        # Match the adapter's safety contract: two fresh snapshots and the
        # service's generation guard are required before the single dispatch.
        for _ in range(2):
            if not should_continue():
                return False
            state = self.refresh(thread_id)
            if failure_identity(state) != expected_turn_id:
                return False
        if not should_continue():
            return False
        self.sent.append((thread_id, expected_turn_id, prompt, len(self.refreshes)))
        if self.lost_reply:
            raise TimeoutError("Fixture: request may have been accepted, reply lost")
        if self.confirm_new_turn:
            self.states[thread_id]["turns"].append({
                "id": "fixture-recovery-turn", "status": "inProgress", "error": None,
            })
        return {"accepted": True}


class QuotaResumeServiceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="codex-resume-service-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.clock = FakeClock()
        self.tools = FakeTools()
        self.desktops = []
        self.service = self.new_service()

    def new_service(self):
        def desktop_factory(**callbacks):
            desktop = FakeDesktop(**callbacks)
            self.desktops.append(desktop)
            return desktop

        service = QuotaResumeService(
            self.home, desktop_factory=desktop_factory,
            tools_factory=lambda **_kwargs: self.tools, clock=self.clock,
        )
        # Most tests drive the state machine directly rather than starting a
        # wall-clock worker. Release its real OS lock before deleting fixtures.
        self.addCleanup(service._process_lock.close)
        self.addCleanup(service.close)
        return service

    def arm(self):
        self.service.set_enabled(True)
        self.service._connect()
        self.service._discover(self.clock())
        self.assertIn(KEY, self.service.store.items)
        self.desktop = self.desktops[-1]
        self.clock.now += 60
        return self.desktop

    def check(self):
        self.service._check_pending(self.clock())

    def make_available(self):
        self.tools.limits = limits(primary=10, secondary=25)
        self.service.wake()

    def test_wait_restoration_rechecks_original_thread_and_sends_exactly_once(self):
        desktop = self.arm()
        self.check()
        self.assertEqual(desktop.sent, [])
        self.assertEqual(self.tools.usage_calls, 1)
        due = self.service.store.items[KEY].reset_at
        for _ in range(10):
            self.service._on_state(THREAD, failed_thread())
            self.check()
        self.assertEqual(self.tools.usage_calls, 1, "Unchanged state must not poll account again")
        self.clock.now = due
        self.check()  # Timestamp passed, but included usage is still exhausted.
        self.assertEqual(desktop.sent, [])
        self.make_available()
        reads_before = len(desktop.refreshes)
        self.check()
        self.assertEqual(len(desktop.sent), 1)
        self.assertEqual(desktop.sent[0][0:2], (THREAD, TURN))
        self.assertGreaterEqual(desktop.sent[0][3] - reads_before, 3)
        self.assertEqual(self.service.store.items[KEY].state, "resumed")
        self.clock.now += 3600
        self.check()
        self.assertEqual(len(desktop.sent), 1)

    def test_acknowledgement_alone_is_not_confirmation_of_a_new_turn(self):
        desktop = self.arm()
        desktop.confirm_new_turn = False
        self.make_available()
        self.check()
        self.assertEqual(self.service.store.items[KEY].state, "resuming")
        desktop.states[THREAD]["turns"].append({"id": "fixture-confirmed-turn", "status": "inProgress"})
        self.service._on_state(THREAD, copy.deepcopy(desktop.states[THREAD]))
        self.assertEqual(self.service.store.items[KEY].state, "resumed")

    def test_no_permission_no_valid_usage_and_account_errors_never_dispatch(self):
        desktop = self.arm()
        for data in (limits(allowed=False), limits(primary=None), limits(primary=100), {}):
            with self.subTest(limits=data):
                self.tools.limits = data
                self.service.wake()
                self.check()
                self.assertEqual(desktop.sent, [])
        self.tools.usage_error = RuntimeError("Fixture account unavailable")
        self.service.wake()
        with self.assertRaises(RuntimeError):
            self.check()
        self.assertEqual(desktop.sent, [])

    def test_manual_stop_and_new_instruction_before_preflight_never_dispatch(self):
        desktop = self.arm()
        self.make_available()
        desktop.states[THREAD]["turns"][-1]["status"] = "interrupted"
        self.check()
        self.assertEqual(desktop.sent, [])
        self.assertNotIn(self.service.store.items[KEY].state, ("waiting", "resuming"))
        desktop.states[THREAD] = failed_thread(turn_id="fixture-second-quota-turn")
        self.service._on_state(THREAD, copy.deepcopy(desktop.states[THREAD]))
        desktop.states[THREAD]["turns"].append({"id": "fixture-new-user-turn", "status": "inProgress"})
        self.clock.now += 60  # Ensure the quiet interval cannot explain no dispatch.
        self.service.wake()
        self.check()
        self.assertEqual(desktop.sent, [])

    def test_cancel_disable_close_or_disconnect_during_preflight_prevents_send(self):
        for action in ("cancel", "disable", "close", "disconnect"):
            with self.subTest(action=action):
                # Use a separate fixture journal/lock for each independent race.
                self.home = Path(self.temporary.name) / action
                self.service = self.new_service()
                desktop = self.arm()
                self.make_available()
                if action == "cancel":
                    desktop.before_resume = lambda: self.service.exclude(KEY)
                elif action == "disable":
                    desktop.before_resume = lambda: self.service.set_enabled(False)
                elif action == "close":
                    desktop.before_resume = self.service.close
                else:
                    desktop.before_resume = lambda: self.service._on_disconnect("Fixture disconnect")
                self.check()
                self.assertEqual(desktop.sent, [])

    def test_new_user_turn_between_service_and_adapter_preflights_prevents_send(self):
        desktop = self.arm()
        self.make_available()

        def new_user_turn():
            desktop.states[THREAD]["turns"].append({
                "id": "fixture-racing-user-turn", "status": "inProgress",
            })

        desktop.before_resume = new_user_turn
        self.check()
        self.assertEqual(desktop.sent, [])

    def test_missing_owner_missing_session_and_disconnected_desktop_never_dispatch(self):
        desktop = self.arm()
        self.make_available()
        desktop.owner_available = False
        with self.assertRaises(RuntimeError):
            self.check()
        self.assertEqual(desktop.sent, [])
        desktop.owner_available = True
        del desktop.states[THREAD]
        self.service.wake()
        with self.assertRaises(RuntimeError):
            self.check()
        self.assertEqual(desktop.sent, [])
        desktop.states[THREAD] = failed_thread()
        desktop.connected = False
        self.service.wake()
        with self.assertRaises(RuntimeError):
            self.check()
        self.assertEqual(desktop.sent, [])

    def test_lost_dispatch_reply_is_uncertain_and_never_retried_even_after_restart(self):
        desktop = self.arm()
        desktop.lost_reply = True
        self.make_available()
        self.check()
        self.assertEqual(len(desktop.sent), 1)
        self.assertEqual(self.service.store.items[KEY].state, "uncertain")
        self.clock.now += 3600
        self.service.wake()
        self.check()
        self.assertEqual(len(desktop.sent), 1)
        self.service.close()
        self.service._process_lock.close()
        restarted = self.new_service()
        restarted.set_enabled(True)
        restarted._connect()
        restarted._discover(self.clock())
        restarted._check_pending(self.clock())
        self.assertEqual(self.desktops[-1].sent, [])
        self.assertEqual(restarted.store.items[KEY].state, "uncertain")

    def test_two_instances_share_a_real_os_lock_and_only_one_can_dispatch(self):
        desktop = self.arm()
        self.make_available()
        second = self.new_service()
        # Test the production startup's lock decision without a background
        # thread racing this deterministic driver.
        with patch("codex_quota_resume.threading.Thread"):
            second.start()
        self.assertFalse(second.store.enabled)
        with self.assertRaises(RuntimeError):
            second.set_enabled(True)
        self.check()
        self.assertEqual(len(desktop.sent), 1)
        self.assertEqual(len(self.desktops), 1, "The rejected monitor must not connect to Desktop")

    def test_ui_snapshots_are_memory_only(self):
        desktop = self.arm()
        refreshes = len(desktop.refreshes)
        calls = (self.tools.list_calls, self.tools.usage_calls)
        for _ in range(50):
            snapshot = self.service.snapshot()
            self.assertEqual(snapshot["items"][0]["key"], KEY)
        self.assertEqual(len(desktop.refreshes), refreshes)
        self.assertEqual((self.tools.list_calls, self.tools.usage_calls), calls)

    def test_failed_enable_write_keeps_the_monitor_disabled(self):
        self.arm()
        self.service.set_enabled(False)
        with patch.object(self.service.store, "save", side_effect=OSError("Fixture disk full")):
            with self.assertRaises(OSError):
                self.service.set_enabled(True)
        self.assertFalse(self.service.snapshot()["enabled"])
        self.make_available()
        self.check()
        self.assertEqual(self.desktop.sent, [])


if __name__ == "__main__":
    unittest.main()
