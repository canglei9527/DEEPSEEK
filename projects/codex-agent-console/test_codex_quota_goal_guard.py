import tempfile
import unittest
from pathlib import Path
from codex_quota_resume import RecoveryPolicy, RecoveryStore, failure_identity


class GoalGuardTests(unittest.TestCase):
    def test_runtime_active_before_new_turn_keeps_dispatch_unconfirmed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecoveryStore(Path(directory))
            policy = RecoveryPolicy(store)
            turn = {"id": "old", "status": "failed",
                    "error": {"codexErrorInfo": "usageLimitExceeded"}}
            state = {"status": {"type": "idle"}, "turns": [turn]}
            policy.observe("thread", state, 100)
            policy.claim("thread/old", state, 200)
            policy.observe("thread", {"status": {"type": "active"}, "turns": [turn]}, 201)
            self.assertEqual(store.items["thread/old"].state, "resuming")
            policy.observe("thread", {"status": {"type": "idle"}, "turns": [
                {"id": "old", "status": "interrupted", "error": None}]}, 202)
            self.assertEqual(store.items["thread/old"].state, "superseded")

    def test_usage_limited_goal_is_resumable_not_blocked(self):
        """usageLimited 是额度耗尽的系统状态，不是用户的决定。

        桌面端把它当作可恢复（`active` 只差一次 thread/goal/set），发轮次的
        通道也没有按 Goal 状态拦截，所以普通续跑轮次是允许的。此前这里被当成
        “不要碰”，结果任务永远停在“暂不可恢复”。
        """
        state = {"status": {"type": "idle"}, "goal": {"status": "usageLimited"},
                 "turns": [{"id": "turn", "status": "failed",
                            "error": {"codexErrorInfo": "usageLimitExceeded"}}]}
        with tempfile.TemporaryDirectory() as directory:
            store = RecoveryStore(Path(directory))
            policy = RecoveryPolicy(store)
            self.assertEqual(failure_identity(state), "turn")
            self.assertTrue(policy.observe("thread", state, 100))
            self.assertEqual(store.items["thread/turn"].state, "waiting")
            self.assertIn("usageLimited", store.items["thread/turn"].detail)
            self.assertIsNotNone(policy.claim("thread/turn", state, 200))

    def test_user_paused_and_budget_limited_goals_stay_untouched(self):
        for goal_status in ("paused", "blocked", "budgetLimited", "complete"):
            with self.subTest(goal=goal_status):
                state = {"status": {"type": "idle"}, "goal": {"status": goal_status},
                         "turns": [{"id": "turn", "status": "failed",
                                    "error": {"codexErrorInfo": "usageLimitExceeded"}}]}
                with tempfile.TemporaryDirectory() as directory:
                    store = RecoveryStore(Path(directory))
                    policy = RecoveryPolicy(store)
                    self.assertIsNone(failure_identity(state))
                    policy.observe("thread", state, 100)
                    self.assertEqual(store.items, {})

    def test_optimistic_turn_does_not_confirm_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            store = RecoveryStore(Path(directory))
            policy = RecoveryPolicy(store)
            failed = {"status": {"type": "idle"}, "turns": [
                {"id": "old", "status": "failed", "error": {"codexErrorInfo": "usageLimitExceeded"}}]}
            policy.observe("thread", failed, 100)
            policy.claim("thread/old", failed, 200)
            policy.observe("thread", {"status": {"type": "active"},
                "turns": [{"id": None, "status": "inProgress"}]}, 201)
            self.assertEqual(store.items["thread/old"].state, "resuming")
