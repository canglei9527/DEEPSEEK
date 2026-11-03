# -*- coding: utf-8 -*-
"""Bounded, read-only discovery and quota reads for automatic task recovery.

Discovery reads SQLite metadata. Quota reads use a short-lived bundled Codex
process and only initialize/account RPCs; this module never loads a thread or
reads credential files, and has no background polling loop.
"""
from __future__ import annotations

from contextlib import closing
import json
import math
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import threading
import time
from typing import Any


class ResumeSourcesError(RuntimeError):
    """Local discovery or a read-only Codex account query failed."""


_MAX_LINE_BYTES = 8 * 1024 * 1024


def _remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0:
        raise ResumeSourcesError("读取 Codex 额度超时")
    return value


def _find_executable() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    directory = base / "OpenAI" / "Codex" / "bin"
    try:
        candidates = [path for path in directory.glob("*/codex.exe") if path.is_file()]
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime_ns)
    except OSError as exc:
        raise ResumeSourcesError("无法查找 Codex 桌面内置程序") from exc
    raise ResumeSourcesError("未找到 Codex 桌面内置 codex.exe")


def _read_messages(stream: Any, messages: queue.Queue) -> None:
    try:
        while True:
            line = stream.readline(_MAX_LINE_BYTES + 1)
            if not line:
                messages.put_nowait(ResumeSourcesError("Codex 额度读取进程已退出"))
                return
            if len(line) > _MAX_LINE_BYTES:
                raise ResumeSourcesError("Codex 额度响应过大")
            try:
                value = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ResumeSourcesError("Codex 额度进程返回无效 JSON") from exc
            if not isinstance(value, dict):
                raise ResumeSourcesError("Codex 额度进程返回无效 RPC 消息")
            messages.put_nowait(value)
    except queue.Full:
        # A notification flood must not grow memory or block the reader forever.
        try:
            messages.get_nowait()
            messages.put_nowait(ResumeSourcesError("Codex 额度进程通知过多"))
        except (queue.Empty, queue.Full):
            pass
    except (OSError, ValueError, ResumeSourcesError) as exc:
        try:
            messages.put_nowait(exc if isinstance(exc, ResumeSourcesError)
                                else ResumeSourcesError("Codex 额度进程读取失败"))
        except queue.Full:
            pass


def _write_message(process: Any, message: dict[str, Any], deadline: float) -> None:
    _remaining(deadline)
    # These fixed account-only requests total under 1 KiB, below pipe capacity.
    data = (json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n").encode("utf-8")
    if process.stdin is None:
        raise ResumeSourcesError("Codex 额度读取输入已关闭")
    try:
        written = process.stdin.write(data)
        if written != len(data):
            raise ResumeSourcesError("Codex 额度读取请求写入不完整")
        process.stdin.flush()
    except (OSError, ValueError) as exc:
        raise ResumeSourcesError("Codex 额度读取请求写入失败") from exc


def _wait_result(messages: queue.Queue, request_id: int, deadline: float) -> dict[str, Any]:
    for _ in range(64):
        try:
            value = messages.get(timeout=_remaining(deadline))
        except queue.Empty as exc:
            raise ResumeSourcesError("读取 Codex 额度超时") from exc
        if isinstance(value, Exception):
            raise ResumeSourcesError(str(value)) from value
        if "id" not in value and isinstance(value.get("method"), str):
            continue
        if value.get("id") != request_id:
            raise ResumeSourcesError("Codex 额度响应 ID 不匹配")
        if "error" in value:
            # Do not copy server errors, credentials or response bodies into logs.
            raise ResumeSourcesError("Codex 额度读取 RPC 失败")
        if not isinstance(value.get("result"), dict):
            raise ResumeSourcesError("Codex 额度响应缺少对象结果")
        return value["result"]
    raise ResumeSourcesError("Codex 额度进程通知过多")


def _close_process(process: Any, reader: threading.Thread | None, deadline: float) -> None:
    # Kill the dedicated account-only process before closing stdout; closing a
    # buffered stream while another thread is reading can otherwise block.
    try:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=max(0.001, min(0.2, deadline - time.monotonic())))
    except (OSError, subprocess.TimeoutExpired):
        pass
    if reader is not None:
        reader.join(timeout=max(0, min(0.1, deadline - time.monotonic())))
    for stream in (process.stdin, process.stdout):
        if stream is None or (stream is process.stdout and reader is not None and reader.is_alive()):
            continue
        try:
            stream.close()
        except (OSError, ValueError):
            pass


class ResumeSources:
    """Only query on demand. No session resume/start or desktop tool dispatch."""

    def __init__(self, codex_home: str | Path | None = None, timeout: float = 15.0,
                 executable: str | Path | None = None):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ResumeSourcesError("Codex 额度读取超时必须是正数")
        self.codex_home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        self.timeout = float(timeout)
        self.executable = Path(executable) if executable is not None else None

    def list_threads(self) -> dict[str, Any]:
        database = self.codex_home / "state_5.sqlite"
        try:
            with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True,
                                         timeout=min(self.timeout, 1.0))) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
                required = {"id", "source", "updated_at", "archived", "rollout_path"}
                if not required.issubset(columns):
                    raise ResumeSourcesError("Codex 会话索引格式不受支持")
                title = "title" if "title" in columns else "''"
                cwd = "cwd" if "cwd" in columns else "''"
                rows = connection.execute(
                    f"SELECT id, rollout_path, updated_at, {title}, {cwd} FROM threads "
                    "WHERE archived = 0 AND source IN ('vscode', 'appServer') "
                    "ORDER BY updated_at DESC LIMIT 50"
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise ResumeSourcesError("无法只读获取 Codex 桌面会话索引") from exc
        return {"threads": [
            {"id": row[0], "threadId": row[0], "kind": "codex", "hostId": "local",
             "status": {"type": "unknown"}, "rolloutPath": row[1],
             "updatedAt": row[2], "title": row[3], "cwd": row[4]}
            for row in rows]}

    def usage_limits(self, thread_id: str | None = None) -> dict[str, Any]:
        # thread_id is accepted for callers' convenience; never sent to the
        # dedicated process, which must not load or mutate any desktop thread.
        deadline = time.monotonic() + self.timeout
        executable = self.executable or _find_executable()
        _remaining(deadline)
        environment = dict(os.environ)
        environment["CODEX_HOME"] = str(self.codex_home)
        process = None
        reader = None
        try:
            process = subprocess.Popen(
                [str(executable), "app-server"], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
                env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            messages: queue.Queue = queue.Queue(maxsize=64)
            reader = threading.Thread(target=_read_messages, args=(process.stdout, messages),
                                      name="codex-quota-account-read", daemon=True)
            reader.start()
            _write_message(process, {"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "codex-agent-console-quota", "version": "1"},
                "capabilities": {"experimentalApi": True}}}, deadline)
            _wait_result(messages, 1, deadline)
            _write_message(process, {"method": "initialized", "params": {}}, deadline)
            _write_message(process, {"id": 2, "method": "account/rateLimits/read", "params": {}}, deadline)
            result = _wait_result(messages, 2, deadline)
            if not isinstance(result.get("rateLimits"), dict) and not isinstance(result.get("rateLimitsByLimitId"), dict):
                raise ResumeSourcesError("Codex 额度响应缺少额度快照")
            return result
        except (OSError, ValueError) as exc:
            raise ResumeSourcesError("无法启动或读取 Codex 额度进程") from exc
        finally:
            if process is not None:
                _close_process(process, reader, deadline)
