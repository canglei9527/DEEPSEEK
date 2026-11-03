# -*- coding: utf-8 -*-
"""Opt-in quota recovery. Only structured desktop failures may arm a retry.

No model is called while waiting. The desktop remains the owner of its threads;
this module never opens a second app-server or changes a thread's permissions.
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

STATE_NAME = "codex-agent-console-resume.json"
DISCOVERY_SECONDS = 60.0
RECHECK_SECONDS = 300.0
RESET_GRACE_SECONDS = 15.0
QUIET_SECONDS = 30.0
# 容量不足与额度耗尽不是一回事：它几分钟内可能自愈，不必等订阅窗口重置。
CAPACITY_RETRY_SECONDS = 180.0
CAPACITY_MAX_DELAY_SECONDS = 1800.0
CAPACITY_MAX_ATTEMPTS = 6
RESUME_PROMPT = (
    "继续之前因使用额度耗尽而中断的任务。先检查当前进度，再从未完成处继续；"
    "沿用原任务的范围、权限和验收要求，避免重复已完成的操作。"
)
CAPACITY_PROMPT = (
    "继续之前因模型容量不足而中断的任务。先检查当前进度，再从未完成处继续；"
    "沿用原任务的范围、权限和验收要求，避免重复已完成的操作。"
)
QUOTA_ERRORS = frozenset(("usageLimitExceeded", "usageLimitReached"))
# “Selected model is at capacity. Please try a different model.” 对应
# serverOverloaded；flexUnavailable 是同一类容量问题。
CAPACITY_ERRORS = frozenset(("serverOverloaded", "flexUnavailable"))
PENDING = frozenset(("waiting", "checking", "resuming"))
TERMINAL = frozenset(
    ("resumed", "cancelled", "superseded", "uncertain", "blocked", "exhausted")
)
FAILURE_KINDS = frozenset(("quota", "capacity"))


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) else None


def _error_info(error: Any) -> list[str]:
    """The structured names carried by a turn error, never its prose."""
    if not isinstance(error, dict):
        return []
    names: list[str] = []
    for field in ("codexErrorInfo", "codex_error_info", "code"):
        info = error.get(field)
        if isinstance(info, str):
            names.append(info)
        elif isinstance(info, dict):
            names.extend(str(key) for key in info)
    return names


def classify_failure(error: Any) -> str | None:
    """Return ``"quota"``, ``"capacity"`` or ``None`` for a turn error.

    Only structured protocol values are accepted: message text, HTTP 429 and
    token counts must never arm a retry.
    """
    names = _error_info(error)
    if any(name in QUOTA_ERRORS for name in names):
        return "quota"
    if any(name in CAPACITY_ERRORS for name in names):
        return "capacity"
    return None


def is_quota_error(error: Any) -> bool:
    """Accept protocol variants, never error prose, HTTP 429 or token counts."""
    return classify_failure(error) == "quota"


def is_capacity_error(error: Any) -> bool:
    return classify_failure(error) == "capacity"


def capacity_retry_delay(attempts: int) -> float:
    return min(CAPACITY_MAX_DELAY_SECONDS,
               CAPACITY_RETRY_SECONDS * (2 ** min(attempts, CAPACITY_MAX_ATTEMPTS)))


def latest_turn(state: dict[str, Any]) -> dict[str, Any] | None:
    turns = state.get("turns")
    if not isinstance(turns, list) or not turns:
        return None
    turn = turns[-1]
    return turn if isinstance(turn, dict) else None


def failure_identity(state: dict[str, Any]) -> str | None:
    """A manual interrupt, active turn, goal pause or normal finish disarms it.

    ``usageLimited`` is deliberately NOT a disarm state: it is the system's own
    response to exhausted quota, not a user decision. The desktop treats it as
    resumable (`active` is one `thread/goal/set` away) and its turn channel has
    no goal-status guard, so a normal continuation turn is allowed. A goal the
    user paused, the runtime blocked, a budget cap or a finished goal must stay
    untouched.

    Capacity failures ("Selected model is at capacity") are also resumable:
    the model provider, not the user, refused the turn.
    """
    status = state.get("status")
    if not isinstance(status, dict) or status.get("type") not in ("idle", "systemError"):
        return None
    if state.get("archived") is True:
        return None
    goal = state.get("goal")
    if isinstance(goal, dict) and goal.get("status") in (
        "paused", "blocked", "budgetLimited", "budget_limited", "complete", "completed",
    ):
        return None
    turn = latest_turn(state)
    if turn and turn.get("status") == "failed" and classify_failure(turn.get("error")):
        turn_id = turn.get("id")
        if isinstance(turn_id, str) and turn_id:
            return turn_id
    return None


def quota_gate(limits: dict[str, Any], now: float, model: str = "") -> tuple[bool, float, str]:
    """Reset times schedule reads; only a fresh explicit permission opens the gate.

    Even when paid credits allow requests, exhausted included windows keep the
    helper waiting. No reset credit or paid reset is ever redeemed here.
    """
    buckets = limits.get("rateLimitsByLimitId")
    if isinstance(buckets, dict) and buckets:
        selected = [b for b in buckets.values() if isinstance(b, dict) and
                    (not b.get("normalModelSlug") or b.get("normalModelSlug") == model)]
    else:
        bucket = limits.get("rateLimits")
        selected = [bucket] if isinstance(bucket, dict) else []
    exhausted: list[float] = []
    unknown_reset = False
    percentages_known = bool(selected)
    for bucket in selected:
        if bucket.get("spendControlReached") is True:
            return False, now + RECHECK_SECONDS, "账户支出限制，等待账户状态更新"
        seen = False
        for key in ("primary", "secondary"):
            window = bucket.get(key)
            if not isinstance(window, dict):
                continue
            used = _finite(window.get("usedPercent"))
            if used is None or used < 0:
                percentages_known = False
                continue
            seen = True
            if used >= 100:
                reset = _finite(window.get("resetsAt"))
                if reset is not None and reset > now:
                    exhausted.append(reset + RESET_GRACE_SECONDS)
                else:
                    unknown_reset = True
        if not seen:
            percentages_known = False
    if exhausted or unknown_reset:
        due = max(exhausted) if exhausted else now + RECHECK_SECONDS
        return False, due, "等待订阅额度恢复"
    if limits.get("ordinaryUsageAllowed") is True and percentages_known:
        return True, now, "额度已恢复，准备复核会话"
    return False, now + RECHECK_SECONDS, "服务端尚未确认额度可用"


@dataclass
class Recovery:
    key: str
    thread_id: str
    turn_id: str
    title: str
    model: str = ""
    state: str = "waiting"
    reset_at: float | None = None
    detail: str = "已识别明确的额度中断，等待检查额度"
    created_at: float = 0.0
    updated_at: float = 0.0
    kind: str = "quota"
    attempts: int = 0
    continuation_turn_id: str | None = None


class RecoveryStore:
    """Local journal: retain dedup identities, claim before dispatch, atomic replace."""

    def __init__(self, home: Path):
        self.path = home / STATE_NAME
        self.enabled = False
        self.items: dict[str, Recovery] = {}
        self.error = ""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1
                    or not isinstance(data.get("items"), list)
                    or not isinstance(data.get("enabled"), bool)):
                raise ValueError("unsupported recovery state")
            self.enabled = data.get("enabled") is True
            for row in data["items"]:
                item = Recovery(**row)
                if item.key != f"{item.thread_id}/{item.turn_id}":
                    raise ValueError("invalid recovery identity")
                if item.key in self.items:
                    raise ValueError("duplicate recovery identity")
                if (not isinstance(item.thread_id, str) or not item.thread_id
                        or not isinstance(item.turn_id, str) or not item.turn_id
                        or item.state not in PENDING | TERMINAL
                        or _finite(item.created_at) is None or _finite(item.updated_at) is None
                        or (item.reset_at is not None and _finite(item.reset_at) is None)
                        or item.kind not in FAILURE_KINDS
                        or not isinstance(item.attempts, int) or isinstance(item.attempts, bool)
                        or item.attempts < 0
                        or (item.continuation_turn_id is not None and
                            (not isinstance(item.continuation_turn_id, str) or not item.continuation_turn_id))):
                    raise ValueError("invalid recovery state")
                # A lost reply may already have started work. Never replay it.
                if item.state in ("resuming", "checking"):
                    item.state = "uncertain"
                    item.detail = "上次恢复未确认；为防重复执行，需在 Codex 中检查"
                elif (item.kind == "capacity" and item.state == "waiting"
                      and item.attempts > 0 and "continuation_turn_id" not in row):
                    item.state = "uncertain"
                    item.detail = "旧版容量重试可能已发送，本次不重复发送；请在 Codex 中检查"
                # 旧版本把 usageLimited 的 Goal 记成 blocked 并放弃。桌面端其实
                # 允许续跑轮次，所以这些记录重新进入等待队列。
                elif item.state == "blocked":
                    item.state = "waiting"
                    item.detail = "已识别明确的额度中断，等待检查额度"
                self.items[item.key] = item
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError):
            self.enabled = False
            self.items = {}
            self.error = "恢复记录无法读取，自动恢复已关闭；请检查记录文件"

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        recent = sorted(self.items.values(), key=lambda item: item.updated_at, reverse=True)
        # A tombstone must outlive the failed turn, including across restarts.
        # Only the UI is bounded; never evict cancellation/dispatch identities.
        data = {"version": 1, "enabled": self.enabled, "items": [asdict(item) for item in recent]}
        temporary = self.path.with_suffix(f".{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


class RecoveryPolicy:
    """Small deterministic state machine used by the background service."""

    def __init__(self, store: RecoveryStore):
        self.store = store

    def observe(self, thread_id: str, state: dict[str, Any], now: float) -> bool:
        failed_id = failure_identity(state)
        turn = latest_turn(state)
        changed = False
        for item in self.store.items.values():
            if item.thread_id != thread_id or item.state not in PENDING:
                continue
            if (item.state == "resuming" and turn and not turn.get("id")
                    and turn.get("status") == "inProgress"):
                continue  # Desktop's optimistic placeholder is not an acknowledgement.
            if (item.state == "resuming" and turn and turn.get("id") == item.turn_id
                    and turn.get("status") == "failed" and classify_failure(turn.get("error"))
                    and state.get("status", {}).get("type") == "active"):
                # Runtime activity can arrive before the new turn entity. It
                # neither confirms dispatch nor proves a manual interruption.
                continue
            if (item.state == "resuming" and turn and isinstance(turn.get("id"), str)
                    and turn["id"] and turn["id"] != item.turn_id):
                item.state, item.detail = "resumed", "已在原会话收到新的执行轮次"
                item.continuation_turn_id = turn["id"]
            elif failed_id != item.turn_id:
                item.state, item.detail = "superseded", "会话已停止、继续或发生变化，取消本次恢复"
            else:
                continue
            item.updated_at = now
            changed = True
        if failed_id:
            key = f"{thread_id}/{failed_id}"
            if key not in self.store.items:
                failure_kind = classify_failure(
                    (latest_turn(state) or {}).get("error")
                ) or "quota"
                if failure_kind == "capacity":
                    detail = "模型容量不足（Selected model is at capacity）；稍后自动重试"
                else:
                    goal = state.get("goal")
                    goal_limited = (
                        isinstance(goal, dict) and goal.get("status") == "usageLimited"
                    )
                    detail = (
                        "额度中断（Goal 处于 usageLimited）；额度恢复后发送续跑轮次，"
                        "目标由续跑轮次重新激活"
                        if goal_limited else "已识别明确的额度中断，等待检查额度"
                    )
                attempts = self._carried_attempts(thread_id, failure_kind, failed_id)
                self.store.items[key] = Recovery(
                    key=key, thread_id=thread_id, turn_id=failed_id,
                    title=str(state.get("name") or state.get("title") or thread_id),
                    model=str(state.get("model") or ""),
                    detail=detail,
                    kind=failure_kind,
                    # 续跑失败会生成新的 turn id，从而成为新记录；容量重试的
                    # 次数必须跟着线程走，否则退避与上限都会被绕过。
                    attempts=attempts,
                    reset_at=(now + capacity_retry_delay(attempts)
                              if failure_kind == "capacity" else None),
                    created_at=now, updated_at=now,
                )
                changed = True
        return changed

    def _carried_attempts(self, thread_id: str, kind: str, failed_id: str) -> int:
        """Carry only the count of the exact automatic continuation that failed.

        A later independent user request must not inherit a lifetime counter.
        """
        previous = [
            item for item in self.store.items.values()
            if item.thread_id == thread_id and item.kind == kind
            and item.continuation_turn_id == failed_id
        ]
        if not previous:
            return 0
        return max(item.attempts for item in previous)

    def exclude(self, key: str, now: float) -> bool:
        item = self.store.items.get(key)
        if item is None or item.state not in PENDING:
            return False
        item.state, item.detail, item.updated_at = "cancelled", "已取消本次自动恢复", now
        return True

    def claim(self, key: str, live: dict[str, Any], now: float) -> Recovery | None:
        item = self.store.items[key]
        if item.state != "waiting" or failure_identity(live) != item.turn_id:
            self.observe(item.thread_id, live, now)
            return None
        item.state, item.detail, item.updated_at = "resuming", "正在原会话发送续跑请求", now
        if item.kind == "capacity":
            # Persist the attempt before dispatch: a new failure can arrive on
            # the reader thread before resume() returns its acknowledgement.
            item.attempts += 1
        # Persistence MUST succeed before any request can start a turn.
        try:
            self.store.save()
        except Exception:
            item.state, item.detail = "uncertain", "恢复记录未能保存，本次不会发送续跑"
            raise
        return item


class _MonitorLock:
    """An OS lock, rather than a stale PID/file, prevents double dispatch."""

    def __init__(self, home: Path):
        self.path = home / "codex-agent-console-resume.lock"
        self.stream: Any = None

    def acquire(self) -> bool:
        if self.stream is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            stream.close()
            return False
        self.stream = stream
        return True

    def close(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            stream.close()


class QuotaResumeService:
    """One sleeping worker; desktop snapshots wake it when a task changes.

    Discovery checks only app metadata once per minute. While a quota reset is
    hours away, no model or account request is made until that reset is due.
    """

    def __init__(self, home: Path, *, desktop_factory: Any = None, tools_factory: Any = None,
                 clock: Any = time.time):
        self.home = home
        self.store = RecoveryStore(home)
        self.policy = RecoveryPolicy(self.store)
        self._clock = clock
        self._desktop_factory = desktop_factory
        self._tools_factory = tools_factory
        self._guard = threading.RLock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._process_lock = _MonitorLock(home)
        self._worker: threading.Thread | None = None
        self._desktop: Any = None
        self._tools: Any = None
        self._states: dict[str, dict[str, Any]] = {}
        self._followed: set[str] = set()
        self._connection = "未连接"
        self._status = self.store.error or "自动恢复已关闭"
        self._next_check_at: float | None = None
        self._generation = 0
        self._next_discovery = 0.0
        self._account_due = 0.0
        self._needs_reconnect = False
        self._unowned_due: dict[str, float] = {}
        self._discovery_updates: dict[str, Any] = {}
        self._release: set[str] = set()

    def start(self) -> None:
        if self._worker is not None:
            return
        with self._guard:
            if self.store.enabled and not self._process_lock.acquire():
                self.store.enabled = False
                self._status = "另一控制台已在监控；此窗口不重复恢复"
        self._worker = threading.Thread(target=self._run, name="quota-resume", daemon=True)
        self._worker.start()

    def snapshot(self) -> dict[str, Any]:
        with self._guard:
            return {
                "enabled": self.store.enabled, "status": self._status,
                "connection": self._connection, "next_check_at": self._next_check_at,
                "items": [asdict(item) for item in sorted(
                    self.store.items.values(), key=lambda item: item.created_at, reverse=True)[:200]],
            }

    def set_enabled(self, enabled: bool) -> None:
        with self._guard:
            if self._stop.is_set():
                raise RuntimeError("控制台正在退出")
            if self.store.error:
                raise RuntimeError(self.store.error)
            if not self._process_lock.acquire():
                raise RuntimeError("另一控制台拥有恢复队列，请先关闭另一控制台窗口")
            # Re-read after taking ownership: another instance may have changed it.
            if self._desktop is None:
                fresh = RecoveryStore(self.home)
                if fresh.error:
                    raise RuntimeError(fresh.error)
                self.store, self.policy = fresh, RecoveryPolicy(fresh)
            self.store.enabled = bool(enabled)
            self._generation += 1
            try:
                self.store.save()
            except OSError:
                self.store.enabled = False
                self._status = "设置保存失败，自动恢复保持关闭"
                self._wake.set()
                raise
            self._status = "准备监听桌面会话" if enabled else "自动恢复已关闭"
            self._next_discovery = 0
            self._account_due = 0
        self._wake.set()

    def exclude(self, key: str) -> None:
        with self._guard:
            if not self._process_lock.acquire():
                raise RuntimeError("恢复队列由另一控制台管理")
            if self.policy.exclude(key, self._clock()):
                self._generation += 1
                self.store.save()
        self._wake.set()

    def wake(self) -> None:
        with self._guard:
            self._next_discovery = 0
            self._account_due = 0
        self._wake.set()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._guard:
            self._generation += 1
            desktop = self._desktop
        if desktop is not None:
            desktop.close()
        if self._worker is not None:
            self._worker.join(timeout=2)
        else:
            self._process_lock.close()

    def _on_state(self, thread_id: str, state: dict[str, Any]) -> None:
        with self._guard:
            if not self.store.enabled or self._stop.is_set():
                return
            if self._states.get(thread_id) == state:
                return
            self._states[thread_id] = copy.deepcopy(state)
            if self.policy.observe(thread_id, state, self._clock()):
                try:
                    self.store.save()
                except OSError as exc:
                    self.store.enabled = False
                    self._generation += 1
                    self._status = f"恢复记录写入失败，自动恢复已停止：{exc}"
            if (state.get("status", {}).get("type") != "active"
                    and failure_identity(state) is None
                    and not any(item.thread_id == thread_id and item.state in PENDING
                                for item in self.store.items.values())):
                self._release.add(thread_id)
        self._wake.set()

    def _on_disconnect(self, detail: str) -> None:
        with self._guard:
            self._connection = str(detail)
            self._needs_reconnect = True
            self._generation += 1
            self._states.clear()
            self._followed.clear()
            self._next_discovery = 0
        self._wake.set()

    def _connect(self) -> None:
        if self._needs_reconnect:
            client, self._desktop = self._desktop, None
            if client is not None:
                client.close()
            self._needs_reconnect = False
        if self._desktop is not None:
            return
        desktop_factory = self._desktop_factory
        tools_factory = self._tools_factory
        if desktop_factory is None:
            from codex_desktop_ipc import DesktopClient
            desktop_factory = DesktopClient
        if tools_factory is None:
            from codex_resume_sources import ResumeSources
            tools_factory = ResumeSources
        client = desktop_factory(on_state=self._on_state, on_disconnect=self._on_disconnect)
        client.connect()
        self._desktop = client
        self._tools = tools_factory(codex_home=self.home)
        self._connection = "已连接 Codex 桌面端"

    def _discover(self, now: float) -> None:
        listing = self._tools.list_threads()
        rows = list(listing.get("pinnedThreads") or []) + list(listing.get("threads") or [])
        for row in rows:
            if not isinstance(row, dict):
                continue
            identity = str(row.get("id") or row.get("threadId") or "")
            updated = row.get("updatedAt")
            if identity and self._discovery_updates.get(identity) != updated:
                self._unowned_due.pop(identity, None)
                self._discovery_updates[identity] = updated
        ordered = [item.thread_id for item in self.store.items.values() if item.state in PENDING]
        ordered.extend(str(row.get("id") or row.get("threadId") or "") for row in rows
                       if isinstance(row, dict) and row.get("kind", "codex") == "codex"
                       and row.get("hostId", "local") == "local")
        attempted = 0
        for thread_id in dict.fromkeys(ordered):
            if (not thread_id or thread_id in self._followed
                    or self._unowned_due.get(thread_id, 0) > now):
                continue
            if self._stop.is_set() or not self.store.enabled:
                return
            try:
                self._desktop.follow(thread_id)
                self._followed.add(thread_id)
            except Exception:
                # Unloaded/closed/remote threads are never resumed through a new server.
                self._unowned_due[thread_id] = now + RECHECK_SECONDS
            attempted += 1
            if attempted >= 8:
                break
        self._next_discovery = self._clock() + DISCOVERY_SECONDS

    def _release_idle(self) -> None:
        with self._guard:
            release, self._release = self._release, set()
        for thread_id in release:
            state = self._states.get(thread_id) or {}
            if state.get("status", {}).get("type") == "active":
                continue
            unfollow = getattr(self._desktop, "unfollow", None)
            if unfollow is not None:
                unfollow(thread_id)
            self._followed.discard(thread_id)
            self._states.pop(thread_id, None)
            self._unowned_due[thread_id] = self._clock() + RECHECK_SECONDS

    def _check_pending(self, now: float) -> None:
        with self._guard:
            waiting = [item for item in self.store.items.values() if item.state == "waiting"]
        if not waiting:
            return

        # 容量不足不需要查额度：它和订阅窗口无关，按退避重试即可。
        capacity = [item for item in waiting if item.kind == "capacity"]
        for item in capacity:
            with self._guard:
                if item.state != "waiting" or not self.store.enabled or self._stop.is_set():
                    continue
                due = max(
                    (item.reset_at or 0.0),
                    item.created_at + capacity_retry_delay(item.attempts),
                )
                if item.attempts >= CAPACITY_MAX_ATTEMPTS:
                    item.state = "exhausted"
                    item.detail = (
                        f"容量不足已连续重试 {item.attempts} 次仍未恢复；"
                        "请在 Codex 中换一个模型或稍后手动继续"
                    )
                    item.updated_at = now
                    self.store.save()
                    continue
                detail = ("模型容量不足（Selected model is at capacity）；"
                          f"等待第 {item.attempts + 1} 次重试（最多 {CAPACITY_MAX_ATTEMPTS} 次）")
                if (item.reset_at, item.detail) != (due, detail):
                    item.reset_at = due
                    item.detail = detail
                    item.updated_at = now
                    self.store.save()
            if now >= due:
                self._resume_one(item)
                self._account_due = self._clock() + 15.0
                return

        quota = [item for item in waiting if item.kind != "capacity"]
        if not quota or now < self._account_due:
            return
        limits = self._tools.usage_limits(thread_id=quota[0].thread_id)
        due_times = []
        for item in quota:
            allowed, due, detail = quota_gate(limits, now, item.model)
            due = max(due, item.created_at + QUIET_SECONDS)
            with self._guard:
                if item.state != "waiting" or not self.store.enabled or self._stop.is_set():
                    continue
                if (item.reset_at, item.detail) != (due, detail):
                    item.reset_at, item.detail, item.updated_at = due, detail, now
                    self.store.save()
            due_times.append(due)
            if allowed and now >= due:
                self._resume_one(item)
                # Fresh account state for every dispatch; allow its usage to settle.
                self._account_due = self._clock() + 15.0
                return
        self._account_due = min(due_times) if due_times else now + RECHECK_SECONDS

    def _resume_one(self, item: Recovery) -> None:
        with self._guard:
            generation = self._generation
        live = self._desktop.refresh(item.thread_id)
        with self._guard:
            if generation != self._generation or not self.store.enabled or self._stop.is_set():
                return
            if self.policy.claim(item.key, live, self._clock()) is None:
                self.store.save()
                return

        def still_allowed() -> bool:
            with self._guard:
                return (generation == self._generation and self.store.enabled
                        and not self._stop.is_set() and item.state == "resuming")

        prompt = CAPACITY_PROMPT if item.kind == "capacity" else RESUME_PROMPT
        try:
            result = self._desktop.resume(item.thread_id, item.turn_id, prompt,
                                          should_continue=still_allowed)
            with self._guard:
                if item.state == "resuming":
                    if result is False:
                        item.state, item.detail = "superseded", "会话发生变化，未发送续跑"
                    else:
                        if item.kind != "capacity":
                            item.attempts += 1
                        # Acceptance alone does not confirm a new turn. Never
                        # put this failed-turn identity back into waiting.
                        item.detail = "续跑请求已发送，等待桌面确认"
                item.updated_at = self._clock()
                self.store.save()
            # Acknowledgement is weaker than a real new turn. Verify separately.
            if result is not False:
                state = self._desktop.refresh(item.thread_id)
                self._on_state(item.thread_id, state)
        except Exception as exc:
            with self._guard:
                if item.state == "resuming":
                    item.state, item.detail = "uncertain", f"恢复结果未确认，不重复发送：{exc}"
                    item.updated_at = self._clock()
                    self.store.save()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.clear()
                if not self.store.enabled:
                    if self._desktop is not None:
                        client, self._desktop = self._desktop, None
                        client.close()
                        self._states.clear()
                        self._followed.clear()
                    self._next_check_at = None
                    self._wake.wait()
                    continue
                now = self._clock()
                try:
                    self._connect()
                    if now >= self._next_discovery:
                        self._discover(now)
                    self._release_idle()
                    self._check_pending(now)
                    with self._guard:
                        for item in self.store.items.values():
                            if item.state == "resuming" and now - item.updated_at > 45:
                                item.state = "uncertain"
                                item.detail = "未收到新轮次确认，请在 Codex 中检查；不会重复发送"
                                self.store.save()
                        pending = [item for item in self.store.items.values() if item.state in PENDING]
                        self._status = f"监听中 · {len(pending)} 个任务待恢复" if pending else "监听中 · 没有额度或容量中断任务"
                    deadline = self._next_discovery
                    for item in pending:
                        if item.state == "resuming":
                            deadline = min(deadline, item.updated_at + 46.0)
                        elif item.state == "waiting":
                            if item.kind == "capacity":
                                # 容量重试有自己的时间表，不受额度查询节流影响。
                                deadline = min(deadline, item.reset_at or now)
                            else:
                                deadline = min(deadline, self._account_due or now)
                    self._next_check_at = deadline
                    self._wake.wait(max(0.5, deadline - self._clock()))
                except Exception as exc:
                    with self._guard:
                        self._status = f"等待连接或重试：{exc}"
                        self._connection = "未就绪，不恢复任务"
                        client, self._desktop = self._desktop, None
                        self._states.clear()
                        self._followed.clear()
                    if client is not None:
                        client.close()
                    self._next_check_at = self._clock() + DISCOVERY_SECONDS
                    self._wake.wait(DISCOVERY_SECONDS)
        finally:
            if self._desktop is not None:
                self._desktop.close()
            self._process_lock.close()
