# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import queue
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from codex_gateway import (
    CodexGateway,
    GatewayConfig,
    GatewayError,
    dsh_mcp_insert,
    dsh_provider_snippet,
    strip_dsh_entries,
    strip_dsh_mcp_insert,
)
from codex_advisor import (
    ADVISOR_EFFORTS,
    AdvisorConfig,
    AdvisorError,
    AdvisorStore,
    MCP_SERVER_NAME,
    encrypt_key,
    find_dsh_install,
    has_advisor_policy,
    mcp_launch_command,
    merge_advisor_policy,
    merge_mcp_config,
)
from codex_quota_resume import QuotaResumeService
from codex_resume_ui import ResumeWindow


APP_NAME = "Codex Agent Console"
APP_VERSION = "2.2.2"
AUTO_REFRESH_MS = 2000
BACKGROUND_REFRESH_MS = 30000
UI_PUMP_MS = 60
DIAGNOSTIC_CLEANUP_GRACE_SECONDS = 5.0
DIAGNOSTIC_PROMPT = (
    "This is an API connectivity diagnostic. Do not use tools. "
    "Reply with exactly API_OK and nothing else."
)
CUSTOM_API_CONFIG_NAME = "codex-agent-console-apis.json"
CUSTOM_API_PROMPT = "Reply with exactly API_OK and nothing else."
CUSTOM_API_CACHE_PROMPT = (
    "Cache benchmark reference text. Reuse this exact request prefix for a "
    "deterministic prompt-cache measurement. "
    * 160
    + "\n\nReply with exactly API_OK and nothing else."
)
CUSTOM_API_CHAT_COMPLETIONS_SUFFIX = "/chat/completions"
CUSTOM_API_ANTHROPIC_MESSAGES_SUFFIX = "/messages"
CUSTOM_API_MODELS_SUFFIX = "/models"
ANTHROPIC_API_VERSION = "2023-06-01"
CUSTOM_API_AUTH_MODES = ("bearer", "x-api-key", "none")
CUSTOM_API_TYPES = (
    "chat_completions",
    "responses",
    "completions",
    "anthropic_messages",
)
CUSTOM_API_TYPE_LABELS = {
    "chat_completions": "Chat Completions",
    "responses": "Responses API",
    "completions": "Legacy Completions",
    "anthropic_messages": "Claude Messages (Anthropic)",
}
CUSTOM_API_TYPE_SUFFIXES = {
    "chat_completions": CUSTOM_API_CHAT_COMPLETIONS_SUFFIX,
    "responses": "/responses",
    "completions": "/completions",
    "anthropic_messages": CUSTOM_API_ANTHROPIC_MESSAGES_SUFFIX,
}
MODELS = (
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.2",
)
EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
MODEL_EFFORTS = {
    "gpt-5.6-sol": ("low", "medium", "high", "xhigh", "max", "ultra"),
    "gpt-5.6-terra": ("low", "medium", "high", "xhigh", "max", "ultra"),
    "gpt-5.6-luna": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.5": ("low", "medium", "high", "xhigh"),
    "gpt-5.2": ("low", "medium", "high", "xhigh"),
}
# Default split: the high-capability model plans, the cheaper model executes.
DEFAULT_MAIN_MODEL = "gpt-5.5"
DEFAULT_MAIN_EFFORT = "medium"
DEFAULT_SUBAGENT_MODEL = "gpt-5.6-sol"
DEFAULT_SUBAGENT_EFFORT = "high"


def load_model_catalog(home: Path) -> dict[str, tuple[str, ...]]:
    """Read the models this Codex installation actually offers.

    Codex keeps its model list in ``~/.codex/models_cache.json``.  The GUI used
    to show a hard-coded list, which hid newer models and disagreed with the
    config values it had just loaded.
    """
    catalog: dict[str, tuple[str, ...]] = {}
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return catalog
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return catalog
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        if entry.get("visibility") == "hide":
            continue
        levels: list[str] = []
        raw_levels = entry.get("supported_reasoning_levels")
        if isinstance(raw_levels, list):
            for level in raw_levels:
                effort = level.get("effort") if isinstance(level, dict) else None
                if isinstance(effort, str) and effort and effort not in levels:
                    levels.append(effort)
        catalog[slug.strip()] = tuple(levels)
    return catalog
DUAL_MODE_POLICY_START = "[Codex Agent Console: dual-model orchestration]"
DUAL_MODE_POLICY_END = "[/Codex Agent Console: dual-model orchestration]"
MANAGED_EXECUTOR_AGENT_NAME = "codex_agent_console_executor"
MANAGED_EXECUTOR_AGENT_FILE = f"{MANAGED_EXECUTOR_AGENT_NAME}.toml"
MANAGED_EXECUTOR_AGENT_MARKER = "# Managed by Codex Agent Console"
GLOBAL_AGENTS_FILENAME = "AGENTS.md"
GLOBAL_AGENTS_OVERRIDE_FILENAME = "AGENTS.override.md"
_DUAL_MODE_POLICY_RE = re.compile(
    rf"{re.escape(DUAL_MODE_POLICY_START)}.*?{re.escape(DUAL_MODE_POLICY_END)}",
    re.DOTALL,
)


def _policy_value(value: str) -> str:
    return value.replace("`", "'").replace("\r", " ").replace("\n", " ").strip()


def build_dual_mode_policy(subagent_model: str, subagent_effort: str) -> str:
    model = _policy_value(subagent_model)
    effort = _policy_value(subagent_effort)
    return f"""{DUAL_MODE_POLICY_START}
`{MANAGED_EXECUTOR_AGENT_NAME}` is the high-capability planner and advisor; the primary model is the implementer that ships the work. For work requests, choose the route immediately and act in the same turn. Never replace the requested work with a plan, speculation, or a list of instructions for the user.

Planning round: for complex, ambiguous, multi-step, high-risk, or unfamiliar work, first send a planning request to exactly one `{MANAGED_EXECUTOR_AGENT_NAME}` with the spawn settings below. Ask for a compact plan: goal, ordered steps, files or modules to touch, constraints, risks, and concrete acceptance checks. For a clear, bounded, low-risk task you already understand, skip the planning round and implement it directly.

Execution: the primary model does the hands-on work itself with the available tools — inspect, edit, run, and verify every acceptance check. Do not delegate the implementation back to the planner unless the user explicitly asks for it; the planner is there for decisions, not for hands-on edits.

Escalation: go back to the planner with `followup_task` when the plan fails twice, requirements conflict, a design or architecture tradeoff is needed, or verification keeps failing after focused attempts. Include the failing command or error, what you already tried, and the exact decision you need. Do not abandon the task because it is difficult, and do not ask the user before the planner has had a chance to unblock you. Whenever a tool can still make progress, keep working instead of ending the turn.

Every `spawn_agent` call for the planner must explicitly use `agent_type` `{MANAGED_EXECUTOR_AGENT_NAME}`, `fork_turns` `"none"`, `model` `{model}`, and `reasoning_effort` `{effort}`. Never use `fork_turns` `"all"` or omit these model fields for this agent: a full-history fork inherits the expensive primary model. Do not substitute a built-in worker, explorer, guardian, or inherited default unless the user explicitly requests it or the configured combination is unavailable.

The primary model owns the result: finish the requested change, run the relevant verification, and report what changed, what was verified, and any remaining blocker. Ask the user only for a decision that genuinely blocks safe completion.
{DUAL_MODE_POLICY_END}"""


def has_dual_mode_policy(instructions: object) -> bool:
    return isinstance(instructions, str) and bool(
        _DUAL_MODE_POLICY_RE.search(instructions)
    )


def merge_dual_mode_policy(
    instructions: object,
    enabled: bool,
    subagent_model: str = "",
    subagent_effort: str = "",
) -> str:
    existing = instructions if isinstance(instructions, str) else ""
    preserved = _DUAL_MODE_POLICY_RE.sub("", existing).strip()
    if not enabled:
        return preserved
    policy = build_dual_mode_policy(subagent_model, subagent_effort)
    return f"{preserved}\n\n{policy}".strip()


def build_managed_executor_agent(model: str, effort: str) -> str:
    return f'''{MANAGED_EXECUTOR_AGENT_MARKER}
name = {json.dumps(MANAGED_EXECUTOR_AGENT_NAME)}
description = "High-capability planner and advisor managed by Codex Agent Console."
model = {json.dumps(_policy_value(model))}
model_reasoning_effort = {json.dumps(_policy_value(effort))}
developer_instructions = """
You are the high-capability planner for a primary implementer. When asked to plan, return a compact, actionable plan: goal, ordered steps, files or modules to touch, constraints, risks, and concrete acceptance checks. Prefer specific edits and commands over general advice. Do not take over the implementation unless explicitly asked. When the implementer reports a blocker, diagnose the exact failure and answer with a decision, a patch approach, or one corrective step to try next; never restate the problem or reply with a generic suggestion. Keep plans short enough to act on immediately.
"""
'''


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError(f"Unsupported TOML value: {type(value).__name__}")


_TABLE_RE = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")


def update_toml_values(text: str, updates: dict[tuple[str, str], object]) -> str:
    """Update selected TOML keys while preserving unrelated text and comments."""
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    pending = dict(updates)
    section = ""
    seen_sections: set[str] = set()
    output: list[str] = []

    def flush_missing(current_section: str) -> None:
        for (target_section, key), value in list(pending.items()):
            if target_section == current_section:
                output.append(f"{key} = {_toml_value(value)}")
                del pending[(target_section, key)]

    for line in lines:
        table_match = _TABLE_RE.match(line)
        if table_match:
            flush_missing(section)
            section = table_match.group(1).strip()
            seen_sections.add(section)
            output.append(line)
            continue

        replaced = False
        for (target_section, key), value in list(pending.items()):
            if target_section != section:
                continue
            key_match = re.match(
                rf"^(\s*){re.escape(key)}\s*=.*?(\s+#.*)?$", line
            )
            if key_match:
                suffix = key_match.group(2) or ""
                output.append(
                    f"{key_match.group(1)}{key} = {_toml_value(value)}{suffix}"
                )
                del pending[(target_section, key)]
                replaced = True
                break
        if not replaced:
            output.append(line)

    flush_missing(section)

    grouped: dict[str, list[tuple[str, object]]] = {}
    for (target_section, key), value in pending.items():
        grouped.setdefault(target_section, []).append((key, value))
    for target_section, values in grouped.items():
        if output and output[-1].strip():
            output.append("")
        if target_section and target_section not in seen_sections:
            output.append(f"[{target_section}]")
        for key, value in values:
            output.append(f"{key} = {_toml_value(value)}")

    return newline.join(output).rstrip() + newline


@dataclass(frozen=True)
class AgentSettings:
    main_model: str
    main_effort: str
    subagent_model: str
    subagent_effort: str
    agents_enabled: bool
    max_threads: int


def load_model_priorities(home: Path) -> dict[str, int]:
    """Tier order from Codex's model cache (lower number = more capable)."""
    priorities: dict[str, int] = {}
    try:
        data = json.loads((home / "models_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return priorities
    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return priorities
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        priority = entry.get("priority")
        if isinstance(slug, str) and isinstance(priority, int):
            priorities[slug.strip()] = priority
    return priorities


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> AgentSettings:
        # New installations default to the reversed split: the cheaper model
        # executes while the high-capability model plans and reviews.
        if not self.path.exists():
            return AgentSettings(
                DEFAULT_MAIN_MODEL,
                DEFAULT_MAIN_EFFORT,
                DEFAULT_SUBAGENT_MODEL,
                DEFAULT_SUBAGENT_EFFORT,
                False,
                4,
            )
        text = self.path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        agents = data.get("agents") if isinstance(data.get("agents"), dict) else {}
        active_instruction_path = self.active_global_instruction_path
        has_global_policy = active_instruction_path.exists() and has_dual_mode_policy(
            active_instruction_path.read_text(encoding="utf-8")
        )
        has_legacy_policy = has_dual_mode_policy(data.get("developer_instructions"))
        dual_mode_enabled = bool(agents.get("enabled", True)) and (
            has_global_policy or has_legacy_policy
        )
        return AgentSettings(
            main_model=str(data.get("model") or DEFAULT_MAIN_MODEL),
            main_effort=str(data.get("model_reasoning_effort") or DEFAULT_MAIN_EFFORT),
            subagent_model=str(
                agents.get("default_subagent_model")
                or data.get("model")
                or DEFAULT_SUBAGENT_MODEL
            ),
            subagent_effort=str(
                agents.get("default_subagent_reasoning_effort")
                or DEFAULT_SUBAGENT_EFFORT
            ),
            agents_enabled=dual_mode_enabled,
            max_threads=max(
                1,
                min(
                    16,
                    int(
                        agents.get("max_concurrent_threads_per_session")
                        or agents.get("max_threads")
                        or 4
                    ),
                ),
            ),
        )

    @property
    def managed_executor_path(self) -> Path:
        return self.path.parent / "agents" / MANAGED_EXECUTOR_AGENT_FILE

    @property
    def global_agents_path(self) -> Path:
        return self.path.parent / GLOBAL_AGENTS_FILENAME

    @property
    def global_agents_override_path(self) -> Path:
        return self.path.parent / GLOBAL_AGENTS_OVERRIDE_FILENAME

    @property
    def global_instruction_paths(self) -> tuple[Path, Path]:
        return self.global_agents_path, self.global_agents_override_path

    @property
    def active_global_instruction_path(self) -> Path:
        override = self.global_agents_override_path
        if override.exists() and override.read_text(encoding="utf-8").strip():
            return override
        return self.global_agents_path

    @staticmethod
    def _write_text_atomically(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)

    def _save_global_instruction_policy(self, settings: AgentSettings) -> None:
        target = self.active_global_instruction_path
        existing_target = (
            target.read_text(encoding="utf-8") if target.exists() else ""
        )

        for path in self.global_instruction_paths:
            if path == target or not path.exists():
                continue
            existing = path.read_text(encoding="utf-8")
            cleaned = merge_dual_mode_policy(existing, False)
            if cleaned != existing:
                self._write_text_atomically(path, cleaned)

        updated_target = merge_dual_mode_policy(
            existing_target,
            settings.agents_enabled,
            settings.subagent_model,
            settings.subagent_effort,
        )
        if settings.agents_enabled or updated_target != existing_target:
            self._write_text_atomically(target, updated_target)

    def _save_managed_executor(self, settings: AgentSettings) -> None:
        path = self.managed_executor_path
        text = build_managed_executor_agent(
            settings.subagent_model, settings.subagent_effort
        )
        tomllib.loads(text)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if MANAGED_EXECUTOR_AGENT_MARKER not in existing:
                raise ValueError(f"执行代理文件已被用户占用：{path}")
        self._write_text_atomically(path, text)

    def _remove_managed_executor(self) -> None:
        path = self.managed_executor_path
        if not path.exists():
            return
        existing = path.read_text(encoding="utf-8")
        if MANAGED_EXECUTOR_AGENT_MARKER in existing:
            path.unlink()

    def save(self, settings: AgentSettings) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        original = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
        original_data: dict[str, object] = {}
        if original.strip():
            original_data = tomllib.loads(original)
        legacy_instructions = merge_dual_mode_policy(
            original_data.get("developer_instructions"), False
        )
        updates: dict[tuple[str, str], object] = {
            ("", "model"): settings.main_model,
            ("", "model_reasoning_effort"): settings.main_effort,
            ("agents", "enabled"): settings.agents_enabled,
            ("agents", "default_subagent_model"): settings.subagent_model,
            (
                "agents",
                "default_subagent_reasoning_effort",
            ): settings.subagent_effort,
            (
                "agents",
                "max_concurrent_threads_per_session",
            ): settings.max_threads,
        }
        if "developer_instructions" in original_data or legacy_instructions:
            updates[("", "developer_instructions")] = legacy_instructions
        updated = update_toml_values(original, updates)
        tomllib.loads(updated)

        if settings.agents_enabled:
            self._save_managed_executor(settings)
        else:
            self._remove_managed_executor()

        backup = self.path.with_name(self.path.name + ".codex-agent-console.bak")
        if self.path.exists():
            shutil.copy2(self.path, backup)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)
        self._save_global_instruction_policy(settings)
        return backup


@dataclass(frozen=True)
class DesktopBackendProcess:
    pid: int
    parent_pid: int
    name: str
    executable_path: str
    command_line: str


@dataclass(frozen=True)
class DesktopBackendReloadResult:
    status: str
    pids: tuple[int, ...] = ()
    error: str = ""


class DesktopBackendReloader:
    """Reload only the Desktop-owned Codex app-server, never a standalone CLI."""

    _DESKTOP_CODEX_PATH = "\\app\\resources\\codex.exe"

    @staticmethod
    def _normalized_path(value: str) -> str:
        return value.replace("/", "\\").casefold()

    @classmethod
    def _has_desktop_ancestor(
        cls,
        process: DesktopBackendProcess,
        processes_by_pid: dict[int, DesktopBackendProcess],
    ) -> bool:
        current_pid = process.parent_pid
        for _ in range(8):
            parent = processes_by_pid.get(current_pid)
            if parent is None:
                return False
            path = cls._normalized_path(parent.executable_path)
            if (
                parent.name.casefold() == "chatgpt.exe"
                and "\\windowsapps\\openai.codex_" in path
            ):
                return True
            current_pid = parent.parent_pid
        return False

    @classmethod
    def select_desktop_backends(
        cls, processes: list[DesktopBackendProcess]
    ) -> list[DesktopBackendProcess]:
        processes_by_pid = {process.pid: process for process in processes}
        selected: list[DesktopBackendProcess] = []
        for process in processes:
            path = cls._normalized_path(process.executable_path)
            command_line = process.command_line.casefold()
            is_desktop_binary = (
                "\\windowsapps\\openai.codex_" in path
                and path.endswith(cls._DESKTOP_CODEX_PATH)
            )
            is_app_server = re.search(r"(?:^|\s)app-server(?:\s|$)", command_line)
            if (
                process.name.casefold() == "codex.exe"
                and is_desktop_binary
                and is_app_server
                and cls._has_desktop_ancestor(process, processes_by_pid)
            ):
                selected.append(process)
        return selected

    @staticmethod
    def _creation_flags() -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def _read_processes(self) -> list[DesktopBackendProcess]:
        if os.name != "nt":
            return []
        command = (
            "$ErrorActionPreference = 'Stop'\n"
            "Get-CimInstance Win32_Process | "
            "Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine | "
            "ConvertTo-Json -Compress"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=self._creation_flags(),
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(detail or "无法读取 Windows 进程列表")
        if not result.stdout.strip():
            return []
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Windows 进程列表格式无效") from exc
        rows = data if isinstance(data, list) else [data]
        processes: list[DesktopBackendProcess] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                pid = int(row.get("ProcessId") or 0)
                parent_pid = int(row.get("ParentProcessId") or 0)
            except (TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            processes.append(
                DesktopBackendProcess(
                    pid=pid,
                    parent_pid=parent_pid,
                    name=str(row.get("Name") or ""),
                    executable_path=str(row.get("ExecutablePath") or ""),
                    command_line=str(row.get("CommandLine") or ""),
                )
            )
        return processes

    def find(self) -> list[DesktopBackendProcess]:
        return self.select_desktop_backends(self._read_processes())

    def reload(
        self, processes: list[DesktopBackendProcess] | None = None
    ) -> DesktopBackendReloadResult:
        if os.name != "nt":
            return DesktopBackendReloadResult("unsupported")
        candidates = self.find() if processes is None else processes
        if not candidates:
            return DesktopBackendReloadResult("not_running")
        stopped: list[int] = []
        failures: list[str] = []
        for process in candidates:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self._creation_flags(),
                check=False,
            )
            if result.returncode == 0:
                stopped.append(process.pid)
            else:
                detail = (result.stderr or result.stdout).strip()
                failures.append(f"PID {process.pid}: {detail or '终止失败'}")
        if stopped:
            return DesktopBackendReloadResult(
                "reloaded", tuple(stopped), "\n".join(failures)
            )
        return DesktopBackendReloadResult("failed", error="\n".join(failures))


@dataclass
class Usage:
    sessions: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    models: dict[str, int] = field(default_factory=dict)
    latest_model: str = ""
    latest_started_at: datetime | None = None

    def add(
        self,
        token_data: dict[str, object],
        model: str = "",
        reasoning_effort: str = "",
        started_at: datetime | None = None,
    ) -> None:
        self.sessions += 1
        if model:
            label = f"{model}/{reasoning_effort}" if reasoning_effort else model
            self.models[label] = self.models.get(label, 0) + 1
            if started_at is not None and (
                self.latest_started_at is None or started_at >= self.latest_started_at
            ):
                self.latest_model = label
                self.latest_started_at = started_at
        for name in (
            "input_tokens",
            "cached_input_tokens",
            "cache_write_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
            "total_tokens",
        ):
            try:
                setattr(self, name, getattr(self, name) + int(token_data.get(name) or 0))
            except (TypeError, ValueError):
                pass

    @property
    def cache_hit_rate(self) -> float:
        return (
            self.cached_input_tokens * 100.0 / self.input_tokens
            if self.input_tokens
            else 0.0
        )


@dataclass(frozen=True)
class DiagnosticResult:
    role: str
    model: str
    effort: str
    attempt: int
    success: bool
    status: str
    first_response_seconds: float | None
    total_seconds: float
    process_seconds: float = 0.0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    tokens_per_second: float = 0.0
    error: str = ""

    @property
    def cleanup_seconds(self) -> float:
        return max(0.0, self.process_seconds - self.total_seconds)


@dataclass(frozen=True)
class DiagnosticSummary:
    total: int = 0
    succeeded: int = 0
    success_rate: float = 0.0
    average_first_response: float | None = None
    average_total_time: float | None = None
    average_tokens_per_second: float | None = None


def summarize_diagnostics(results: list[DiagnosticResult]) -> DiagnosticSummary:
    if not results:
        return DiagnosticSummary()
    succeeded = sum(result.success for result in results)
    first_response_values = [
        result.first_response_seconds
        for result in results
        if result.first_response_seconds is not None
    ]
    total_values = [result.total_seconds for result in results]
    speed_values = [
        result.tokens_per_second
        for result in results
        if result.success and result.output_tokens > 0
    ]
    return DiagnosticSummary(
        total=len(results),
        succeeded=succeeded,
        success_rate=succeeded * 100.0 / len(results),
        average_first_response=(
            sum(first_response_values) / len(first_response_values)
            if first_response_values
            else None
        ),
        average_total_time=sum(total_values) / len(total_values),
        average_tokens_per_second=(
            sum(speed_values) / len(speed_values) if speed_values else None
        ),
    )


def classify_diagnostic_error(message: str) -> str:
    normalized = message.casefold()
    if re.search(r"(?:^|\D)401(?:\D|$)", normalized) or any(
        term in normalized
        for term in ("unauthorized", "authentication", "invalid api key")
    ):
        return "401 认证失败"
    if re.search(r"(?:^|\D)404(?:\D|$)", normalized) or (
        "model" in normalized and "not found" in normalized
    ):
        return "404 模型不可用"
    if re.search(r"(?:^|\D)429(?:\D|$)", normalized) or any(
        term in normalized for term in ("rate limit", "too many requests", "quota")
    ):
        return "429 请求受限"
    if "timeout" in normalized or "timed out" in normalized:
        return "请求超时"
    if "用户停止" in message or "cancelled" in normalized or "canceled" in normalized:
        return "已停止"
    if any(
        term in normalized
        for term in (
            "connection",
            "connect error",
            "network",
            "dns",
            "socket",
            "tls",
            "ssl",
            "certificate",
        )
    ):
        return "网络错误"
    return "CLI 失败"


def _safe_token_count(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


_CUMULATIVE_TOKEN_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def _cumulative_token_delta(
    latest: dict[str, object], baseline: dict[str, object]
) -> dict[str, int]:
    return {
        name: max(
            0,
            _safe_token_count(latest.get(name))
            - _safe_token_count(baseline.get(name)),
        )
        for name in _CUMULATIVE_TOKEN_USAGE_FIELDS
    }


def normalize_token_usage(value: object) -> dict[str, int]:
    usage = value if isinstance(value, dict) else {}
    input_details = usage.get("input_tokens_details")
    cached_fallback = (
        input_details.get("cached_tokens")
        if isinstance(input_details, dict)
        else 0
    )
    return {
        "input_tokens": _safe_token_count(usage.get("input_tokens")),
        "cached_input_tokens": _safe_token_count(
            usage.get("cached_input_tokens") or cached_fallback
        ),
        "output_tokens": _safe_token_count(usage.get("output_tokens")),
    }


def _event_error_message(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("message", "detail", "error", "code"):
            message = _event_error_message(value.get(key))
            if message:
                return message
    return ""


def parse_codex_json_event(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    event_type = value.get("type")
    parsed: dict[str, object] = {}
    if event_type == "thread.started" and isinstance(value.get("thread_id"), str):
        parsed["thread_id"] = value["thread_id"]

    item = value.get("item")
    item_type = item.get("type") if isinstance(item, dict) else None
    if event_type in ("item.started", "item.completed") and item_type in (
        "agent_message",
        "message",
    ):
        parsed["first_response"] = True

    if event_type == "turn.completed":
        parsed["completed"] = True
        parsed["usage"] = normalize_token_usage(value.get("usage"))
    if event_type in ("turn.failed", "error"):
        parsed["failed"] = True
        message = _event_error_message(value.get("error"))
        if not message:
            message = _event_error_message(value.get("message"))
        if message:
            parsed["error"] = message
    return parsed


def find_codex_executable(
    local_appdata: Path | None = None,
    path_lookup: Callable[[str], str | None] = shutil.which,
) -> Path | None:
    local_root = local_appdata
    if local_root is None:
        configured = os.environ.get("LOCALAPPDATA", "").strip()
        local_root = Path(configured) if configured else None

    candidates: list[Path] = []
    if local_root is not None:
        bundled_root = local_root / "OpenAI" / "Codex" / "bin"
        try:
            bundled = sorted(
                bundled_root.glob("*/codex.exe"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            bundled = []
        candidates.extend(bundled)
        candidates.append(local_root / "Microsoft" / "WindowsApps" / "codex.exe")

    for name in ("codex.exe", "codex"):
        located = path_lookup(name)
        if located:
            candidates.append(Path(located))

    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            resolved = candidate
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        if candidate.is_file() and (sys.platform != "win32" or candidate.suffix.lower() == ".exe"):
            return candidate
    return None


def locate_diagnostic_session(
    sessions_dir: Path, thread_id: str
) -> Path | None:
    if not thread_id or not sessions_dir.exists():
        return None
    try:
        matches = list(sessions_dir.rglob(f"*{thread_id}*.jsonl"))
    except OSError:
        return None
    if not matches:
        return None
    try:
        return max(matches, key=lambda path: path.stat().st_mtime_ns)
    except OSError:
        return matches[0]


class SessionTokenTail:
    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.latest = normalize_token_usage({})

    def read_latest(self) -> dict[str, int]:
        try:
            if self.path.stat().st_size < self.offset:
                self.offset = 0
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.offset)
                while True:
                    line_offset = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        self.offset = line_offset
                        break
                    self.offset = handle.tell()
                    try:
                        item = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    payload = item.get("payload") if isinstance(item, dict) else None
                    if not isinstance(payload, dict) or payload.get("type") != "token_count":
                        continue
                    info = payload.get("info")
                    total = (
                        info.get("total_token_usage")
                        if isinstance(info, dict)
                        else None
                    )
                    if isinstance(total, dict):
                        self.latest = normalize_token_usage(total)
        except OSError:
            pass
        return dict(self.latest)


class CodexDiagnosticsRunner:
    def __init__(self, executable: Path, home: Path, workdir: Path):
        self.executable = executable
        self.home = home
        self.workdir = workdir
        self._cancel = threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._process_lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def run(
        self,
        cases: list[tuple[str, str, str]],
        attempts: int,
        timeout_seconds: float,
        on_progress: Callable[[dict[str, object]], None],
        on_result: Callable[[DiagnosticResult], None],
    ) -> None:
        try:
            for attempt in range(1, attempts + 1):
                for role, model, effort in cases:
                    if self._cancel.is_set():
                        return
                    try:
                        result = self._run_one(
                            role,
                            model,
                            effort,
                            attempt,
                            timeout_seconds,
                            on_progress,
                        )
                    except Exception as exc:
                        message = f"诊断器异常：{exc}"
                        result = DiagnosticResult(
                            role=role,
                            model=model,
                            effort=effort,
                            attempt=attempt,
                            success=False,
                            status="CLI 失败",
                            first_response_seconds=None,
                            total_seconds=0.0,
                            error=message,
                        )
                    on_result(result)
        finally:
            on_progress(
                {"phase": "finished", "cancelled": self._cancel.is_set()}
            )

    @staticmethod
    def _read_stdout(
        stream: object, output: queue.Queue[str | None]
    ) -> None:
        try:
            for line in stream:  # type: ignore[union-attr]
                output.put(line)
        finally:
            output.put(None)

    @staticmethod
    def _read_stderr(stream: object, output: list[str]) -> None:
        try:
            for line in stream:  # type: ignore[union-attr]
                output.append(line)
        except (OSError, ValueError):
            pass

    def _run_one(
        self,
        role: str,
        model: str,
        effort: str,
        attempt: int,
        timeout_seconds: float,
        on_progress: Callable[[dict[str, object]], None],
    ) -> DiagnosticResult:
        command = [
            str(self.executable),
            "-a",
            "never",
            "-s",
            "read-only",
            "-m",
            model,
            "-c",
            f'model_reasoning_effort="{effort}"',
            "-c",
            "agents.enabled=false",
            "exec",
            "--skip-git-repo-check",
            "--json",
            "-C",
            str(self.workdir),
            DIAGNOSTIC_PROMPT,
        ]
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
        except OSError as exc:
            message = str(exc)
            return DiagnosticResult(
                role,
                model,
                effort,
                attempt,
                False,
                classify_diagnostic_error(message),
                None,
                time.monotonic() - started,
                error=message,
            )

        with self._process_lock:
            self._process = process
        stdout_lines: queue.Queue[str | None] = queue.Queue()
        stderr_lines: list[str] = []
        stdout_thread = threading.Thread(
            target=self._read_stdout,
            args=(process.stdout, stdout_lines),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process.stderr, stderr_lines),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        thread_id = ""
        session_tail: SessionTokenTail | None = None
        session_usage = normalize_token_usage({})
        final_usage = normalize_token_usage({})
        first_response: float | None = None
        completed = False
        response_completed_seconds: float | None = None
        event_error = ""
        stdout_done = False
        process_exit_seconds: float | None = None
        forced_error = ""
        termination_started: float | None = None
        speed_samples: list[tuple[float, int]] = []
        live_speed = 0.0
        cleanup_forced = False
        next_session_poll = started
        next_progress = started

        def consume_stdout() -> None:
            nonlocal thread_id, first_response, completed, event_error, stdout_done
            nonlocal response_completed_seconds
            while True:
                try:
                    line = stdout_lines.get_nowait()
                except queue.Empty:
                    break
                if line is None:
                    stdout_done = True
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError):
                    continue
                parsed = parse_codex_json_event(event)
                if isinstance(parsed.get("thread_id"), str):
                    thread_id = str(parsed["thread_id"])
                if parsed.get("first_response") and first_response is None:
                    first_response = time.monotonic() - started
                if parsed.get("completed"):
                    completed = True
                    if response_completed_seconds is None:
                        response_completed_seconds = time.monotonic() - started
                    usage = parsed.get("usage")
                    if isinstance(usage, dict):
                        final_usage.update(usage)
                if parsed.get("failed"):
                    event_error = str(parsed.get("error") or "Codex 请求失败")

        while True:
            consume_stdout()
            now = time.monotonic()
            elapsed = now - started
            return_code = process.poll()
            if return_code is not None and process_exit_seconds is None:
                process_exit_seconds = elapsed

            if not completed and not forced_error and self._cancel.is_set():
                forced_error = "用户停止了诊断请求"
            elif not completed and not forced_error and elapsed >= timeout_seconds:
                forced_error = f"请求超时（超过 {timeout_seconds:g} 秒）"

            cleanup_elapsed = (
                elapsed - response_completed_seconds
                if response_completed_seconds is not None
                else 0.0
            )
            if (
                completed
                and return_code is None
                and termination_started is None
                and (
                    self._cancel.is_set()
                    or cleanup_elapsed >= DIAGNOSTIC_CLEANUP_GRACE_SECONDS
                )
            ):
                cleanup_forced = True
                termination_started = now
                try:
                    process.terminate()
                except OSError:
                    pass

            if forced_error and return_code is None and termination_started is None:
                termination_started = now
                try:
                    process.terminate()
                except OSError:
                    pass
            if (
                termination_started is not None
                and return_code is None
                and now - termination_started >= 2.0
            ):
                try:
                    process.kill()
                except OSError:
                    pass

            if now >= next_session_poll:
                if session_tail is None and thread_id:
                    session_path = locate_diagnostic_session(
                        self.home / "sessions", thread_id
                    )
                    if session_path is not None:
                        session_tail = SessionTokenTail(session_path)
                if session_tail is not None:
                    session_usage = session_tail.read_latest()
                    output_tokens = session_usage["output_tokens"]
                    if output_tokens > 0 and first_response is None:
                        first_response = elapsed
                    speed_samples.append((now, output_tokens))
                    speed_samples = [
                        sample for sample in speed_samples if now - sample[0] <= 3.0
                    ]
                    if len(speed_samples) >= 2:
                        duration = speed_samples[-1][0] - speed_samples[0][0]
                        token_delta = speed_samples[-1][1] - speed_samples[0][1]
                        live_speed = max(0.0, token_delta / duration) if duration else 0.0
                    if output_tokens > 0 and live_speed == 0.0:
                        live_speed = output_tokens / max(0.05, elapsed)
                next_session_poll = now + 0.25

            if now >= next_progress:
                on_progress(
                    {
                        "phase": "cleanup" if completed else "running",
                        "role": role,
                        "model": model,
                        "effort": effort,
                        "attempt": attempt,
                        "elapsed": elapsed,
                        "output_tokens": session_usage["output_tokens"],
                        "tokens_per_second": live_speed,
                        "response_seconds": response_completed_seconds,
                        "cleanup_seconds": cleanup_elapsed,
                    }
                )
                next_progress = now + 0.25

            if return_code is not None and (
                stdout_done
                or (
                    process_exit_seconds is not None
                    and elapsed - process_exit_seconds >= 0.35
                )
            ):
                break
            time.sleep(0.05)

        consume_stdout()
        stdout_thread.join(timeout=0.2)
        stderr_thread.join(timeout=0.2)
        loop_seconds = time.monotonic() - started
        process_seconds = process_exit_seconds or loop_seconds
        total_seconds = response_completed_seconds or process_seconds
        with self._process_lock:
            self._process = None

        usage = final_usage if any(final_usage.values()) else session_usage
        output_tokens = usage["output_tokens"]
        if output_tokens > 0:
            average_speed = output_tokens / max(0.05, total_seconds)
        else:
            average_speed = 0.0

        return_code = process.returncode if process.returncode is not None else -1
        success = (
            completed
            and (return_code == 0 or cleanup_forced)
            and not forced_error
            and not event_error
        )
        if success:
            cleanup_slow = (
                cleanup_forced
                or process_seconds - total_seconds
                >= DIAGNOSTIC_CLEANUP_GRACE_SECONDS
            )
            status = "成功/CLI退出慢" if cleanup_slow else "成功"
            error = ""
        else:
            stderr = "".join(stderr_lines).strip()
            error = forced_error or event_error or stderr or f"Codex CLI 退出码 {return_code}"
            status = classify_diagnostic_error(error)
        return DiagnosticResult(
            role=role,
            model=model,
            effort=effort,
            attempt=attempt,
            success=success,
            status=status,
            first_response_seconds=first_response,
            total_seconds=total_seconds,
            process_seconds=process_seconds,
            input_tokens=usage["input_tokens"],
            cached_input_tokens=usage["cached_input_tokens"],
            output_tokens=output_tokens,
            tokens_per_second=average_speed,
            error=error,
        )


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def protect_secret(value: str) -> str:
    if not value:
        return ""
    if sys.platform != "win32":
        raise RuntimeError("API Key 加密保存仅支持 Windows")
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    input_blob = _DataBlob(
        len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Codex Agent Console custom API",
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if sys.platform != "win32":
        raise RuntimeError("API Key 解密仅支持 Windows")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("保存的 API Key 数据无效") from exc
    buffer = ctypes.create_string_buffer(raw)
    input_blob = _DataBlob(
        len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    )
    output_blob = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        0x01,
        ctypes.byref(output_blob),
    ):
        raise ctypes.WinError()
    try:
        decrypted = ctypes.string_at(output_blob.pbData, output_blob.cbData)
        return decrypted.decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output_blob.pbData)


@dataclass
class CustomApiEndpoint:
    endpoint_id: str
    name: str
    url: str
    model: str
    auth_mode: str = "bearer"
    encrypted_api_key: str = ""
    selected: bool = True
    api_type: str = "chat_completions"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.endpoint_id,
            "name": self.name,
            "url": self.url,
            "model": self.model,
            "auth_mode": self.auth_mode,
            "encrypted_api_key": self.encrypted_api_key,
            "selected": self.selected,
            "api_type": self.api_type,
        }

    @classmethod
    def from_dict(cls, value: object) -> CustomApiEndpoint | None:
        if not isinstance(value, dict):
            return None
        endpoint_id = str(value.get("id") or "").strip()
        name = str(value.get("name") or "").strip()
        url = str(value.get("url") or "").strip()
        model = str(value.get("model") or "").strip()
        auth_mode = str(value.get("auth_mode") or "bearer").strip()
        api_type = str(value.get("api_type") or "chat_completions").strip()
        if not endpoint_id or not name or not url or not model:
            return None
        if auth_mode not in CUSTOM_API_AUTH_MODES:
            auth_mode = "bearer"
        if api_type not in CUSTOM_API_TYPES:
            api_type = "chat_completions"
        return cls(
            endpoint_id=endpoint_id,
            name=name,
            url=url,
            model=model,
            auth_mode=auth_mode,
            encrypted_api_key=str(value.get("encrypted_api_key") or ""),
            selected=bool(value.get("selected", True)),
            api_type=api_type,
        )


def custom_api_base_url(value: str) -> str:
    """Return the editable base URL for a stored endpoint or user input."""
    normalized = value.strip().rstrip("/")
    for suffix in CUSTOM_API_TYPE_SUFFIXES.values():
        if normalized.casefold().endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break
    return normalized


def custom_api_endpoint_url(value: str, api_type: str = "chat_completions") -> str:
    """Build an API endpoint URL from a provider base URL."""
    if api_type not in CUSTOM_API_TYPES:
        raise ValueError("不支持的接口类型")
    base = custom_api_base_url(value)
    parsed = urllib.parse.urlsplit(base)
    path = parsed.path.rstrip("/")
    suffix = CUSTOM_API_TYPE_SUFFIXES[api_type]
    if path.casefold().endswith("/v1"):
        path += suffix
    else:
        path += "/v1" + suffix
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )


def custom_api_models_url(value: str) -> str:
    """Build the conventional OpenAI-compatible models URL."""
    base = custom_api_base_url(value)
    parsed = urllib.parse.urlsplit(base)
    path = parsed.path.rstrip("/")
    if path.casefold().endswith("/v1"):
        path += CUSTOM_API_MODELS_SUFFIX
    else:
        path += "/v1" + CUSTOM_API_MODELS_SUFFIX
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)
    )


def validate_custom_api_url(url: str) -> None:
    parsed = urllib.parse.urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请输入基础 URL，例如 https://api.example.com/v1")
    if parsed.username or parsed.password:
        raise ValueError("URL 中不能包含用户名或密码")
    hostname = (parsed.hostname or "").casefold()
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and hostname not in local_hosts:
        raise ValueError("远程 API 必须使用 HTTPS，避免 API Key 明文传输")


def custom_api_type_label(api_type: str) -> str:
    return CUSTOM_API_TYPE_LABELS.get(api_type, api_type)


def validate_custom_api_endpoint(endpoint: CustomApiEndpoint) -> None:
    if not endpoint.name.strip():
        raise ValueError("API 名称不能为空")
    if not endpoint.model.strip():
        raise ValueError("模型不能为空")
    if endpoint.auth_mode not in CUSTOM_API_AUTH_MODES:
        raise ValueError("不支持的鉴权方式")
    if endpoint.api_type not in CUSTOM_API_TYPES:
        raise ValueError("不支持的接口类型")
    validate_custom_api_url(endpoint.url)


class CustomApiStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> list[CustomApiEndpoint]:
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        entries = data.get("endpoints") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return []
        endpoints: list[CustomApiEndpoint] = []
        seen: set[str] = set()
        for entry in entries:
            endpoint = CustomApiEndpoint.from_dict(entry)
            if endpoint is None or endpoint.endpoint_id in seen:
                continue
            validate_custom_api_endpoint(endpoint)
            endpoints.append(endpoint)
            seen.add(endpoint.endpoint_id)
        return endpoints

    def save(self, endpoints: list[CustomApiEndpoint]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "endpoints": [endpoint.to_dict() for endpoint in endpoints],
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        backup = self.path.with_name(self.path.name + ".bak")
        if self.path.exists():
            shutil.copy2(self.path, backup)
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.remove(temp_name)


@dataclass(frozen=True)
class CustomApiResult:
    endpoint_id: str
    name: str
    model: str
    attempt: int
    success: bool
    status: str
    http_status: int | None
    first_response_seconds: float | None
    total_seconds: float
    output_tokens: int = 0
    tokens_estimated: bool = False
    tokens_per_second: float = 0.0
    error: str = ""
    api_type: str = "chat_completions"
    input_tokens: int = 0
    cached_input_tokens: int = 0
    input_tokens_reported: bool = False
    cache_phase: str = ""

    @property
    def cache_hit_rate(self) -> float | None:
        if not self.input_tokens_reported or self.input_tokens <= 0:
            return None
        return self.cached_input_tokens * 100.0 / self.input_tokens


@dataclass(frozen=True)
class CustomApiSummary:
    endpoint_id: str
    name: str
    model: str
    total: int
    succeeded: int
    success_rate: float
    average_first_response: float | None
    average_total_time: float | None
    jitter_percent: float | None
    average_tokens_per_second: float | None
    api_type: str = "chat_completions"
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_hit_rate: float | None = None


def summarize_custom_apis(
    results: list[CustomApiResult],
) -> list[CustomApiSummary]:
    grouped: dict[str, list[CustomApiResult]] = {}
    order: list[str] = []
    for result in results:
        if result.cache_phase == "warmup":
            continue
        if result.endpoint_id not in grouped:
            grouped[result.endpoint_id] = []
            order.append(result.endpoint_id)
        grouped[result.endpoint_id].append(result)

    summaries: list[CustomApiSummary] = []
    for endpoint_id in order:
        endpoint_results = grouped[endpoint_id]
        successes = [result for result in endpoint_results if result.success]
        first_values = [
            result.first_response_seconds
            for result in successes
            if result.first_response_seconds is not None
        ]
        total_values = [result.total_seconds for result in successes]
        speed_values = [
            result.tokens_per_second
            for result in successes
            if result.output_tokens > 0
        ]
        usage_results = [
            result for result in successes if result.input_tokens_reported
        ]
        input_tokens = sum(result.input_tokens for result in usage_results)
        cached_input_tokens = sum(
            result.cached_input_tokens for result in usage_results
        )
        average_total = (
            sum(total_values) / len(total_values) if total_values else None
        )
        jitter = None
        if len(total_values) >= 2 and average_total:
            jitter = statistics.pstdev(total_values) * 100.0 / average_total
        sample = endpoint_results[0]
        summaries.append(
            CustomApiSummary(
                endpoint_id=endpoint_id,
                name=sample.name,
                model=sample.model,
                total=len(endpoint_results),
                succeeded=len(successes),
                success_rate=(
                    len(successes) * 100.0 / len(endpoint_results)
                    if endpoint_results
                    else 0.0
                ),
                average_first_response=(
                    sum(first_values) / len(first_values)
                    if first_values
                    else None
                ),
                average_total_time=average_total,
                jitter_percent=jitter,
                average_tokens_per_second=(
                    sum(speed_values) / len(speed_values)
                    if speed_values
                    else None
                ),
                api_type=sample.api_type,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_hit_rate=(
                    cached_input_tokens * 100.0 / input_tokens
                    if input_tokens > 0
                    else None
                ),
            )
        )
    return summaries


def choose_custom_api_winners(
    summaries: list[CustomApiSummary],
) -> tuple[CustomApiSummary | None, CustomApiSummary | None]:
    usable = [
        summary
        for summary in summaries
        if summary.average_total_time is not None and summary.succeeded > 0
    ]
    if not usable:
        return None, None
    fastest = min(
        usable,
        key=lambda summary: (
            -summary.success_rate,
            summary.average_total_time or math.inf,
            summary.jitter_percent if summary.jitter_percent is not None else math.inf,
        ),
    )
    stable = min(
        usable,
        key=lambda summary: (
            -summary.success_rate,
            summary.jitter_percent if summary.jitter_percent is not None else math.inf,
            summary.average_total_time or math.inf,
        ),
    )
    return fastest, stable


def _extract_custom_api_content(
    value: object, api_type: str = "chat_completions"
) -> str:
    if not isinstance(value, dict):
        return ""
    if api_type == "anthropic_messages":
        event_type = value.get("type")
        if event_type == "content_block_delta":
            delta = value.get("delta")
            return str(delta.get("text") or "") if isinstance(delta, dict) else ""
        if event_type == "content_block_start":
            content_block = value.get("content_block")
            return (
                str(content_block.get("text") or "")
                if isinstance(content_block, dict)
                else ""
            )
        content = value.get("content")
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        return str(value.get("text") or "")
    if api_type == "responses":
        event_type = value.get("type")
        if event_type == "response.output_text.delta":
            return str(value.get("delta") or "")
        if event_type == "response.output_text.done":
            return str(value.get("text") or "")
        output_text = value.get("output_text")
        if isinstance(output_text, str):
            return output_text
        response = value.get("response")
        if isinstance(response, dict):
            output_text = response.get("output_text")
            if isinstance(output_text, str):
                return output_text
            value = response
        output = value.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        parts.append(part["text"])
            return "".join(parts)
        return ""
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    choice = choices[0]
    source = choice.get("delta")
    if not isinstance(source, dict):
        source = choice.get("message")
    if isinstance(source, dict):
        content = source.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return "".join(parts)
    return str(choice.get("text") or "")


def _extract_custom_api_usage(
    value: object, api_type: str = "chat_completions"
) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    if api_type == "anthropic_messages":
        usage = value.get("usage")
        if not isinstance(usage, dict):
            message = value.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
        return usage if isinstance(usage, dict) else None
    usage = value.get("usage")
    if not isinstance(usage, dict) and api_type == "responses":
        response = value.get("response")
        usage = response.get("usage") if isinstance(response, dict) else None
    return usage if isinstance(usage, dict) else None


def _first_custom_api_usage_value(
    usage: dict[str, object], names: tuple[str, ...]
) -> object | None:
    for name in names:
        value = usage.get(name)
        if value is not None:
            return value
    return None


def _extract_custom_api_input_usage(
    value: object, api_type: str = "chat_completions"
) -> tuple[int, int, bool]:
    usage = _extract_custom_api_usage(value, api_type)
    if usage is None:
        return 0, 0, False

    raw_input = _first_custom_api_usage_value(
        usage,
        (
            "input_tokens",
            "prompt_tokens",
            "input_tokens_total",
        ),
    )
    input_reported = raw_input is not None
    input_tokens = _safe_token_count(raw_input)
    details = usage.get("input_tokens_details")
    if not isinstance(details, dict):
        details = usage.get("prompt_tokens_details")
    cached_value = (
        _first_custom_api_usage_value(details, ("cached_tokens",))
        if isinstance(details, dict)
        else None
    )
    cache_read_is_separate = False
    cache_creation_is_separate = False
    cache_read_value = _first_custom_api_usage_value(
        usage,
        (
            "cache_read_input_tokens",
            "cache_read_input_tokens_total",
        ),
    )
    cache_creation_value = _first_custom_api_usage_value(
        usage,
        (
            "cache_creation_input_tokens",
            "cache_creation_input_tokens_total",
            "cache_write_input_tokens",
            "cache_write_tokens",
        ),
    )
    if cached_value is None:
        cached_value = _first_custom_api_usage_value(
            usage,
            (
                "cached_input_tokens",
                "cached_tokens",
                "cache_hit_tokens",
                "prompt_cache_hit_tokens",
            ),
        )
        cache_read_is_separate = cache_read_value is not None
        cache_creation_is_separate = cache_creation_value is not None
        if cached_value is None and cache_read_value is not None:
            cached_value = cache_read_value
    cached_input_tokens = _safe_token_count(cached_value)
    if cache_read_is_separate or cache_creation_is_separate:
        input_tokens += _safe_token_count(cache_read_value)
        input_tokens += _safe_token_count(cache_creation_value)
        input_reported = True
    return input_tokens, cached_input_tokens, input_reported


def _extract_custom_api_output_tokens(
    value: object, api_type: str = "chat_completions"
) -> int:
    usage = _extract_custom_api_usage(value, api_type)
    if usage is None:
        return 0
    return _safe_token_count(
        usage.get("completion_tokens")
        or usage.get("output_tokens")
        or usage.get("output_tokens_total")
    )


def _estimated_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text.encode("utf-8")) / 4.0))


def _custom_api_error_status(message: str, http_status: int | None = None) -> str:
    classified = classify_diagnostic_error(message)
    if classified != "CLI 失败":
        return classified
    if http_status is not None:
        return f"HTTP {http_status}"
    return "请求失败"


def _custom_api_auth_headers(
    auth_mode: str,
    api_key: str,
    api_type: str = "chat_completions",
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if auth_mode == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    elif auth_mode == "x-api-key":
        headers["x-api-key"] = api_key
    if api_type == "anthropic_messages":
        headers["anthropic-version"] = ANTHROPIC_API_VERSION
    return headers


def fetch_custom_api_models(
    base_url: str,
    auth_mode: str,
    api_key: str,
    timeout_seconds: float = 15.0,
    api_type: str = "chat_completions",
) -> list[str]:
    """Fetch model IDs from an OpenAI-compatible or Anthropic /models endpoint."""
    endpoint_url = custom_api_endpoint_url(base_url, api_type)
    validate_custom_api_url(endpoint_url)
    request = urllib.request.Request(
        custom_api_models_url(base_url),
        headers={
            "Accept": "application/json",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            **_custom_api_auth_headers(auth_mode, api_key, api_type),
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(2048).decode("utf-8", errors="replace").strip()
        except OSError:
            detail = ""
        message = f"模型接口 HTTP {exc.code} {exc.reason}"
        if detail:
            message += f"：{detail}"
        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        raise ValueError(message) from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"模型接口连接失败：{exc.reason}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"模型接口返回无效：{exc}") from exc

    if isinstance(payload, dict):
        candidates = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        candidates = payload
    else:
        candidates = []
    if not isinstance(candidates, list):
        candidates = [candidates]

    models: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if isinstance(item, str):
            model_id = item.strip()
        elif isinstance(item, dict):
            model_id = str(
                item.get("id") or item.get("name") or item.get("model") or ""
            ).strip()
        else:
            model_id = ""
        if model_id and model_id not in seen:
            models.append(model_id)
            seen.add(model_id)
    if not models:
        raise ValueError("模型接口返回中没有可用模型")
    return models


class CustomApiBenchmarkRunner:
    def __init__(self):
        self._cancel = threading.Event()
        self._response_lock = threading.Lock()
        self._response: object | None = None

    def cancel(self) -> None:
        self._cancel.set()
        with self._response_lock:
            response = self._response
        if response is not None:
            try:
                response.close()  # type: ignore[union-attr]
            except (OSError, ValueError):
                pass

    def run(
        self,
        cases: list[tuple[CustomApiEndpoint, str]],
        attempts: int,
        timeout_seconds: float,
        on_progress: Callable[[dict[str, object]], None],
        on_result: Callable[[CustomApiResult], None],
        cache_test: bool = False,
    ) -> None:
        try:
            for attempt in range(1, attempts + 1):
                for endpoint, api_key in cases:
                    phases = ("warmup", "measure") if cache_test else ("",)
                    for cache_phase in phases:
                        if self._cancel.is_set():
                            return
                        try:
                            result = self._run_one(
                                endpoint,
                                api_key,
                                attempt,
                                timeout_seconds,
                                on_progress,
                                cache_test,
                                cache_phase,
                            )
                        except Exception as exc:
                            result = CustomApiResult(
                                endpoint_id=endpoint.endpoint_id,
                                name=endpoint.name,
                                model=endpoint.model,
                                attempt=attempt,
                                success=False,
                                status="测速器异常",
                                http_status=None,
                                first_response_seconds=None,
                                total_seconds=0.0,
                                error=str(exc).replace(api_key, "[REDACTED]")
                                if api_key
                                else str(exc),
                                api_type=endpoint.api_type,
                                cache_phase=cache_phase,
                            )
                        on_result(result)
        finally:
            on_progress(
                {"phase": "finished", "cancelled": self._cancel.is_set()}
            )

    def _run_one(
        self,
        endpoint: CustomApiEndpoint,
        api_key: str,
        attempt: int,
        timeout_seconds: float,
        on_progress: Callable[[dict[str, object]], None],
        cache_test: bool = False,
        cache_phase: str = "",
    ) -> CustomApiResult:
        prompt = CUSTOM_API_CACHE_PROMPT if cache_test else CUSTOM_API_PROMPT
        stream = not cache_test
        if endpoint.api_type == "anthropic_messages":
            message_content: object = prompt
            if cache_test:
                message_content = [
                    {
                        "type": "text",
                        "text": prompt,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
            payload_value = {
                "model": endpoint.model,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": message_content}],
                "stream": stream,
            }
        elif endpoint.api_type == "responses":
            payload_value = {
                "model": endpoint.model,
                "input": prompt,
                "stream": stream,
            }
        elif endpoint.api_type == "completions":
            payload_value = {
                "model": endpoint.model,
                "prompt": prompt,
                "stream": stream,
            }
        else:
            payload_value = {
                "model": endpoint.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": stream,
            }
        payload = json.dumps(payload_value, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "text/event-stream, application/json",
            "Content-Type": "application/json",
            "Connection": "close",
            "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        }
        headers.update(
            _custom_api_auth_headers(endpoint.auth_mode, api_key, endpoint.api_type)
        )
        if endpoint.api_type == "anthropic_messages" and cache_test:
            headers["anthropic-beta"] = "prompt-caching-2024-07-31"
        request = urllib.request.Request(
            custom_api_endpoint_url(endpoint.url, endpoint.api_type),
            data=payload,
            headers=headers,
            method="POST",
        )

        started = time.monotonic()
        first_response: float | None = None
        http_status: int | None = None
        output_parts: list[str] = []
        output_tokens = 0
        input_tokens = 0
        cached_input_tokens = 0
        input_tokens_reported = False
        content_events = 0
        plain_body: list[bytes] = []
        response = None
        try:
            response = urllib.request.urlopen(request, timeout=timeout_seconds)
            with self._response_lock:
                self._response = response
            http_status = response.getcode()
            for raw_line in response:
                if self._cancel.is_set():
                    raise InterruptedError("用户停止了测试")
                elapsed = time.monotonic() - started
                if elapsed >= timeout_seconds:
                    raise TimeoutError(f"请求超时（超过 {timeout_seconds:g} 秒）")
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if first_response is None:
                    first_response = elapsed
                line = stripped.decode("utf-8", errors="replace")
                if line.startswith("data:"):
                    data_text = line[5:].strip()
                    if data_text == "[DONE]":
                        break
                    try:
                        event = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    content = _extract_custom_api_content(event, endpoint.api_type)
                    if content:
                        output_parts.append(content)
                        content_events += 1
                    output_tokens = max(
                        output_tokens,
                        _extract_custom_api_output_tokens(event, endpoint.api_type),
                    )
                    (
                        event_input_tokens,
                        event_cached_input_tokens,
                        event_input_reported,
                    ) = _extract_custom_api_input_usage(event, endpoint.api_type)
                    if event_input_reported:
                        input_tokens = max(input_tokens, event_input_tokens)
                        cached_input_tokens = max(
                            cached_input_tokens, event_cached_input_tokens
                        )
                        input_tokens_reported = True
                elif line.startswith(("event:", "id:", "retry:", ":")):
                    continue
                else:
                    plain_body.append(raw_line)

                text = "".join(output_parts)
                live_tokens = output_tokens or _estimated_token_count(text)
                generation_seconds = max(0.05, elapsed - (first_response or 0.0))
                on_progress(
                    {
                        "phase": "running",
                        "name": endpoint.name,
                        "model": endpoint.model,
                        "attempt": attempt,
                        "elapsed": elapsed,
                        "output_tokens": live_tokens,
                        "tokens_per_second": live_tokens / generation_seconds,
                        "estimated": output_tokens == 0,
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_input_tokens,
                        "input_tokens_reported": input_tokens_reported,
                        "cache_phase": cache_phase,
                    }
                )

            if plain_body:
                body = b"".join(plain_body).decode("utf-8", errors="replace")
                parsed = json.loads(body)
                content = _extract_custom_api_content(parsed, endpoint.api_type)
                if content:
                    output_parts.append(content)
                    content_events += 1
                output_tokens = max(
                    output_tokens,
                    _extract_custom_api_output_tokens(parsed, endpoint.api_type),
                )
                (
                    body_input_tokens,
                    body_cached_input_tokens,
                    body_input_reported,
                ) = _extract_custom_api_input_usage(parsed, endpoint.api_type)
                if body_input_reported:
                    input_tokens = max(input_tokens, body_input_tokens)
                    cached_input_tokens = max(
                        cached_input_tokens, body_cached_input_tokens
                    )
                    input_tokens_reported = True
            response.close()
            with self._response_lock:
                self._response = None

            total_seconds = time.monotonic() - started
            output_text = "".join(output_parts)
            estimated = output_tokens == 0
            if estimated:
                output_tokens = _estimated_token_count(output_text)
            if not output_text and output_tokens == 0:
                raise ValueError("HTTP 200，但没有返回可识别的模型输出")
            if first_response is None:
                first_response = total_seconds
            if content_events >= 2:
                speed_seconds = max(0.05, total_seconds - first_response)
            else:
                speed_seconds = max(0.05, total_seconds)
            return CustomApiResult(
                endpoint_id=endpoint.endpoint_id,
                name=endpoint.name,
                model=endpoint.model,
                attempt=attempt,
                success=True,
                status="成功",
                http_status=http_status,
                first_response_seconds=first_response,
                total_seconds=total_seconds,
                output_tokens=output_tokens,
                tokens_estimated=estimated,
                tokens_per_second=output_tokens / speed_seconds,
                api_type=endpoint.api_type,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                input_tokens_reported=input_tokens_reported,
                cache_phase=cache_phase,
            )
        except urllib.error.HTTPError as exc:
            http_status = exc.code
            try:
                detail = exc.read(4096).decode("utf-8", errors="replace").strip()
            except OSError:
                detail = ""
            message = f"HTTP {exc.code} {exc.reason}"
            if detail:
                message += f"：{detail}"
        except (TimeoutError, socket.timeout) as exc:
            message = str(exc) or f"请求超时（超过 {timeout_seconds:g} 秒）"
        except urllib.error.URLError as exc:
            message = f"网络连接失败：{exc.reason}"
        except InterruptedError as exc:
            message = str(exc)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            message = str(exc)
        finally:
            if response is not None:
                try:
                    response.close()
                except (OSError, ValueError):
                    pass
            with self._response_lock:
                self._response = None

        if api_key:
            message = message.replace(api_key, "[REDACTED]")
        total_seconds = time.monotonic() - started
        return CustomApiResult(
            endpoint_id=endpoint.endpoint_id,
            name=endpoint.name,
            model=endpoint.model,
            attempt=attempt,
            success=False,
            status=_custom_api_error_status(message, http_status),
            http_status=http_status,
            first_response_seconds=first_response,
            total_seconds=total_seconds,
            error=message,
            api_type=endpoint.api_type,
            cache_phase=cache_phase,
        )


@dataclass(frozen=True)
class ParsedSession:
    started_at: datetime
    is_subagent: bool
    is_execution_subagent: bool
    tokens: dict[str, object]
    model: str = ""
    reasoning_effort: str = ""


@dataclass
class _SessionScan:
    """Resumable parse state for one session log.

    A rollout log that is still being written changes on every refresh.  Keeping
    the accumulated state lets a refresh parse only the appended bytes instead
    of re-reading hundreds of megabytes of JSONL every second.
    """

    offset: int = 0
    mtime_ns: int = 0
    size: int = 0
    started_at: datetime | None = None
    subagent_started_at: datetime | None = None
    is_subagent: bool = False
    is_execution_subagent: bool = False
    latest_tokens: dict[str, object] | None = None
    # (record index, model, effort, cumulative tokens seen before this turn);
    # the fourth item is the baseline for a forked execution subagent.
    settings_events: list[tuple[int, str, str]] = field(default_factory=list)
    turn_context_events: list[tuple[int, str, str, dict[str, object] | None]] = field(
        default_factory=list
    )
    execution_session_id: str = ""
    execution_session_meta_seen: bool = False
    has_embedded_parent_history: bool = False
    record_index: int = 0
    model: str = ""
    reasoning_effort: str = ""
    fingerprint: tuple[int, ...] = ()

    def reset(self) -> None:
        self.__init__()

    def to_json(self) -> dict[str, object]:
        return {
            "offset": self.offset,
            "mtime_ns": self.mtime_ns,
            "size": self.size,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "subagent_started_at": (
                self.subagent_started_at.isoformat()
                if self.subagent_started_at
                else None
            ),
            "is_subagent": self.is_subagent,
            "is_execution_subagent": self.is_execution_subagent,
            "latest_tokens": (
                {
                    name: _safe_token_count(self.latest_tokens.get(name))
                    for name in _CUMULATIVE_TOKEN_USAGE_FIELDS
                }
                if self.latest_tokens is not None
                else None
            ),
            "settings_events": [list(item) for item in self.settings_events],
            "turn_context_events": [
                [
                    item[0],
                    item[1],
                    item[2],
                    (
                        {
                            name: _safe_token_count(item[3].get(name))
                            for name in _CUMULATIVE_TOKEN_USAGE_FIELDS
                        }
                        if item[3] is not None
                        else None
                    ),
                ]
                for item in self.turn_context_events
            ],
            "execution_session_id": self.execution_session_id,
            "execution_session_meta_seen": self.execution_session_meta_seen,
            "has_embedded_parent_history": self.has_embedded_parent_history,
            "record_index": self.record_index,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "fingerprint": list(self.fingerprint),
        }

    @classmethod
    def from_json(cls, value: object) -> _SessionScan | None:
        if not isinstance(value, dict):
            return None

        def parse_stamp(raw: object) -> datetime | None:
            if not isinstance(raw, str):
                return None
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None

        def parse_tokens(raw: object) -> dict[str, object] | None:
            return raw if isinstance(raw, dict) else None

        def parse_events(raw: object) -> list[tuple[int, str, str]]:
            events: list[tuple[int, str, str]] = []
            if not isinstance(raw, list):
                return events
            for entry in raw:
                if isinstance(entry, (list, tuple)) and len(entry) == 3:
                    events.append((int(entry[0]), str(entry[1]), str(entry[2])))
            return events

        def parse_turn_events(
            raw: object,
        ) -> list[tuple[int, str, str, dict[str, object] | None]]:
            events: list[tuple[int, str, str, dict[str, object] | None]] = []
            if not isinstance(raw, list):
                return events
            for entry in raw:
                if isinstance(entry, (list, tuple)) and len(entry) == 4:
                    tokens = entry[3]
                    events.append(
                        (
                            int(entry[0]),
                            str(entry[1]),
                            str(entry[2]),
                            tokens if isinstance(tokens, dict) else None,
                        )
                    )
            return events

        try:
            scan = cls()
            scan.offset = int(value.get("offset") or 0)
            scan.mtime_ns = int(value.get("mtime_ns") or 0)
            scan.size = int(value.get("size") or 0)
            scan.started_at = parse_stamp(value.get("started_at"))
            scan.subagent_started_at = parse_stamp(value.get("subagent_started_at"))
            scan.is_subagent = bool(value.get("is_subagent"))
            scan.is_execution_subagent = bool(value.get("is_execution_subagent"))
            scan.latest_tokens = parse_tokens(value.get("latest_tokens"))
            scan.settings_events = parse_events(value.get("settings_events"))
            scan.turn_context_events = parse_turn_events(
                value.get("turn_context_events")
            )
            scan.execution_session_id = str(value.get("execution_session_id") or "")
            scan.execution_session_meta_seen = bool(
                value.get("execution_session_meta_seen")
            )
            scan.has_embedded_parent_history = bool(
                value.get("has_embedded_parent_history")
            )
            scan.record_index = int(value.get("record_index") or 0)
            scan.model = str(value.get("model") or "")
            scan.reasoning_effort = str(value.get("reasoning_effort") or "")
            fingerprint = value.get("fingerprint")
            scan.fingerprint = (
                tuple(int(item) for item in fingerprint)
                if isinstance(fingerprint, list)
                else ()
            )
            return scan
        except (TypeError, ValueError):
            return None


STATS_CACHE_VERSION = 3
STATS_CACHE_NAME = "codex-agent-console-stats-cache.json"
STATS_CACHE_SAVE_INTERVAL = 30.0
# (inode, size, mtime_ns, head byte, tail byte); detects append-only growth and
# any in-place rewrite that would make a resumed offset meaningless.
_SCAN_FINGERPRINT_BYTES = 256
_SESSION_HEAD_BYTES = 4096


def _parsed_session_to_json(parsed: ParsedSession) -> dict[str, object]:
    return {
        "started_at": parsed.started_at.isoformat(),
        "is_subagent": parsed.is_subagent,
        "is_execution_subagent": parsed.is_execution_subagent,
        "tokens": {
            name: _safe_token_count(parsed.tokens.get(name))
            for name in _CUMULATIVE_TOKEN_USAGE_FIELDS
        },
        "model": parsed.model,
        "reasoning_effort": parsed.reasoning_effort,
    }


def _parsed_session_from_json(value: object) -> ParsedSession | None:
    if not isinstance(value, dict):
        return None
    raw_started = value.get("started_at")
    if not isinstance(raw_started, str):
        return None
    try:
        started_at = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
    except ValueError:
        return None
    tokens = value.get("tokens")
    return ParsedSession(
        started_at=started_at,
        is_subagent=bool(value.get("is_subagent")),
        is_execution_subagent=bool(value.get("is_execution_subagent")),
        tokens=tokens if isinstance(tokens, dict) else {},
        model=str(value.get("model") or ""),
        reasoning_effort=str(value.get("reasoning_effort") or ""),
    )


def _scan_identity(path: Path, end: int) -> tuple[int, ...]:
    """Identity of the log prefix ``[0, end)`` so appended lines can be resumed.

    The probe hashes the first and the last few hundred bytes of the consumed
    prefix: an append-only log keeps both unchanged, while an in-place rewrite
    or truncation changes at least one of them.
    """
    try:
        stat = path.stat()
        inode = getattr(stat, "st_ino", 0)
        with path.open("rb") as handle:
            head = handle.read(min(_SCAN_FINGERPRINT_BYTES, end))
            tail = b""
            if end > _SCAN_FINGERPRINT_BYTES:
                handle.seek(max(0, end - _SCAN_FINGERPRINT_BYTES))
                tail = handle.read(_SCAN_FINGERPRINT_BYTES)
    except OSError:
        return ()
    return (inode, end, len(head), sum(head), len(tail), sum(tail))


def _scan_identity_compatible(
    previous: tuple[int, ...], current: tuple[int, ...]
) -> bool:
    """True when the previously parsed prefix is still intact."""
    if len(previous) != 6 or len(current) != 6:
        return False
    return previous == current


class SessionStatsReader:
    """Aggregate Codex session logs.

    Parsing a large rollout log is the expensive part, so parsed results are
    cached by (mtime, size).  The cache is also persisted next to the Codex
    home directory, which keeps the first paint of a new process fast even
    when the session directory holds hundreds of megabytes of JSONL.
    """

    def __init__(self, sessions_dir: Path, cache_path: Path | None = None):
        self.sessions_dir = sessions_dir
        self.cache_path = cache_path
        self._cache: dict[Path, tuple[int, int, ParsedSession | None]] = {}
        self._scans: dict[Path, _SessionScan] = {}
        self._cache_loaded = cache_path is None
        self._cache_dirty = False
        self._last_save = 0.0
        # Bytes parsed since construction; lets tests prove a refresh only read
        # the appended tail instead of the whole rollout log.
        self.scanned_bytes = 0

    def _apply_record(self, scan: _SessionScan, item: object) -> None:
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict):
            return
        scan.record_index += 1
        record_index = scan.record_index
        if item.get("type") == "session_meta":
            source = payload.get("source")
            record_is_subagent = payload.get("thread_source") == "subagent" or (
                isinstance(source, dict) and "subagent" in source
            )
            source_subagent = (
                source.get("subagent") if isinstance(source, dict) else None
            )
            is_system_subagent = isinstance(source_subagent, dict) and bool(
                source_subagent.get("other")
            )
            scan.is_subagent = scan.is_subagent or record_is_subagent
            scan.is_execution_subagent = scan.is_execution_subagent or (
                record_is_subagent and not is_system_subagent
            )
            record_session_id = payload.get("id")
            if record_is_subagent and not is_system_subagent:
                if scan.execution_session_meta_seen:
                    scan.has_embedded_parent_history = (
                        scan.has_embedded_parent_history
                        or not scan.execution_session_id
                        or not isinstance(record_session_id, str)
                        or record_session_id != scan.execution_session_id
                    )
                else:
                    scan.execution_session_meta_seen = True
                    if isinstance(record_session_id, str):
                        scan.execution_session_id = record_session_id
            elif scan.execution_session_meta_seen:
                # Forked logs prepend the parent's session metadata and counters.
                scan.has_embedded_parent_history = True
            stamp = payload.get("timestamp") or item.get("timestamp")
            if isinstance(stamp, str):
                try:
                    record_started_at = datetime.fromisoformat(
                        stamp.replace("Z", "+00:00")
                    )
                    if scan.started_at is None:
                        scan.started_at = record_started_at
                    if record_is_subagent and scan.subagent_started_at is None:
                        scan.subagent_started_at = record_started_at
                except ValueError:
                    pass
        if isinstance(payload.get("model"), str):
            scan.model = payload["model"]
        if isinstance(payload.get("effort"), str):
            scan.reasoning_effort = payload["effort"]
        if item.get("type") == "turn_context":
            context_model = payload.get("model")
            context_effort = payload.get("effort")
            scan.turn_context_events.append(
                (
                    record_index,
                    context_model if isinstance(context_model, str) else "",
                    context_effort if isinstance(context_effort, str) else "",
                    scan.latest_tokens,
                )
            )
        collaboration = payload.get("collaboration_mode")
        settings = (
            collaboration.get("settings") if isinstance(collaboration, dict) else None
        )
        if isinstance(settings, dict) and isinstance(
            settings.get("reasoning_effort"), str
        ):
            scan.reasoning_effort = settings["reasoning_effort"]
        thread_settings = payload.get("thread_settings")
        if isinstance(thread_settings, dict):
            if isinstance(thread_settings.get("model"), str):
                scan.model = thread_settings["model"]
            if isinstance(thread_settings.get("reasoning_effort"), str):
                scan.reasoning_effort = thread_settings["reasoning_effort"]
        if payload.get("type") == "thread_settings_applied":
            scan.settings_events.append(
                (
                    record_index,
                    thread_settings.get("model")
                    if isinstance(thread_settings, dict)
                    and isinstance(thread_settings.get("model"), str)
                    else "",
                    thread_settings.get("reasoning_effort")
                    if isinstance(thread_settings, dict)
                    and isinstance(thread_settings.get("reasoning_effort"), str)
                    else "",
                )
            )
        if payload.get("type") == "token_count":
            info = payload.get("info")
            total = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(total, dict):
                scan.latest_tokens = total

    def _finish_scan(
        self, path: Path, scan: _SessionScan, stat: os.stat_result
    ) -> ParsedSession | None:
        latest_tokens = scan.latest_tokens
        if latest_tokens is None:
            return None
        if (
            scan.is_execution_subagent
            and scan.has_embedded_parent_history
            and scan.settings_events
        ):
            settings_index, settings_model, settings_effort = scan.settings_events[-1]
            execution_turn = next(
                (
                    item
                    for item in scan.turn_context_events
                    if item[0] > settings_index
                    and (not settings_model or item[1] == settings_model)
                    and (not settings_effort or item[2] == settings_effort)
                ),
                None,
            )
            if execution_turn is not None:
                # The execution turn starts at the cumulative value that was
                # current just before it; everything above that is parent history.
                baseline = execution_turn[3]
                if baseline is not None:
                    latest_tokens = _cumulative_token_delta(latest_tokens, baseline)
        started_at = scan.subagent_started_at or scan.started_at
        if started_at is None:
            started_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        elif started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        return ParsedSession(
            started_at,
            scan.is_subagent,
            scan.is_execution_subagent,
            latest_tokens,
            scan.model,
            scan.reasoning_effort,
        )

    def _parse_file(self, path: Path) -> ParsedSession | None:
        """Full parse; kept for callers and tests that need a fresh read."""
        try:
            stat = path.stat()
        except OSError:
            return None
        scan = _SessionScan()
        self._scan_file(path, scan, stat)
        return self._finish_scan(path, scan, stat)

    def _scan_file(
        self, path: Path, scan: _SessionScan, stat: os.stat_result
    ) -> _SessionScan:
        resume = bool(
            scan.offset
            and scan.offset <= stat.st_size
            and _scan_identity_compatible(
                scan.fingerprint, _scan_identity(path, scan.offset)
            )
        )
        if not resume:
            scan.reset()
        start = scan.offset if resume else 0
        try:
            with path.open("rb") as handle:
                if start:
                    handle.seek(start)
                for raw in handle:
                    if not raw.endswith(b"\n"):
                        # A partially written trailing record is parsed again on
                        # the next refresh once the writer finishes the line.
                        break
                    start += len(raw)
                    self.scanned_bytes += len(raw)
                    if (
                        b'"token_count"' not in raw
                        and b'"session_meta"' not in raw
                        and b'"turn_context"' not in raw
                        and b'"thread_settings' not in raw
                        and b'"model"' not in raw
                        and b'"effort"' not in raw
                        and b'"collaboration_mode"' not in raw
                    ):
                        continue
                    try:
                        item = json.loads(raw)
                    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                        continue
                    self._apply_record(scan, item)
        except OSError:
            return scan
        scan.offset = start
        scan.size = stat.st_size
        scan.mtime_ns = stat.st_mtime_ns
        scan.fingerprint = _scan_identity(path, start)
        return scan

    def _get_session(self, path: Path) -> ParsedSession | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        self._load_persistent_cache()
        cached = self._cache.get(path)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        scan = self._scans.get(path)
        if scan is None:
            scan = _SessionScan()
        self._scan_file(path, scan, stat)
        self._scans[path] = scan
        parsed = self._finish_scan(path, scan, stat)
        self._cache[path] = (stat.st_mtime_ns, stat.st_size, parsed)
        self._cache_dirty = True
        return parsed

    def _load_persistent_cache(self) -> None:
        if self._cache_loaded:
            return
        self._cache_loaded = True
        if self.cache_path is None or not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or data.get("version") != STATS_CACHE_VERSION:
            return
        entries = data.get("scans")
        if not isinstance(entries, dict):
            return
        for raw_path, entry in entries.items():
            scan = _SessionScan.from_json(entry)
            if scan is None:
                continue
            self._scans[Path(raw_path)] = scan

    def save_persistent_cache(self, force: bool = False) -> None:
        """Persist the incremental scan state; failures are non-fatal.

        A live rollout log changes every second, so writes are throttled to keep
        the refresh loop from turning into a disk-write loop.
        """
        if self.cache_path is None or not self._cache_dirty:
            return
        now = time.monotonic()
        if not force and now - self._last_save < STATS_CACHE_SAVE_INTERVAL:
            return
        payload = {
            "version": STATS_CACHE_VERSION,
            "scans": {
                str(path): scan.to_json() for path, scan in self._scans.items()
            },
        }
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(temporary, self.cache_path)
            self._cache_dirty = False
            self._last_save = now
        except OSError:
            return

    def aggregate(
        self,
        days: int | None = 7,
        *,
        calendar_day: bool = False,
        now: datetime | None = None,
        progress: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> tuple[Usage, Usage]:
        main = Usage()
        subagent = Usage()
        if not self.sessions_dir.exists():
            return main, subagent
        if now is None:
            local_now = datetime.now().astimezone()
        elif now.tzinfo is None:
            local_now = now.replace(tzinfo=timezone.utc).astimezone()
        else:
            local_now = now
        if calendar_day:
            cutoff = local_now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).astimezone(timezone.utc)
        elif days is not None:
            cutoff = local_now.astimezone(timezone.utc) - timedelta(days=days)
        else:
            cutoff = None
        paths = list(self.sessions_dir.rglob("*.jsonl"))
        live_paths = set(paths)
        for stale in set(self._cache) - live_paths:
            del self._cache[stale]
            self._scans.pop(stale, None)
            self._cache_dirty = True
        total = len(paths)
        for index, path in enumerate(paths, start=1):
            if should_stop is not None and should_stop():
                break
            parsed = self._get_session(path)
            if parsed is not None and not (cutoff and parsed.started_at < cutoff):
                if not parsed.is_subagent or parsed.is_execution_subagent:
                    (subagent if parsed.is_execution_subagent else main).add(
                        parsed.tokens,
                        parsed.model,
                        parsed.reasoning_effort,
                        parsed.started_at,
                    )
            if progress is not None and index % 25 == 0:
                progress(index, total)
        self.save_persistent_cache()
        return main, subagent

def _fmt_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return str(value)


class CodexAgentConsole:
    def __init__(self, root: tk.Tk, home: Path | None = None):
        self.root = root
        self.home = home or codex_home()
        self.config_store = ConfigStore(self.home / "config.toml")
        self.desktop_backend_reloader = DesktopBackendReloader()
        self.custom_api_store = CustomApiStore(self.home / CUSTOM_API_CONFIG_NAME)
        self.codex_models = load_model_catalog(self.home)
        self.model_priority = load_model_priorities(self.home)
        self.model_efforts: dict[str, tuple[str, ...]] = dict(MODEL_EFFORTS)
        self.model_efforts.update(self.codex_models)
        self.available_models: tuple[str, ...] = tuple(
            dict.fromkeys((*self.codex_models, *MODELS))
        )
        self.stats_reader = SessionStatsReader(
            self.home / "sessions",
            cache_path=self.home / STATS_CACHE_NAME,
        )
        self._refresh_running = False
        self._refresh_generation = 0
        self._auto_refresh_job: str | None = None
        self._ui_queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._ui_pump_job: str | None = None
        self._diagnostic_window: tk.Toplevel | None = None
        self._diagnostic_runner: CodexDiagnosticsRunner | None = None
        self._diagnostic_generation = 0
        self._diagnostic_results: list[DiagnosticResult] = []
        self._diagnostic_errors: dict[str, str] = {}
        self._custom_api_window: tk.Toplevel | None = None
        self._custom_api_runner: CustomApiBenchmarkRunner | None = None
        self._custom_api_generation = 0
        self._custom_api_endpoints: list[CustomApiEndpoint] = []
        self._custom_api_results: list[CustomApiResult] = []
        self._custom_api_error_details: dict[str, str] = {}
        self.gateway_config_path = self.home / "codex-agent-console-gateway.json"
        self.gateway_config = GatewayConfig.load(self.gateway_config_path)
        self._gateway: CodexGateway | None = None
        self._gateway_window: tk.Toplevel | None = None
        self._gateway_poll_job: str | None = None
        self.advisor_store = AdvisorStore.for_home(self.home)
        self.advisor_config = self.advisor_store.load_config()
        self._advisor_window: tk.Toplevel | None = None
        self.resume_service = QuotaResumeService(self.home)
        self._resume_window = ResumeWindow(root, self.resume_service)

        root.title(f"{APP_NAME} {APP_VERSION}")
        root.configure(bg="#0f1216")
        root.protocol("WM_DELETE_WINDOW", self._close_app)
        self._configure_style()
        self._build_ui()
        self._apply_window_size()
        self._load_settings()
        self.refresh_stats()
        self._auto_refresh_job = self.root.after(
            AUTO_REFRESH_MS, self._auto_refresh
        )
        self._start_ui_pump()
        self._autostart_gateway()
        self.resume_service.start()
        root.bind("<Map>", self._on_window_mapped, add="+")

    def _apply_window_size(self) -> None:
        """Fit the initial window and the minimum size to the real screen.

        The previous build hard-coded 1040x720 while the content requested
        1116x592, so the action buttons were silently clipped at the minimum
        size.  Sizing from the measured request keeps every control readable.
        """
        root = self.root
        root.update_idletasks()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        request_w = root.winfo_reqwidth()
        request_h = root.winfo_reqheight()
        width = min(max(1120, request_w + 8), max(880, screen_w - 120))
        height = min(max(760, request_h + 8), max(600, screen_h - 120))
        left = max(0, (screen_w - width) // 2)
        top = max(0, (screen_h - height) // 2 - 24)
        root.geometry(f"{width}x{height}+{left}+{top}")
        root.minsize(
            min(max(900, request_w + 8), max(860, screen_w - 120)),
            min(max(620, request_h + 8), max(580, screen_h - 120)),
        )

    # ---------------------------------------------------------------- styling
    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        palette = {
            "bg": "#0b0e12",
            "surface": "#12161b",
            "card": "#171d24",
            "input": "#0e1319",
            "border": "#28313c",
            "border_soft": "#1e252e",
            "text": "#e6edf3",
            "muted": "#8b96a3",
            "accent": "#3b82f6",
            "accent_hover": "#5b9bff",
            "accent_press": "#2f6fd0",
            "good": "#3ddc97",
        }
        self.palette = palette
        base_font = ("Segoe UI", 10)
        small_font = ("Segoe UI", 9)
        bold_font = ("Segoe UI Semibold", 10)
        style.configure(
            ".", background=palette["surface"], foreground=palette["text"],
            font=base_font, bordercolor=palette["border"],
            lightcolor=palette["surface"], darkcolor=palette["surface"],
        )
        style.configure("TFrame", background=palette["bg"])
        style.configure("Surface.TFrame", background=palette["surface"])
        style.configure("Card.TFrame", background=palette["card"])
        style.configure(
            "TLabel", background=palette["bg"], foreground=palette["text"],
            font=base_font,
        )
        style.configure(
            "Muted.TLabel", background=palette["bg"], foreground=palette["muted"],
            font=small_font,
        )
        style.configure(
            "Title.TLabel", background=palette["bg"], foreground="#ffffff",
            font=("Segoe UI Semibold", 17),
        )
        style.configure(
            "Subtitle.TLabel", background=palette["bg"], foreground=palette["muted"],
            font=small_font,
        )
        style.configure(
            "Section.TLabel", background=palette["surface"], foreground="#ffffff",
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "FieldLabel.TLabel", background=palette["surface"],
            foreground=palette["muted"], font=small_font,
        )
        style.configure(
            "SurfaceMuted.TLabel", background=palette["surface"],
            foreground=palette["muted"], font=small_font,
        )
        style.configure(
            "CardTitle.TLabel", background=palette["card"], foreground="#ffffff",
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "CardMuted.TLabel", background=palette["card"],
            foreground=palette["muted"], font=small_font,
        )
        style.configure(
            "Kpi.TLabel", background=palette["card"], foreground="#ffffff",
            font=("Segoe UI Semibold", 17),
        )
        style.configure(
            "KpiValue.TLabel", background=palette["card"], foreground=palette["text"],
            font=("Segoe UI Semibold", 11),
        )
        style.configure(
            "Accent.TLabel", background=palette["card"], foreground=palette["accent"],
            font=("Segoe UI Semibold", 11),
        )
        # Names still used by the diagnostics and custom-API windows.
        style.configure("Panel.TFrame", background=palette["card"])
        style.configure(
            "PanelMuted.TLabel", background=palette["card"],
            foreground=palette["muted"], font=small_font,
        )
        style.configure(
            "Value.TLabel", background=palette["card"], foreground=palette["accent"],
            font=("Segoe UI Semibold", 14),
        )
        style.configure(
            "Panel.TCheckbutton", background=palette["card"],
            foreground=palette["text"], font=base_font,
        )
        style.map(
            "Panel.TCheckbutton",
            background=[("active", palette["card"])],
            foreground=[("disabled", "#5f6b78")],
            indicatorcolor=[("selected", palette["accent"])],
        )

        # ---- buttons
        for name, bg, fg, border in (
            ("TButton", palette["card"], palette["text"], palette["border"]),
            ("Ghost.TButton", palette["surface"], palette["muted"], "#2a333e"),
            ("Primary.TButton", palette["accent"], "#ffffff", palette["accent"]),
        ):
            style.configure(
                name, padding=(12, 6), font=bold_font, background=bg, foreground=fg,
                bordercolor=border, lightcolor=bg, darkcolor=bg, focuscolor=bg,
                relief="flat",
            )
        style.map(
            "TButton",
            background=[("disabled", "#161c23"), ("pressed", "#222c38"), ("active", "#1e2733")],
            foreground=[("disabled", "#5c6774")],
            bordercolor=[("active", palette["border"])],
        )
        style.map(
            "Ghost.TButton",
            background=[("disabled", "#0d1116"), ("pressed", "#161c23"), ("active", "#151b22")],
            foreground=[("disabled", "#48525e"), ("active", palette["text"])],
        )
        style.map(
            "Primary.TButton",
            background=[
                ("disabled", "#24384f"),
                ("pressed", palette["accent_press"]),
                ("active", palette["accent_hover"]),
            ],
            foreground=[("disabled", "#93a4b8")],
            bordercolor=[("active", palette["accent_hover"])],
        )

        # ---- inputs
        style.configure(
            "TCombobox", fieldbackground=palette["input"], background=palette["card"],
            foreground="#ffffff", arrowcolor=palette["muted"],
            bordercolor=palette["border"], lightcolor=palette["input"],
            darkcolor=palette["input"], padding=(8, 5), font=base_font,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["input"]), ("disabled", "#0c1014")],
            foreground=[("readonly", "#ffffff"), ("disabled", "#5c6774")],
            selectbackground=[("readonly", palette["input"])],
            selectforeground=[("readonly", "#ffffff")],
            bordercolor=[("focus", palette["accent"])],
            lightcolor=[("focus", palette["input"])],
            darkcolor=[("focus", palette["input"])],
        )
        style.configure(
            "TEntry", fieldbackground=palette["input"], foreground="#ffffff",
            insertcolor="#ffffff", bordercolor=palette["border"],
            lightcolor=palette["input"], darkcolor=palette["input"],
            padding=(8, 5), font=base_font,
        )
        style.map(
            "TEntry",
            fieldbackground=[("disabled", "#0c1014")],
            foreground=[("disabled", "#5c6774")],
            bordercolor=[("focus", palette["accent"])],
        )
        style.configure(
            "TSpinbox", fieldbackground=palette["input"], foreground="#ffffff",
            arrowcolor=palette["muted"], bordercolor=palette["border"],
            lightcolor=palette["input"], darkcolor=palette["input"],
            padding=(6, 4), font=base_font,
        )
        style.map(
            "TSpinbox",
            fieldbackground=[("disabled", "#0c1014")],
            foreground=[("disabled", "#5c6774")],
            bordercolor=[("focus", palette["accent"])],
        )
        style.configure(
            "Switch.TCheckbutton", background=palette["surface"],
            foreground=palette["text"], font=bold_font, focuscolor=palette["surface"],
        )
        style.map(
            "Switch.TCheckbutton",
            background=[("active", palette["surface"])],
            indicatorcolor=[("selected", palette["accent"]), ("!selected", palette["border"])],
            foreground=[("disabled", "#5c6774")],
        )

        # ---- data views
        style.configure("TSeparator", background=palette["border_soft"])
        style.configure(
            "Treeview", background=palette["input"], fieldbackground=palette["input"],
            foreground=palette["text"], borderwidth=0, rowheight=25, font=base_font,
        )
        style.configure(
            "Treeview.Heading", background=palette["card"], foreground=palette["muted"],
            font=("Segoe UI Semibold", 9), relief="flat", borderwidth=0,
        )
        style.map(
            "Treeview", background=[("selected", "#1f3552")], foreground=[("selected", "#ffffff")]
        )
        style.map("Treeview.Heading", background=[("active", palette["card"])])
        style.configure(
            "Vertical.TScrollbar", background=palette["card"], troughcolor=palette["input"],
            bordercolor=palette["input"], arrowcolor=palette["muted"],
            lightcolor=palette["card"], darkcolor=palette["card"], width=10,
        )
        style.map(
            "Vertical.TScrollbar", background=[("active", "#2a3441")]
        )
        style.configure(
            "Horizontal.TScrollbar", background=palette["card"],
            troughcolor=palette["input"], bordercolor=palette["input"],
            arrowcolor=palette["muted"], lightcolor=palette["card"],
            darkcolor=palette["card"],
        )

    # ------------------------------------------------------------- main window
    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, padding=(22, 16, 22, 18))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        title_block = ttk.Frame(header)
        title_block.grid(row=0, column=0, rowspan=2, sticky="w")
        title_line = ttk.Frame(title_block)
        title_line.pack(anchor="w")
        ttk.Label(title_line, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(
            title_line, text=f" {APP_VERSION} ", style="Muted.TLabel"
        ).pack(side="left", padx=(6, 0))
        self.subtitle_var = tk.StringVar(value="运行设置 · Token 统计")
        ttk.Label(
            title_block, textvariable=self.subtitle_var, style="Subtitle.TLabel"
        ).pack(anchor="w", pady=(3, 0))

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(header, textvariable=self.status_var, style="Muted.TLabel").grid(
            row=0, column=1, sticky="e"
        )
        self._build_toolbar(header, row=1, column=1)

        body = ttk.Frame(shell)
        body.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        body.columnconfigure(0, weight=0, minsize=372)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self.settings_panel = ttk.Frame(body, style="Surface.TFrame", padding=18)
        self.settings_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        self.stats_panel = ttk.Frame(body, style="Surface.TFrame", padding=18)
        self.stats_panel.grid(row=0, column=1, sticky="nsew")
        self._build_settings(self.settings_panel)
        self._build_stats(self.stats_panel)

    def _build_toolbar(self, parent: ttk.Frame, row: int, column: int) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=row, column=column, sticky="e", pady=(6, 0))
        self.toolbar_buttons: list[ttk.Button] = []
        for text, command in (
            ("额度续跑", self._resume_window.show),
            ("接入 Codex", self.open_advisor),
            ("接入 DSH", self.open_gateway),
            ("API 诊断", self.open_diagnostics),
            ("自定义 API", self.open_custom_api_benchmark),
            ("会话目录", self.open_sessions),
            ("配置文件", self.open_config),
        ):
            button = ttk.Button(toolbar, text=text, command=command, style="Ghost.TButton")
            button.pack(side="left", padx=(6, 0))
            self.toolbar_buttons.append(button)

    def _build_settings(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(12, weight=1)

        ttk.Label(parent, text="运行设置", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )

        self.mode_var = tk.StringVar(value="multi")
        self.dual_mode_var = tk.BooleanVar(value=True)
        mode_switch = ttk.Frame(parent, style="Surface.TFrame")
        mode_switch.grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.mode_multi_button = ttk.Button(
            mode_switch, text="双模型协作", width=10,
            command=lambda: self._set_mode(True),
        )
        self.mode_multi_button.pack(side="left")
        self.mode_single_button = ttk.Button(
            mode_switch, text="单代理", width=10,
            command=lambda: self._set_mode(False),
        )
        self.mode_single_button.pack(side="left", padx=(6, 0))

        ttk.Label(
            parent,
            text=(
                "这一页只配置 Codex 自己的模型；"
                "DeepSeek 顾问在“接入 Codex”里单独配置，两者互不影响。"
            ),
            style="FieldLabel.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        ttk.Separator(parent, orient="horizontal").grid(
            row=3, column=0, sticky="ew", pady=(14, 12)
        )

        self.main_model_var = tk.StringVar()
        self.main_effort_var = tk.StringVar()
        self.sub_model_var = tk.StringVar()
        self.sub_effort_var = tk.StringVar()
        self.max_threads_var = tk.IntVar(value=4)
        self.role_fields: list[dict[str, object]] = []

        ttk.Label(parent, text="主模型（执行）", style="FieldLabel.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        self.main_role_frame = ttk.Frame(parent, style="Surface.TFrame")
        self.main_role_frame.grid(row=5, column=0, sticky="ew", pady=(4, 12))
        self.main_role_frame.columnconfigure(0, weight=1)
        self._build_role_fields(
            self.main_role_frame,
            self.main_model_var,
            self.main_effort_var,
        )

        ttk.Label(parent, text="Codex 子代理（规划）", style="FieldLabel.TLabel").grid(
            row=6, column=0, sticky="w"
        )
        self.sub_role_frame = ttk.Frame(parent, style="Surface.TFrame")
        self.sub_role_frame.grid(row=7, column=0, sticky="ew", pady=(4, 0))
        self.sub_role_frame.columnconfigure(0, weight=1)
        self._build_role_fields(
            self.sub_role_frame,
            self.sub_model_var,
            self.sub_effort_var,
        )

        details = ttk.Frame(parent, style="Surface.TFrame")
        details.grid(row=8, column=0, sticky="ew", pady=(14, 0))
        ttk.Label(details, text="并发子代理", style="FieldLabel.TLabel").pack(
            side="left", padx=(0, 10)
        )
        ttk.Spinbox(
            details, from_=1, to=16, textvariable=self.max_threads_var, width=5
        ).pack(side="left")
        ttk.Label(details, text="1–16", style="FieldLabel.TLabel").pack(
            side="left", padx=(8, 0)
        )

        self.role_hint_var = tk.StringVar(value="")
        ttk.Label(
            parent,
            textvariable=self.role_hint_var,
            style="SurfaceMuted.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=9, column=0, sticky="w", pady=(14, 0))

        actions = ttk.Frame(parent, style="Surface.TFrame")
        actions.grid(row=10, column=0, sticky="ew", pady=(18, 0))
        self.save_apply_button = ttk.Button(
            actions, text="保存并应用", style="Primary.TButton",
            command=self.apply_to_desktop,
        )
        self.save_apply_button.pack(side="left")
        self.save_button = ttk.Button(
            actions, text="仅保存", command=self.save_settings
        )
        self.save_button.pack(side="left", padx=(8, 0))

        self.hint_var = tk.StringVar(
            value="保存并应用会重启 Desktop 后台，运行中的任务会中断。"
        )
        ttk.Label(
            parent,
            textvariable=self.hint_var,
            style="SurfaceMuted.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=11, column=0, sticky="nw", pady=(10, 0))
        ttk.Frame(parent, style="Surface.TFrame").grid(row=12, column=0, sticky="nsew")
        self.config_path_var = tk.StringVar(value=f"配置：{self.config_store.path}")
        ttk.Label(
            parent,
            textvariable=self.config_path_var,
            style="SurfaceMuted.TLabel",
            wraplength=330,
            justify="left",
        ).grid(row=13, column=0, sticky="sw")

    def _build_role_fields(
        self,
        parent: ttk.Frame,
        model_var: tk.StringVar,
        effort_var: tk.StringVar,
    ) -> None:
        model_combo = ttk.Combobox(
            parent, textvariable=model_var, values=self.available_models,
            state="normal",
        )
        model_combo.grid(row=0, column=0, sticky="ew")
        effort_combo = ttk.Combobox(
            parent, textvariable=effort_var, values=self._efforts_for(model_var.get()),
            state="normal", width=10,
        )
        effort_combo.grid(row=0, column=1, sticky="e", padx=(8, 0))
        model_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: (
                self._sync_effort_values(model_var, effort_var, effort_combo),
                self._update_role_hint(),
            ),
        )
        effort_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._update_role_hint()
        )
        self.role_fields.append(
            {
                "model_var": model_var,
                "effort_var": effort_var,
                "model_combo": model_combo,
                "effort_combo": effort_combo,
            }
        )

    def _refresh_role_fields(self) -> None:
        """Keep the pickers in sync with the loaded config and the model catalog."""
        extra: list[str] = []
        for field in getattr(self, "role_fields", []):
            model = str(field["model_var"].get()).strip()  # type: ignore[attr-defined]
            if model and model not in self.available_models:
                extra.append(model)
        if extra:
            self.available_models = tuple(
                dict.fromkeys((*self.available_models, *extra))
            )
        for field in getattr(self, "role_fields", []):
            model_combo = field["model_combo"]
            effort_combo = field["effort_combo"]
            model_combo.configure(values=self.available_models)  # type: ignore[attr-defined]
            model = str(field["model_var"].get()).strip()  # type: ignore[attr-defined]
            levels = list(self._efforts_for(model))
            effort = str(field["effort_var"].get()).strip()  # type: ignore[attr-defined]
            if effort and effort not in levels:
                levels.insert(0, effort)
            effort_combo.configure(values=tuple(levels))  # type: ignore[attr-defined]
        self._update_role_hint()

    def _efforts_for(self, model: str) -> tuple[str, ...]:
        levels = self.model_efforts.get(model.strip())
        if levels:
            return levels
        return EFFORTS

    def _sync_effort_values(
        self,
        model_var: tk.StringVar,
        effort_var: tk.StringVar,
        effort_combo: ttk.Combobox,
    ) -> None:
        levels = self._efforts_for(model_var.get())
        effort_combo.configure(values=levels)
        if effort_var.get().strip() not in levels:
            effort_var.set(levels[0])

    def _set_mode(self, enabled: bool) -> None:
        self.dual_mode_var.set(enabled)
        self.mode_var.set("multi" if enabled else "single")
        for field in getattr(self, "role_fields", []):
            if field["model_var"] is not self.sub_model_var:
                continue
            for key in ("model_combo", "effort_combo"):
                widget = field[key]
                widget.configure(state="normal" if enabled else "disabled")  # type: ignore[attr-defined]
        self._sync_mode_buttons()
        self._update_role_hint()

    def _sync_mode_buttons(self) -> None:
        if not hasattr(self, "mode_multi_button"):
            return
        enabled = bool(self.dual_mode_var.get())
        self.mode_multi_button.configure(
            style="Primary.TButton" if enabled else "Ghost.TButton"
        )
        self.mode_single_button.configure(
            style="Ghost.TButton" if enabled else "Primary.TButton"
        )

    def _update_role_hint(self) -> None:
        if not hasattr(self, "role_hint_var"):
            return
        if not self.dual_mode_var.get():
            self.role_hint_var.set("单代理模式：全部工作由主模型完成。")
            return
        main_model = self.main_model_var.get().strip()
        sub_model = self.sub_model_var.get().strip()
        main_rank = self.model_priority.get(main_model)
        sub_rank = self.model_priority.get(sub_model)
        if main_model and main_model == sub_model:
            self.role_hint_var.set("提示：主模型与子模型相同，建议给子模型换更高阶型号。")
        elif main_rank is not None and sub_rank is not None and sub_rank >= main_rank:
            self.role_hint_var.set(
                f"提示：{sub_model} 的档位不低于 {main_model}，规划优势不明显。"
            )
        else:
            self.role_hint_var.set(
                f"规划用 {sub_model}，执行用 {main_model}；失败两次或需取舍时再问子模型。"
            )

    # ------------------------------------------------------------------ stats
    def _build_stats(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)

        bar = ttk.Frame(parent, style="Surface.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(1, weight=1)
        ttk.Label(bar, text="Token 统计", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.period_var = tk.StringVar(value="7天")
        self.period_buttons: list[ttk.Button] = []
        period_bar = ttk.Frame(bar, style="Surface.TFrame")
        period_bar.grid(row=0, column=2, sticky="e")
        for label in ("今天", "7天", "30天", "全部"):
            button = ttk.Button(
                period_bar,
                text=label,
                style="Ghost.TButton",
                width=5,
                command=lambda value=label: self._select_period(value),
            )
            button.pack(side="left", padx=(4, 0))
            self.period_buttons.append(button)
        self.refresh_button = ttk.Button(
            bar, text="刷新", command=lambda: self.refresh_stats(force=True)
        )
        self.refresh_button.grid(row=0, column=3, sticky="e", padx=(8, 0))

        self.updated_var = tk.StringVar(value="尚未刷新")
        ttk.Label(
            parent, textvariable=self.updated_var, style="SurfaceMuted.TLabel"
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        blocks = ttk.Frame(parent, style="Surface.TFrame")
        blocks.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        blocks.columnconfigure(0, weight=1, uniform="usage")
        blocks.columnconfigure(1, weight=1, uniform="usage")
        self.main_stats_vars = self._build_usage_block(blocks, 0, "主模型")
        self.sub_stats_vars = self._build_usage_block(blocks, 1, "子模型")

        ttk.Label(
            parent, text="模型分布", style="Section.TLabel"
        ).grid(row=3, column=0, sticky="w", pady=(12, 6))

        table_frame = ttk.Frame(parent, style="Surface.TFrame")
        table_frame.grid(row=4, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.model_tree = ttk.Treeview(
            table_frame,
            columns=("role", "model", "sessions", "share"),
            show="headings",
            selectmode="none",
            height=5,
        )
        for name, text, width, anchor in (
            ("role", "来源", 90, "w"),
            ("model", "模型 / 思考级别", 260, "w"),
            ("sessions", "会话", 70, "center"),
            ("share", "占比", 70, "center"),
        ):
            self.model_tree.heading(name, text=text)
            self.model_tree.column(
                name, width=width, minwidth=50, anchor=anchor,
                stretch=name == "model",
            )
        self.model_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.model_tree.yview
        )
        scroll.grid(row=0, column=1, sticky="ns", padx=(2, 0))
        self.model_tree.configure(yscrollcommand=scroll.set)

    def _select_period(self, value: str, refresh: bool = True) -> None:
        self.period_var.set(value)
        for button, label in zip(self.period_buttons, ("今天", "7天", "30天", "全部")):
            button.configure(
                style="Primary.TButton" if label == value else "Ghost.TButton"
            )
        if refresh:
            self.refresh_stats(force=True)

    def _build_usage_block(
        self, parent: ttk.Frame, column: int, title: str
    ) -> dict[str, tk.StringVar]:
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.grid(
            row=0, column=column, sticky="nsew",
            padx=(0, 6) if column == 0 else (6, 0),
        )
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)
        vars_ = {
            "total": tk.StringVar(value="0"),
            "input": tk.StringVar(value="0"),
            "cached": tk.StringVar(value="0"),
            "output": tk.StringVar(value="0"),
            "reasoning": tk.StringVar(value="0"),
            "hit": tk.StringVar(value="0%"),
            "sessions": tk.StringVar(value="0 个会话"),
            "actual": tk.StringVar(value="尚无记录"),
        }
        header = ttk.Frame(card, style="Card.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=title, style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            header, textvariable=vars_["sessions"], style="CardMuted.TLabel"
        ).grid(row=0, column=1, sticky="e")
        ttk.Label(
            card, textvariable=vars_["total"], style="Kpi.TLabel"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(card, text="总 Token", style="CardMuted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        metrics = (
            ("输入", "input"),
            ("缓存输入", "cached"),
            ("输出", "output"),
            ("推理输出", "reasoning"),
        )
        for index, (label, key) in enumerate(metrics):
            row = 3 + index // 2
            col = index % 2
            cell = ttk.Frame(card, style="Card.TFrame")
            cell.grid(row=row, column=col, sticky="ew", pady=(0, 6))
            ttk.Label(cell, text=label, style="CardMuted.TLabel").pack(anchor="w")
            ttk.Label(
                cell, textvariable=vars_[key], style="KpiValue.TLabel"
            ).pack(anchor="w", pady=(1, 0))
        ttk.Label(card, text="缓存命中率", style="CardMuted.TLabel").grid(
            row=5, column=0, sticky="w", pady=(2, 0)
        )
        ttk.Label(
            card, textvariable=vars_["hit"], style="Accent.TLabel"
        ).grid(row=5, column=1, sticky="w", pady=(2, 0))
        ttk.Label(
            card, textvariable=vars_["actual"], style="CardMuted.TLabel",
            wraplength=260, justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        return vars_

    def _load_settings(self) -> None:
        try:
            settings = self.config_store.load()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法读取配置：{exc}")
            return
        self.main_model_var.set(settings.main_model)
        self.main_effort_var.set(settings.main_effort)
        self.sub_model_var.set(settings.subagent_model)
        self.sub_effort_var.set(settings.subagent_effort)
        self.max_threads_var.set(settings.max_threads)
        self.mode_var.set("multi" if settings.agents_enabled else "single")
        self.dual_mode_var.set(settings.agents_enabled)
        self._refresh_role_fields()
        self._set_mode(settings.agents_enabled)
        self._select_period("7天", refresh=False)

    def _current_settings(self) -> AgentSettings:
        main_model = self.main_model_var.get().strip()
        sub_model = self.sub_model_var.get().strip()
        main_effort = self.main_effort_var.get().strip()
        sub_effort = self.sub_effort_var.get().strip()
        if not main_model or not sub_model:
            raise ValueError("主模型和子代理模型不能为空")
        for model, effort, role in (
            (main_model, main_effort, "主模型"),
            (sub_model, sub_effort, "子代理"),
        ):
            allowed = self._efforts_for(model)
            if effort not in allowed:
                raise ValueError(f"{role} {model} 不支持 {effort} 思考程度")
        threads = int(self.max_threads_var.get())
        if not 1 <= threads <= 16:
            raise ValueError("并发子代理必须在 1 到 16 之间")
        return AgentSettings(
            main_model,
            main_effort,
            sub_model,
            sub_effort,
            self.mode_var.get() == "multi",
            threads,
        )

    def save_settings(self) -> bool:
        try:
            settings = self._current_settings()
            self.config_store.save(settings)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"保存失败：{exc}")
            return False
        mode = "双模型协作" if settings.agents_enabled else "单代理"
        self.status_var.set(f"已保存 · {mode} · 新任务生效")
        return True

    def apply_to_desktop(self) -> None:
        if not self.save_settings():
            return
        try:
            backends = self.desktop_backend_reloader.find()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法检测 Desktop 后端：{exc}")
            return
        if not backends:
            self.status_var.set("已保存 · 未检测到 Desktop 后端 · 下次启动时加载")
            messagebox.showinfo(
                APP_NAME,
                "未检测到正在运行的 Codex Desktop 后端。\n\n设置已保存，将在下次启动 Desktop 或新建任务时加载。",
            )
            return
        if not messagebox.askyesno(
            APP_NAME,
            "将只重载 Codex Desktop 的后台 app-server，不会关闭 Desktop 窗口。\n\n"
            "正在运行的任务会被中断，已有任务不会热切换。确认立即应用到后续新任务？",
            icon="warning",
        ):
            self.status_var.set("已保存 · Desktop 后端未重载")
            return
        result = self.desktop_backend_reloader.reload(backends)
        if result.status == "reloaded":
            details = f"\n\n部分后端未能重载：\n{result.error}" if result.error else ""
            self.status_var.set("已应用 · 新任务使用当前设置")
            messagebox.showinfo(
                APP_NAME,
                "Desktop 后端已重载，窗口保持打开。\n\n"
                "请新建任务使用当前模型与双模型策略；已有任务保持原设置。"
                + details,
            )
            return
        if result.status == "unsupported":
            messagebox.showinfo(
                APP_NAME,
                "当前系统不支持 Desktop 后端重载。设置已保存，将在下次启动时加载。",
            )
            return
        messagebox.showerror(
            APP_NAME,
            f"Desktop 后端未能重载：{result.error or '未知错误'}",
        )

    # ------------------------------------------------------------ stats refresh
    def _period_days(self) -> int | None:
        return {"今天": 1, "7天": 7, "30天": 30, "全部": None}.get(
            self.period_var.get(), 7
        )

    def refresh_stats(self, force: bool = False) -> None:
        """Kick off a background aggregation; never blocks the event loop.

        ``force`` invalidates any in-flight run so a period switch or a manual
        refresh always wins over a slower automatic one.
        """
        if self._refresh_running and not force:
            return
        if force:
            self._refresh_generation += 1
            self.status_var.set("正在统计…")
        self._refresh_running = True
        generation = self._refresh_generation
        days = self._period_days()
        calendar_day = self.period_var.get() == "今天"
        self._refresh_button_busy(True)
        reader = self.stats_reader

        def worker() -> None:
            try:
                result = reader.aggregate(days, calendar_day=calendar_day)
            except Exception as exc:  # pragma: no cover - defensive
                message = str(exc)
                self._post(lambda: self._refresh_failed(message, generation))
                return
            self._post(lambda: self._apply_stats(*result, generation=generation))

        threading.Thread(target=worker, daemon=True).start()

    def _post(self, callback: Callable[[], None]) -> None:
        """Queue UI work from a worker thread.

        Tkinter must only be touched by the main thread; calling ``after`` from a
        worker happens to work while ``mainloop`` runs but raises or serializes
        under load, which is why the UI used to stutter during long refreshes.
        """
        self._ui_queue.put(callback)

    def _start_ui_pump(self) -> None:
        self._ui_pump_job = self.root.after(UI_PUMP_MS, self._pump_ui)

    def _pump_ui(self) -> None:
        self._ui_pump_job = None
        for _ in range(200):
            try:
                callback = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:  # pragma: no cover - keep the pump alive
                continue
        try:
            delay = UI_PUMP_MS if self.root.state() == "normal" else 500
            self._ui_pump_job = self.root.after(delay, self._pump_ui)
        except tk.TclError:
            self._ui_pump_job = None

    def _refresh_button_busy(self, busy: bool) -> None:
        button = getattr(self, "refresh_button", None)
        if button is None:
            return
        try:
            button.configure(state="disabled" if busy else "normal")
        except tk.TclError:
            pass

    def _refresh_failed(self, message: str, generation: int) -> None:
        if generation == self._refresh_generation:
            self.status_var.set(f"统计失败：{message}")
        self._end_refresh(generation)

    def _end_refresh(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._refresh_generation:
            return
        self._refresh_running = False
        self._refresh_button_busy(False)

    def _apply_stats(
        self, main: Usage, subagent: Usage, *, generation: int | None = None
    ) -> None:
        if generation is not None and generation != self._refresh_generation:
            return
        self._set_usage_vars(self.main_stats_vars, main)
        self._set_usage_vars(self.sub_stats_vars, subagent)
        self._update_model_table(main, subagent)
        self.updated_var.set(time.strftime("更新于 %H:%M:%S"))
        if self.status_var.get() == "正在统计…":
            self.status_var.set("统计已更新")
        self._end_refresh(generation)

    @staticmethod
    def _set_usage_vars(vars_: dict[str, tk.StringVar], usage: Usage) -> None:
        vars_["total"].set(_fmt_tokens(usage.total_tokens))
        vars_["input"].set(_fmt_tokens(usage.input_tokens))
        vars_["cached"].set(_fmt_tokens(usage.cached_input_tokens))
        vars_["output"].set(_fmt_tokens(usage.output_tokens))
        vars_["reasoning"].set(_fmt_tokens(usage.reasoning_output_tokens))
        vars_["hit"].set(f"{usage.cache_hit_rate:.1f}%")
        vars_["sessions"].set(f"{usage.sessions} 个会话")
        model_items = sorted(usage.models.items(), key=lambda item: (-item[1], item[0]))
        if not model_items:
            vars_["actual"].set("实际模型：尚无记录")
        else:
            latest = usage.latest_model or model_items[0][0]
            vars_["actual"].set(f"最新使用：{latest}")

    def _update_model_table(self, main: Usage, subagent: Usage) -> None:
        tree = getattr(self, "model_tree", None)
        if tree is None:
            return
        for item in tree.get_children():
            tree.delete(item)
        rows: list[tuple[str, str, int]] = []
        for role, usage in (("主模型", main), ("子模型", subagent)):
            for model, count in usage.models.items():
                rows.append((role, model, count))
        if not rows:
            return
        total = sum(count for _role, _model, count in rows) or 1
        for role, model, count in sorted(rows, key=lambda row: (-row[2], row[1])):
            tree.insert(
                "",
                "end",
                values=(role, model, count, f"{count * 100.0 / total:.1f}%"),
            )

    def open_diagnostics(self) -> None:
        if (
            self._diagnostic_window is not None
            and self._diagnostic_window.winfo_exists()
        ):
            self._diagnostic_window.deiconify()
            self._diagnostic_window.lift()
            self._diagnostic_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self._diagnostic_window = window
        window.title(f"API 诊断 · {APP_NAME} {APP_VERSION}")
        window.geometry("1240x700")
        window.minsize(940, 560)
        window.configure(bg="#111418")
        window.protocol("WM_DELETE_WINDOW", self._close_diagnostics)

        shell = ttk.Frame(window, padding=16)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(3, weight=1)

        controls = ttk.Frame(shell)
        controls.grid(row=0, column=0, sticky="ew")
        self.diag_target_var = tk.StringVar(value="主模型和子代理")
        self.diag_attempts_var = tk.StringVar(value="1")
        self.diag_timeout_var = tk.IntVar(value=120)

        ttk.Label(controls, text="测试目标").pack(side="left")
        self.diag_target_combo = ttk.Combobox(
            controls,
            textvariable=self.diag_target_var,
            values=("主模型", "子代理", "主模型和子代理"),
            state="readonly",
            width=16,
        )
        self.diag_target_combo.pack(side="left", padx=(8, 18))
        ttk.Label(controls, text="次数").pack(side="left")
        self.diag_attempts_combo = ttk.Combobox(
            controls,
            textvariable=self.diag_attempts_var,
            values=("1", "3", "5"),
            state="readonly",
            width=5,
        )
        self.diag_attempts_combo.pack(side="left", padx=(8, 18))
        ttk.Label(controls, text="超时（秒）").pack(side="left")
        self.diag_timeout_spin = ttk.Spinbox(
            controls,
            from_=10,
            to=600,
            textvariable=self.diag_timeout_var,
            width=7,
        )
        self.diag_timeout_spin.pack(side="left", padx=(8, 18))
        self.diag_start_button = ttk.Button(
            controls,
            text="开始测试",
            style="Primary.TButton",
            command=self._start_diagnostics,
        )
        self.diag_start_button.pack(side="left")
        self.diag_stop_button = ttk.Button(
            controls,
            text="停止",
            command=self._stop_diagnostics,
            state="disabled",
        )
        self.diag_stop_button.pack(side="left", padx=8)

        self.diag_cli_var = tk.StringVar(value="正在检测 Codex CLI…")
        ttk.Label(
            controls,
            textvariable=self.diag_cli_var,
            style="Muted.TLabel",
        ).pack(side="right")

        summary = ttk.Frame(shell, style="Panel.TFrame", padding=(14, 12))
        summary.grid(row=1, column=0, sticky="ew", pady=(14, 10))
        for column in range(4):
            summary.columnconfigure(column, weight=1)
        self.diag_summary_vars = {
            "success": tk.StringVar(value="0/0"),
            "first": tk.StringVar(value="--"),
            "total": tk.StringVar(value="--"),
            "speed": tk.StringVar(value="--"),
        }
        for column, (label, key) in enumerate(
            (
                ("连通成功率", "success"),
                ("平均首响应", "first"),
                ("平均总耗时", "total"),
                ("平均输出速度", "speed"),
            )
        ):
            block = ttk.Frame(summary, style="Panel.TFrame")
            block.grid(row=0, column=column, sticky="ew", padx=8)
            ttk.Label(block, text=label, style="PanelMuted.TLabel").pack(anchor="w")
            ttk.Label(
                block,
                textvariable=self.diag_summary_vars[key],
                style="Value.TLabel",
            ).pack(anchor="w", pady=(4, 0))

        self.diag_live_var = tk.StringVar(value="尚未开始测试")
        ttk.Label(shell, textvariable=self.diag_live_var).grid(
            row=2, column=0, sticky="ew", pady=(0, 10)
        )

        table_frame = ttk.Frame(shell)
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = (
            "role",
            "model",
            "effort",
            "attempt",
            "status",
            "first",
            "total",
            "cleanup",
            "input",
            "cached",
            "output",
            "speed",
            "error",
        )
        self.diag_tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "role": "目标",
            "model": "模型",
            "effort": "思考",
            "attempt": "轮次",
            "status": "状态",
            "first": "首响应",
            "total": "API 总耗时",
            "cleanup": "CLI 收尾",
            "input": "输入",
            "cached": "缓存",
            "output": "输出",
            "speed": "Tokens/s",
            "error": "错误详情",
        }
        widths = {
            "role": 72,
            "model": 130,
            "effort": 64,
            "attempt": 54,
            "status": 112,
            "first": 78,
            "total": 92,
            "cleanup": 82,
            "input": 76,
            "cached": 76,
            "output": 70,
            "speed": 84,
            "error": 280,
        }
        for name in columns:
            self.diag_tree.heading(name, text=headings[name])
            self.diag_tree.column(
                name,
                width=widths[name],
                minwidth=50,
                stretch=name == "error",
                anchor="w" if name in ("model", "error") else "center",
            )
        self.diag_tree.tag_configure("success", foreground="#7ee787")
        self.diag_tree.tag_configure("failure", foreground="#ff9b9b")
        self.diag_tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(
            table_frame, orient="vertical", command=self.diag_tree.yview
        )
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(
            table_frame, orient="horizontal", command=self.diag_tree.xview
        )
        horizontal.grid(row=1, column=0, sticky="ew")
        self.diag_tree.configure(
            yscrollcommand=vertical.set,
            xscrollcommand=horizontal.set,
        )
        self.diag_tree.bind("<<TreeviewSelect>>", self._show_diagnostic_error)

        footer = ttk.Frame(shell)
        footer.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.diag_error_var = tk.StringVar(value="错误详情：无")
        ttk.Label(
            footer,
            textvariable=self.diag_error_var,
            style="Muted.TLabel",
            wraplength=980,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            footer,
            text="诊断会发起真实请求并产生 Token。",
            style="Muted.TLabel",
        ).grid(row=0, column=1, sticky="e", padx=(16, 0))

        executable = find_codex_executable()
        if executable is None:
            self.diag_cli_var.set("未找到 Codex CLI")
            self.diag_start_button.configure(state="disabled")
        else:
            self.diag_cli_var.set(f"CLI：{executable.name}")

    def _diagnostic_window_exists(self) -> bool:
        return bool(
            self._diagnostic_window is not None
            and self._diagnostic_window.winfo_exists()
        )

    def _start_diagnostics(self) -> None:
        executable = find_codex_executable()
        if executable is None:
            messagebox.showerror(APP_NAME, "未找到可执行的 Codex CLI。")
            return
        try:
            settings = self._current_settings()
            attempts = int(self.diag_attempts_var.get())
            timeout_seconds = int(self.diag_timeout_var.get())
            if attempts not in (1, 3, 5):
                raise ValueError("测试次数只能是 1、3 或 5")
            if not 10 <= timeout_seconds <= 600:
                raise ValueError("超时必须在 10 到 600 秒之间")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法开始诊断：{exc}")
            return

        target = self.diag_target_var.get()
        cases: list[tuple[str, str, str]] = []
        if target in ("主模型", "主模型和子代理"):
            cases.append(("主模型", settings.main_model, settings.main_effort))
        if target in ("子代理", "主模型和子代理"):
            cases.append(("子代理", settings.subagent_model, settings.subagent_effort))
        if not cases:
            messagebox.showerror(APP_NAME, "请选择测试目标。")
            return

        self._diagnostic_generation += 1
        generation = self._diagnostic_generation
        self._diagnostic_results = []
        self._diagnostic_errors = {}
        for item in self.diag_tree.get_children():
            self.diag_tree.delete(item)
        self.diag_error_var.set("错误详情：无")
        self._update_diagnostic_summary()
        self._set_diagnostic_running(True)
        self.diag_cli_var.set(f"CLI：{executable}")

        runner = CodexDiagnosticsRunner(executable, self.home, Path.home())
        self._diagnostic_runner = runner

        def progress(payload: dict[str, object]) -> None:
            self._post(
                lambda value=payload: self._apply_diagnostic_progress(
                    value, generation
                )
            )

        def result(value: DiagnosticResult) -> None:
            self._post(
                lambda item=value: self._apply_diagnostic_result(item, generation)
            )

        threading.Thread(
            target=runner.run,
            args=(cases, attempts, float(timeout_seconds), progress, result),
            daemon=True,
        ).start()

    def _stop_diagnostics(self) -> None:
        if self._diagnostic_runner is None:
            return
        self.diag_live_var.set("正在停止当前诊断请求…")
        self.diag_stop_button.configure(state="disabled")
        self._diagnostic_runner.cancel()

    def _set_diagnostic_running(self, running: bool) -> None:
        if not self._diagnostic_window_exists():
            return
        self.diag_target_combo.configure(state="disabled" if running else "readonly")
        self.diag_attempts_combo.configure(state="disabled" if running else "readonly")
        self.diag_timeout_spin.configure(state="disabled" if running else "normal")
        self.diag_start_button.configure(state="disabled" if running else "normal")
        self.diag_stop_button.configure(state="normal" if running else "disabled")

    def _apply_diagnostic_progress(
        self, payload: dict[str, object], generation: int
    ) -> None:
        if generation != self._diagnostic_generation or not self._diagnostic_window_exists():
            return
        if payload.get("phase") == "running":
            self.diag_live_var.set(
                f"{payload.get('role')} · {payload.get('model')}/{payload.get('effort')} · "
                f"第 {payload.get('attempt')} 轮 · {float(payload.get('elapsed') or 0):.1f}s · "
                f"输出 {int(payload.get('output_tokens') or 0):,} · "
                f"实时 {float(payload.get('tokens_per_second') or 0):.1f} Tokens/s"
            )
            return
        if payload.get("phase") == "cleanup":
            self.diag_live_var.set(
                f"{payload.get('role')} · API 已在 "
                f"{float(payload.get('response_seconds') or 0):.2f}s 完成 · "
                f"CLI 正在收尾 {float(payload.get('cleanup_seconds') or 0):.1f}s"
            )
            return
        if payload.get("phase") == "finished":
            cancelled = bool(payload.get("cancelled"))
            self.diag_live_var.set(
                "测试已停止" if cancelled else "测试完成"
            )
            self._diagnostic_runner = None
            self._set_diagnostic_running(False)
            self.refresh_stats()

    def _apply_diagnostic_result(
        self, result: DiagnosticResult, generation: int
    ) -> None:
        if generation != self._diagnostic_generation or not self._diagnostic_window_exists():
            return
        self._diagnostic_results.append(result)
        compact_error = " ".join(result.error.split())
        if len(compact_error) > 180:
            compact_error = compact_error[:177] + "..."
        item_id = self.diag_tree.insert(
            "",
            "end",
            values=(
                result.role,
                result.model,
                result.effort,
                result.attempt,
                result.status,
                (
                    f"{result.first_response_seconds:.2f}s"
                    if result.first_response_seconds is not None
                    else "--"
                ),
                f"{result.total_seconds:.2f}s",
                f"+{result.cleanup_seconds:.2f}s",
                f"{result.input_tokens:,}",
                f"{result.cached_input_tokens:,}",
                f"{result.output_tokens:,}",
                f"{result.tokens_per_second:.1f}",
                compact_error,
            ),
            tags=("success" if result.success else "failure",),
        )
        self._diagnostic_errors[item_id] = result.error
        self.diag_tree.see(item_id)
        self._update_diagnostic_summary()

    def _update_diagnostic_summary(self) -> None:
        summary = summarize_diagnostics(self._diagnostic_results)
        self.diag_summary_vars["success"].set(
            f"{summary.succeeded}/{summary.total} ({summary.success_rate:.1f}%)"
            if summary.total
            else "0/0"
        )
        self.diag_summary_vars["first"].set(
            f"{summary.average_first_response:.2f} s"
            if summary.average_first_response is not None
            else "--"
        )
        self.diag_summary_vars["total"].set(
            f"{summary.average_total_time:.2f} s"
            if summary.average_total_time is not None
            else "--"
        )
        self.diag_summary_vars["speed"].set(
            f"{summary.average_tokens_per_second:.1f} Tokens/s"
            if summary.average_tokens_per_second is not None
            else "--"
        )

    @staticmethod
    def _custom_auth_label(auth_mode: str) -> str:
        return {
            "bearer": "Bearer",
            "x-api-key": "x-api-key",
            "none": "无鉴权",
        }.get(auth_mode, auth_mode)

    def open_custom_api_benchmark(self) -> None:
        if (
            self._custom_api_window is not None
            and self._custom_api_window.winfo_exists()
        ):
            self._custom_api_window.deiconify()
            self._custom_api_window.lift()
            self._custom_api_window.focus_force()
            return
        try:
            self._custom_api_endpoints = self.custom_api_store.load()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法读取自定义 API 配置：{exc}")
            return

        window = tk.Toplevel(self.root)
        self._custom_api_window = window
        window.title(f"自定义 API 测速 · {APP_NAME} {APP_VERSION}")
        window.geometry("1380x820")
        window.minsize(1080, 680)
        window.configure(bg="#111418")
        window.protocol("WM_DELETE_WINDOW", self._close_custom_api)

        shell = ttk.Frame(window, padding=16)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(5, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        ttk.Label(header, text="已保存 API", style="Section.TLabel").pack(side="left")
        ttk.Label(
            header,
            text="自定义 API 使用独立 Key；Codex 官方订阅使用 CLI 登录",
            style="Muted.TLabel",
        ).pack(side="left", padx=(14, 0))
        self.custom_api_manage_buttons: list[ttk.Button] = []
        for text, command in (
            ("新增 API", lambda: self._open_custom_api_editor(None)),
            ("全选", lambda: self._set_all_custom_api_selection(True)),
            ("清空", lambda: self._set_all_custom_api_selection(False)),
        ):
            button = ttk.Button(header, text=text, command=command)
            button.pack(side="right", padx=(8 if self.custom_api_manage_buttons else 0, 0))
            self.custom_api_manage_buttons.append(button)
        official_button = ttk.Button(
            header,
            text="测试 Codex 官方订阅",
            command=self.open_diagnostics,
        )
        official_button.pack(side="right", padx=(0, 8))
        self.custom_api_manage_buttons.append(official_button)

        endpoint_panel = ttk.Frame(shell, style="Panel.TFrame", padding=10)
        endpoint_panel.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        endpoint_panel.columnconfigure(0, weight=1)
        endpoint_panel.rowconfigure(0, weight=1)
        list_frame = ttk.Frame(endpoint_panel, style="Panel.TFrame")
        list_frame.grid(row=0, column=0, sticky="ew")
        list_frame.columnconfigure(0, weight=1)
        self.custom_api_canvas = tk.Canvas(
            list_frame,
            height=148,
            background="#171b20",
            highlightthickness=0,
            borderwidth=0,
        )
        self.custom_api_canvas.grid(row=0, column=0, sticky="ew")
        scrollbar = ttk.Scrollbar(
            list_frame,
            orient="vertical",
            command=self.custom_api_canvas.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.custom_api_canvas.configure(yscrollcommand=scrollbar.set)
        self.custom_api_list_frame = ttk.Frame(
            self.custom_api_canvas,
            style="Panel.TFrame",
        )
        self.custom_api_list_frame.columnconfigure(0, weight=1)
        self.custom_api_canvas_window = self.custom_api_canvas.create_window(
            (0, 0),
            window=self.custom_api_list_frame,
            anchor="nw",
        )
        self.custom_api_list_frame.bind(
            "<Configure>",
            lambda _event: self.custom_api_canvas.configure(
                scrollregion=self.custom_api_canvas.bbox("all")
            ),
        )
        self.custom_api_canvas.bind(
            "<Configure>",
            lambda event: self.custom_api_canvas.itemconfigure(
                self.custom_api_canvas_window,
                width=event.width,
            ),
        )
        self._custom_api_selection_vars: dict[str, tk.BooleanVar] = {}
        self._custom_api_checks: list[ttk.Checkbutton] = []
        self._custom_api_row_buttons: list[ttk.Button] = []
        self._rebuild_custom_api_list()

        controls = ttk.Frame(shell)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.custom_api_attempts_var = tk.StringVar(value="3")
        self.custom_api_timeout_var = tk.IntVar(value=120)
        ttk.Label(controls, text="测试次数").pack(side="left")
        self.custom_api_attempts_combo = ttk.Combobox(
            controls,
            textvariable=self.custom_api_attempts_var,
            values=("1", "3", "5"),
            state="readonly",
            width=5,
        )
        self.custom_api_attempts_combo.pack(side="left", padx=(8, 18))
        ttk.Label(controls, text="单次超时（秒）").pack(side="left")
        self.custom_api_timeout_spin = ttk.Spinbox(
            controls,
            from_=10,
            to=600,
            textvariable=self.custom_api_timeout_var,
            width=7,
        )
        self.custom_api_timeout_spin.pack(side="left", padx=(8, 18))
        self.custom_api_cache_test_var = tk.BooleanVar(value=False)
        self.custom_api_cache_test_check = ttk.Checkbutton(
            controls,
            text="缓存命中测试",
            variable=self.custom_api_cache_test_var,
        )
        self.custom_api_cache_test_check.pack(side="left", padx=(0, 18))
        self.custom_api_start_button = ttk.Button(
            controls,
            text="开始测速",
            style="Primary.TButton",
            command=self._start_custom_api_benchmark,
        )
        self.custom_api_start_button.pack(side="left")
        self.custom_api_stop_button = ttk.Button(
            controls,
            text="停止",
            command=self._stop_custom_api,
            state="disabled",
        )
        self.custom_api_stop_button.pack(side="left", padx=8)
        self.custom_api_cost_var = tk.StringVar(
            value="测速会发起真实请求并消耗对应 API 配额。"
        )
        ttk.Label(
            controls,
            textvariable=self.custom_api_cost_var,
            style="Muted.TLabel",
        ).pack(side="right")

        comparison_panel = ttk.Frame(shell, style="Panel.TFrame", padding=10)
        comparison_panel.grid(row=3, column=0, sticky="ew", pady=(0, 10))
        comparison_panel.columnconfigure(0, weight=1)
        ttk.Label(
            comparison_panel,
            text="API 对比",
            style="Section.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.custom_api_fastest_var = tk.StringVar(value="最快：--")
        self.custom_api_stable_var = tk.StringVar(value="最稳定：--")
        self.custom_api_coverage_var = tk.StringVar(value="完成：0/0")
        comparison_header = ttk.Frame(comparison_panel, style="Panel.TFrame")
        comparison_header.grid(row=0, column=0, sticky="e")
        ttk.Label(
            comparison_header,
            textvariable=self.custom_api_fastest_var,
            style="PanelMuted.TLabel",
        ).pack(side="left", padx=8)
        ttk.Label(
            comparison_header,
            textvariable=self.custom_api_stable_var,
            style="PanelMuted.TLabel",
        ).pack(side="left", padx=8)
        ttk.Label(
            comparison_header,
            textvariable=self.custom_api_coverage_var,
            style="PanelMuted.TLabel",
        ).pack(side="left", padx=8)
        self.custom_api_compare_tree = ttk.Treeview(
            comparison_panel,
            columns=(
                "name",
                "model",
                "success",
                "first",
                "total",
                "jitter",
                "cache",
                "speed",
            ),
            show="headings",
            height=4,
        )
        compare_headings = {
            "name": "API",
            "model": "模型",
            "success": "成功率",
            "first": "平均首响应",
            "total": "平均总耗时",
            "jitter": "耗时波动",
            "cache": "缓存命中",
            "speed": "Tokens/s",
        }
        compare_widths = {
            "name": 170,
            "model": 150,
            "success": 90,
            "first": 110,
            "total": 110,
            "jitter": 100,
            "cache": 110,
            "speed": 100,
        }
        for name in self.custom_api_compare_tree["columns"]:
            self.custom_api_compare_tree.heading(name, text=compare_headings[name])
            self.custom_api_compare_tree.column(
                name,
                width=compare_widths[name],
                minwidth=70,
                anchor="w" if name in ("name", "model") else "center",
            )
        self.custom_api_compare_tree.tag_configure("winner", foreground="#7ee787")
        self.custom_api_compare_tree.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self.custom_api_live_var = tk.StringVar(value="尚未开始测速")
        ttk.Label(
            shell,
            textvariable=self.custom_api_live_var,
        ).grid(row=4, column=0, sticky="ew", pady=(0, 8))

        detail_frame = ttk.Frame(shell)
        detail_frame.grid(row=5, column=0, sticky="nsew")
        shell.rowconfigure(5, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.custom_api_detail_tree = ttk.Treeview(
            detail_frame,
            columns=(
                "name",
                "model",
                "attempt",
                "status",
                "http",
                "first",
                "total",
                "output",
                "cache",
                "speed",
                "error",
            ),
            show="headings",
            selectmode="browse",
        )
        detail_headings = {
            "name": "API",
            "model": "模型",
            "attempt": "轮次",
            "status": "状态",
            "http": "HTTP",
            "first": "首响应",
            "total": "总耗时",
            "output": "输出",
            "cache": "缓存命中",
            "speed": "Tokens/s",
            "error": "错误详情",
        }
        detail_widths = {
            "name": 160,
            "model": 140,
            "attempt": 55,
            "status": 110,
            "http": 55,
            "first": 82,
            "total": 82,
            "output": 80,
            "cache": 136,
            "speed": 90,
            "error": 360,
        }
        for name in self.custom_api_detail_tree["columns"]:
            self.custom_api_detail_tree.heading(name, text=detail_headings[name])
            self.custom_api_detail_tree.column(
                name,
                width=detail_widths[name],
                minwidth=50,
                stretch=name == "error",
                anchor="w" if name in ("name", "model", "error") else "center",
            )
        self.custom_api_detail_tree.tag_configure("success", foreground="#7ee787")
        self.custom_api_detail_tree.tag_configure("failure", foreground="#ff9b9b")
        self.custom_api_detail_tree.grid(row=0, column=0, sticky="nsew")
        detail_vertical = ttk.Scrollbar(
            detail_frame,
            orient="vertical",
            command=self.custom_api_detail_tree.yview,
        )
        detail_vertical.grid(row=0, column=1, sticky="ns")
        detail_horizontal = ttk.Scrollbar(
            detail_frame,
            orient="horizontal",
            command=self.custom_api_detail_tree.xview,
        )
        detail_horizontal.grid(row=1, column=0, sticky="ew")
        self.custom_api_detail_tree.configure(
            yscrollcommand=detail_vertical.set,
            xscrollcommand=detail_horizontal.set,
        )
        self.custom_api_detail_tree.bind(
            "<<TreeviewSelect>>",
            self._show_custom_api_error,
        )
        self.custom_api_error_var = tk.StringVar(value="错误详情：无")
        ttk.Label(
            shell,
            textvariable=self.custom_api_error_var,
            style="Muted.TLabel",
            wraplength=1080,
            justify="left",
        ).grid(row=6, column=0, sticky="w", pady=(8, 0))

    def _rebuild_custom_api_list(self) -> None:
        for child in self.custom_api_list_frame.winfo_children():
            child.destroy()
        self._custom_api_selection_vars = {}
        self._custom_api_checks = []
        self._custom_api_row_buttons = []
        if not self._custom_api_endpoints:
            ttk.Label(
                self.custom_api_list_frame,
                text="尚未保存 API，点击右上角“新增 API”。",
                style="PanelMuted.TLabel",
            ).grid(row=0, column=0, sticky="w", padx=8, pady=12)
            return
        for row_index, endpoint in enumerate(self._custom_api_endpoints):
            row = ttk.Frame(self.custom_api_list_frame, style="Panel.TFrame")
            row.grid(row=row_index, column=0, sticky="ew", padx=2, pady=3)
            row.columnconfigure(0, weight=1)
            selected = tk.BooleanVar(value=endpoint.selected)
            self._custom_api_selection_vars[endpoint.endpoint_id] = selected
            check = ttk.Checkbutton(
                row,
                text=(
                    f"{endpoint.name} · {custom_api_type_label(endpoint.api_type)}"
                    f" · {endpoint.model}"
                ),
                variable=selected,
                style="Panel.TCheckbutton",
                command=lambda eid=endpoint.endpoint_id: self._custom_api_selection_changed(eid),
            )
            check.grid(row=0, column=0, sticky="w")
            self._custom_api_checks.append(check)
            ttk.Label(
                row,
                text=(
                    f"基础 URL：{custom_api_base_url(endpoint.url)} · "
                    f"{self._custom_auth_label(endpoint.auth_mode)}"
                ),
                style="PanelMuted.TLabel",
                wraplength=760,
                justify="left",
            ).grid(row=1, column=0, sticky="w", padx=(28, 0))
            edit_button = ttk.Button(
                row,
                text="编辑",
                command=lambda item=endpoint: self._open_custom_api_editor(item),
            )
            edit_button.grid(row=0, column=1, rowspan=2, padx=(8, 0))
            delete_button = ttk.Button(
                row,
                text="删除",
                command=lambda eid=endpoint.endpoint_id: self._delete_custom_api(eid),
            )
            delete_button.grid(row=0, column=2, rowspan=2, padx=(6, 0))
            self._custom_api_row_buttons.extend((edit_button, delete_button))

    def _save_custom_api_endpoints(self) -> bool:
        try:
            self.custom_api_store.save(self._custom_api_endpoints)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"保存自定义 API 失败：{exc}")
            return False
        return True

    def _custom_api_selection_changed(self, endpoint_id: str) -> None:
        selected = self._custom_api_selection_vars[endpoint_id].get()
        for endpoint in self._custom_api_endpoints:
            if endpoint.endpoint_id == endpoint_id:
                endpoint.selected = selected
                break
        self._save_custom_api_endpoints()

    def _set_all_custom_api_selection(self, selected: bool) -> None:
        for endpoint in self._custom_api_endpoints:
            endpoint.selected = selected
        self._rebuild_custom_api_list()
        self._save_custom_api_endpoints()

    def _open_custom_api_editor(self, endpoint: CustomApiEndpoint | None) -> None:
        editor = tk.Toplevel(self._custom_api_window)
        editor.title("编辑自定义 API" if endpoint else "新增自定义 API")
        editor.geometry("700x520")
        editor.minsize(620, 450)
        editor.configure(bg="#111418")
        editor.transient(self._custom_api_window)
        editor.grab_set()
        panel = ttk.Frame(editor, style="Panel.TFrame", padding=18)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=0)

        name_var = tk.StringVar(value=endpoint.name if endpoint else "")
        base_url_var = tk.StringVar(
            value=custom_api_base_url(endpoint.url) if endpoint else ""
        )
        model_var = tk.StringVar(value=endpoint.model if endpoint else "")
        initial_api_type = endpoint.api_type if endpoint else "chat_completions"
        api_type_var = tk.StringVar(
            value=custom_api_type_label(endpoint.api_type)
            if endpoint
            else custom_api_type_label("chat_completions")
        )
        auth_var = tk.StringVar(
            value=self._custom_auth_label(endpoint.auth_mode)
            if endpoint
            else self._custom_auth_label(
                "x-api-key" if initial_api_type == "anthropic_messages" else "bearer"
            )
        )
        key_var = tk.StringVar()
        model_status_var = tk.StringVar(
            value="填好基础 URL 和 Key 后点击“获取模型”。"
        )

        fields = (("名称", name_var), ("基础 URL", base_url_var))
        for row, (label, variable) in enumerate(fields):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=8)
            entry = ttk.Entry(panel, textvariable=variable)
            entry.grid(row=row, column=1, columnspan=2, sticky="ew", pady=8)

        ttk.Label(panel, text="接口类型").grid(row=2, column=0, sticky="w", pady=8)
        api_type_combo = ttk.Combobox(
            panel,
            textvariable=api_type_var,
            values=tuple(CUSTOM_API_TYPE_LABELS.values()),
            state="readonly",
        )
        api_type_combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=8)

        def selected_api_type() -> str:
            return {
                label: key for key, label in CUSTOM_API_TYPE_LABELS.items()
            }.get(api_type_var.get(), "chat_completions")

        def api_type_changed(_event: object = None) -> None:
            if (
                selected_api_type() == "anthropic_messages"
                and auth_var.get() == "Bearer"
            ):
                auth_var.set("x-api-key")

        api_type_combo.bind("<<ComboboxSelected>>", api_type_changed)

        ttk.Label(panel, text="模型").grid(row=3, column=0, sticky="w", pady=8)
        model_combo = ttk.Combobox(
            panel,
            textvariable=model_var,
            state="normal",
        )
        model_combo.grid(row=3, column=1, sticky="ew", pady=8)

        ttk.Label(panel, text="鉴权方式").grid(row=4, column=0, sticky="w", pady=8)
        auth_combo = ttk.Combobox(
            panel,
            textvariable=auth_var,
            values=("Bearer", "x-api-key", "无鉴权"),
            state="readonly",
        )
        auth_combo.grid(row=4, column=1, columnspan=2, sticky="ew", pady=8)

        ttk.Label(panel, text="API Key").grid(row=5, column=0, sticky="w", pady=8)
        key_entry = ttk.Entry(panel, textvariable=key_var, show="*")
        key_entry.grid(row=5, column=1, sticky="ew", pady=8)

        def selected_auth_mode() -> str:
            return {
                "Bearer": "bearer",
                "x-api-key": "x-api-key",
                "无鉴权": "none",
            }.get(auth_var.get(), "bearer")

        def apply_models(models: list[str], error: str = "") -> None:
            if not editor.winfo_exists():
                return
            fetch_button.configure(state="normal")
            if error:
                model_status_var.set(f"获取模型失败：{error}")
                return
            model_combo.configure(values=models)
            if model_var.get().strip() not in models:
                model_var.set(models[0])
            model_status_var.set(f"已获取 {len(models)} 个模型，可从下拉框选择。")

        def fetch_models() -> None:
            auth_mode = selected_auth_mode()
            base_url = base_url_var.get().strip()
            api_key = key_var.get()
            try:
                validate_custom_api_url(custom_api_endpoint_url(base_url))
                if auth_mode != "none" and not api_key and endpoint:
                    api_key = unprotect_secret(endpoint.encrypted_api_key)
                if auth_mode != "none" and not api_key:
                    raise ValueError("请先填写 API Key")
            except Exception as exc:
                model_status_var.set(f"无法获取模型：{exc}")
                return
            fetch_button.configure(state="disabled")
            model_status_var.set("正在获取模型列表…")

            def worker() -> None:
                try:
                    models = fetch_custom_api_models(
                        base_url,
                        auth_mode,
                        api_key,
                        api_type=selected_api_type(),
                    )
                    self._post(lambda: apply_models(models))
                except Exception as exc:
                    self._post(lambda: apply_models([], str(exc)))

            threading.Thread(target=worker, daemon=True).start()

        fetch_button = ttk.Button(panel, text="获取模型", command=fetch_models)
        fetch_button.grid(row=3, column=2, padx=(8, 0), pady=8)

        ttk.Label(
            panel,
            textvariable=model_status_var,
            style="PanelMuted.TLabel",
            wraplength=490,
            justify="left",
        ).grid(row=6, column=1, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(
            panel,
            text="基础 URL 例：https://api.example.com/v1；保存时会自动补全对应接口路径。",
            style="PanelMuted.TLabel",
            wraplength=540,
            justify="left",
        ).grid(row=7, column=1, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Label(
            panel,
            text=(
                "编辑时留空保留已保存 Key；Key 会使用 Windows DPAPI 加密，"
                "不会明文写入配置。"
            ),
            style="PanelMuted.TLabel",
            wraplength=540,
            justify="left",
        ).grid(row=8, column=1, columnspan=2, sticky="w", pady=(0, 12))
        button_row = ttk.Frame(panel, style="Panel.TFrame")
        button_row.grid(row=9, column=0, columnspan=3, sticky="e", pady=(8, 0))

        def save_editor() -> None:
            auth_mode = selected_auth_mode()
            api_type = selected_api_type()
            existing_key = endpoint.encrypted_api_key if endpoint else ""
            key = key_var.get()
            if auth_mode == "none":
                encrypted_key = ""
            elif key:
                try:
                    encrypted_key = protect_secret(key)
                except Exception as exc:
                    messagebox.showerror(APP_NAME, f"API Key 加密失败：{exc}", parent=editor)
                    return
            else:
                encrypted_key = existing_key
            try:
                endpoint_url = custom_api_endpoint_url(base_url_var.get(), api_type)
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=editor)
                return
            candidate = CustomApiEndpoint(
                endpoint_id=endpoint.endpoint_id if endpoint else uuid.uuid4().hex,
                name=name_var.get().strip(),
                url=endpoint_url,
                model=model_var.get().strip(),
                auth_mode=auth_mode,
                encrypted_api_key=encrypted_key,
                selected=endpoint.selected if endpoint else True,
                api_type=api_type,
            )
            try:
                validate_custom_api_endpoint(candidate)
                if candidate.auth_mode != "none" and not candidate.encrypted_api_key:
                    raise ValueError("请输入 API Key")
                if candidate.auth_mode != "none" and not key and not endpoint:
                    raise ValueError("新增 API 时必须输入 API Key")
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=editor)
                return
            if endpoint:
                for index, current in enumerate(self._custom_api_endpoints):
                    if current.endpoint_id == endpoint.endpoint_id:
                        self._custom_api_endpoints[index] = candidate
                        break
            else:
                self._custom_api_endpoints.append(candidate)
            if self._save_custom_api_endpoints():
                self._rebuild_custom_api_list()
                editor.destroy()

        ttk.Button(button_row, text="取消", command=editor.destroy).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(
            button_row,
            text="保存 API",
            style="Primary.TButton",
            command=save_editor,
        ).pack(side="right")
        editor.bind("<Return>", lambda _event: save_editor())

    def _delete_custom_api(self, endpoint_id: str) -> None:
        endpoint = next(
            (item for item in self._custom_api_endpoints if item.endpoint_id == endpoint_id),
            None,
        )
        if endpoint is None:
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"删除已保存 API“{endpoint.name}”？\n加密保存的 Key 也会一并删除。",
            parent=self._custom_api_window,
        ):
            return
        self._custom_api_endpoints = [
            item for item in self._custom_api_endpoints if item.endpoint_id != endpoint_id
        ]
        self._save_custom_api_endpoints()
        self._rebuild_custom_api_list()

    def _start_custom_api_benchmark(self) -> None:
        try:
            attempts = int(self.custom_api_attempts_var.get())
            timeout_seconds = int(self.custom_api_timeout_var.get())
            if attempts not in (1, 3, 5):
                raise ValueError("测试次数只能是 1、3 或 5")
            if not 10 <= timeout_seconds <= 600:
                raise ValueError("超时必须在 10 到 600 秒之间")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法开始测速：{exc}")
            return
        cache_test = bool(self.custom_api_cache_test_var.get())
        selected_endpoints = [
            endpoint for endpoint in self._custom_api_endpoints if endpoint.selected
        ]
        if not selected_endpoints:
            messagebox.showerror(APP_NAME, "请至少勾选一个 API。")
            return
        cases: list[tuple[CustomApiEndpoint, str]] = []
        try:
            for endpoint in selected_endpoints:
                validate_custom_api_endpoint(endpoint)
                api_key = (
                    unprotect_secret(endpoint.encrypted_api_key)
                    if endpoint.auth_mode != "none"
                    else ""
                )
                if endpoint.auth_mode != "none" and not api_key:
                    raise ValueError(f"{endpoint.name} 没有可用的 API Key")
                cases.append((endpoint, api_key))
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法读取 API 配置：{exc}")
            return

        self._custom_api_generation += 1
        generation = self._custom_api_generation
        self._custom_api_results = []
        self._custom_api_error_details = {}
        for tree in (self.custom_api_compare_tree, self.custom_api_detail_tree):
            for item in tree.get_children():
                tree.delete(item)
        self.custom_api_error_var.set("错误详情：无")
        self.custom_api_fastest_var.set("最快：--")
        self.custom_api_stable_var.set("最稳定：--")
        self.custom_api_coverage_var.set(
            f"完成：0/{len(cases) * attempts * (2 if cache_test else 1)}"
        )
        self._set_custom_api_running(True)
        runner = CustomApiBenchmarkRunner()
        self._custom_api_runner = runner

        def progress(payload: dict[str, object]) -> None:
            self._post(
                lambda value=payload: self._apply_custom_api_progress(
                    value, generation
                )
            )

        def result(value: CustomApiResult) -> None:
            self._post(
                lambda item=value: self._apply_custom_api_result(item, generation),
            )

        threading.Thread(
            target=runner.run,
            args=(
                cases,
                attempts,
                float(timeout_seconds),
                progress,
                result,
                cache_test,
            ),
            daemon=True,
        ).start()

    def _set_custom_api_running(self, running: bool) -> None:
        if not self._custom_api_window_exists():
            return
        self.custom_api_attempts_combo.configure(
            state="disabled" if running else "readonly"
        )
        self.custom_api_timeout_spin.configure(
            state="disabled" if running else "normal"
        )
        self.custom_api_cache_test_check.configure(
            state="disabled" if running else "normal"
        )
        self.custom_api_start_button.configure(
            state="disabled" if running else "normal"
        )
        self.custom_api_stop_button.configure(
            state="normal" if running else "disabled"
        )
        for button in self.custom_api_manage_buttons:
            button.configure(state="disabled" if running else "normal")
        for check in self._custom_api_checks:
            check.configure(state="disabled" if running else "normal")
        for button in self._custom_api_row_buttons:
            button.configure(state="disabled" if running else "normal")

    def _custom_api_window_exists(self) -> bool:
        return bool(
            self._custom_api_window is not None
            and self._custom_api_window.winfo_exists()
        )

    def _apply_custom_api_progress(
        self, payload: dict[str, object], generation: int
    ) -> None:
        if generation != self._custom_api_generation or not self._custom_api_window_exists():
            return
        if payload.get("phase") == "running":
            estimated = "约 " if payload.get("estimated") else ""
            cache_phase = {
                "warmup": " · 预热",
                "measure": " · 检测",
            }.get(str(payload.get("cache_phase") or ""), "")
            input_tokens = int(payload.get("input_tokens") or 0)
            cached_input_tokens = int(payload.get("cached_input_tokens") or 0)
            cache_usage = ""
            if payload.get("input_tokens_reported") and input_tokens > 0:
                cache_usage = (
                    f" · 缓存 {cached_input_tokens * 100.0 / input_tokens:.1f}%"
                )
            self.custom_api_live_var.set(
                f"{payload.get('name')} · {payload.get('model')} · "
                f"第 {payload.get('attempt')} 轮{cache_phase} · "
                f"{float(payload.get('elapsed') or 0):.1f}s · "
                f"输出 {estimated}{int(payload.get('output_tokens') or 0):,} · "
                f"{float(payload.get('tokens_per_second') or 0):.1f} Tokens/s"
                f"{cache_usage}"
            )
            return
        if payload.get("phase") == "finished":
            self.custom_api_live_var.set(
                "自定义 API 测速已停止"
                if payload.get("cancelled")
                else "自定义 API 测速完成"
            )
            self._custom_api_runner = None
            self._set_custom_api_running(False)

    def _apply_custom_api_result(
        self, result: CustomApiResult, generation: int
    ) -> None:
        if generation != self._custom_api_generation or not self._custom_api_window_exists():
            return
        self._custom_api_results.append(result)
        error = " ".join(result.error.split())
        if len(error) > 220:
            error = error[:217] + "..."
        attempt = str(result.attempt)
        if result.cache_phase == "warmup":
            attempt += " · 预热"
        elif result.cache_phase == "measure":
            attempt += " · 检测"
        cache_hit = (
            f"{result.cache_hit_rate:.1f}% "
            f"({result.cached_input_tokens:,}/{result.input_tokens:,})"
            if result.cache_hit_rate is not None
            else "--"
        )
        item_id = self.custom_api_detail_tree.insert(
            "",
            "end",
            values=(
                result.name,
                f"{custom_api_type_label(result.api_type)} · {result.model}",
                attempt,
                result.status,
                result.http_status or "--",
                (
                    f"{result.first_response_seconds:.2f}s"
                    if result.first_response_seconds is not None
                    else "--"
                ),
                f"{result.total_seconds:.2f}s",
                f"{'~' if result.tokens_estimated else ''}{result.output_tokens:,}",
                cache_hit,
                f"{result.tokens_per_second:.1f}",
                error,
            ),
            tags=("success" if result.success else "failure",),
        )
        self._custom_api_error_details[item_id] = result.error
        self.custom_api_detail_tree.see(item_id)
        self._update_custom_api_comparison()

    def _update_custom_api_comparison(self) -> None:
        summaries = summarize_custom_apis(self._custom_api_results)
        for item in self.custom_api_compare_tree.get_children():
            self.custom_api_compare_tree.delete(item)
        fastest, stable = choose_custom_api_winners(summaries)
        self.custom_api_fastest_var.set(
            f"最快：{fastest.name} {fastest.average_total_time:.2f}s"
            if fastest and fastest.average_total_time is not None
            else "最快：--"
        )
        self.custom_api_stable_var.set(
            f"最稳定：{stable.name} {stable.success_rate:.0f}%"
            if stable
            else "最稳定：--"
        )
        self.custom_api_coverage_var.set(
            f"完成：{len(self._custom_api_results)} 次"
        )
        for summary in summaries:
            tags: list[str] = []
            if fastest and summary.endpoint_id == fastest.endpoint_id:
                tags.append("winner")
            if stable and summary.endpoint_id == stable.endpoint_id:
                tags.append("winner")
            self.custom_api_compare_tree.insert(
                "",
                "end",
                values=(
                    summary.name,
                    f"{custom_api_type_label(summary.api_type)} · {summary.model}",
                    f"{summary.succeeded}/{summary.total} ({summary.success_rate:.0f}%)",
                    (
                        f"{summary.average_first_response:.2f}s"
                        if summary.average_first_response is not None
                        else "--"
                    ),
                    (
                        f"{summary.average_total_time:.2f}s"
                        if summary.average_total_time is not None
                        else "--"
                    ),
                    (
                        f"{summary.jitter_percent:.1f}%"
                        if summary.jitter_percent is not None
                        else "需多次"
                    ),
                    (
                        f"{summary.cache_hit_rate:.1f}%"
                        if summary.cache_hit_rate is not None
                        else "--"
                    ),
                    (
                        f"{summary.average_tokens_per_second:.1f}"
                        if summary.average_tokens_per_second is not None
                        else "--"
                    ),
                ),
                tags=tuple(tags),
            )

    def _show_custom_api_error(self, _event: object = None) -> None:
        selected = self.custom_api_detail_tree.selection()
        if not selected:
            self.custom_api_error_var.set("错误详情：无")
            return
        error = self._custom_api_error_details.get(selected[0], "")
        self.custom_api_error_var.set(f"错误详情：{error or '无'}")

    def _stop_custom_api(self) -> None:
        if self._custom_api_runner is None:
            return
        self.custom_api_live_var.set("正在停止当前 API 测速…")
        self.custom_api_stop_button.configure(state="disabled")
        self._custom_api_runner.cancel()

    def _close_custom_api(self) -> None:
        self._custom_api_generation += 1
        if self._custom_api_runner is not None:
            self._custom_api_runner.cancel()
            self._custom_api_runner = None
        if self._custom_api_window is not None:
            self._custom_api_window.destroy()
        self._custom_api_window = None

    def _show_diagnostic_error(self, _event: object = None) -> None:
        selected = self.diag_tree.selection()
        if not selected:
            self.diag_error_var.set("错误详情：无")
            return
        error = self._diagnostic_errors.get(selected[0], "")
        self.diag_error_var.set(f"错误详情：{error or '无'}")

    def _close_diagnostics(self) -> None:
        self._diagnostic_generation += 1
        if self._diagnostic_runner is not None:
            self._diagnostic_runner.cancel()
            self._diagnostic_runner = None
        if self._diagnostic_window is not None:
            self._diagnostic_window.destroy()
        self._diagnostic_window = None

    def _close_app(self) -> None:
        self._resume_window.close()
        self.resume_service.close()
        self._refresh_generation += 1
        for job in (self._auto_refresh_job, self._ui_pump_job):
            if job is None:
                continue
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._auto_refresh_job = None
        self._ui_pump_job = None
        self._diagnostic_generation += 1
        if self._diagnostic_runner is not None:
            self._diagnostic_runner.cancel()
            self._diagnostic_runner = None
        self._custom_api_generation += 1
        if self._custom_api_runner is not None:
            self._custom_api_runner.cancel()
            self._custom_api_runner = None
        if self._gateway is not None:
            self._gateway.stop()
            self._gateway = None
        try:
            self.stats_reader.save_persistent_cache(force=True)
        except Exception:  # pragma: no cover - cache is best effort
            pass
        self.root.destroy()

    def _auto_refresh(self) -> None:
        self._auto_refresh_job = None
        try:
            if not self.root.winfo_exists():
                return
        except tk.TclError:
            return
        visible = self.root.state() == "normal"
        if visible:
            self.refresh_stats()
        self._auto_refresh_job = self.root.after(
            AUTO_REFRESH_MS if visible else BACKGROUND_REFRESH_MS, self._auto_refresh
        )

    def _on_window_mapped(self, event: tk.Event) -> None:
        if event.widget is not self.root or self.root.state() != "normal":
            return
        if self._auto_refresh_job is not None:
            self.root.after_cancel(self._auto_refresh_job)
        self._auto_refresh_job = self.root.after(AUTO_REFRESH_MS, self._auto_refresh)

    @staticmethod
    def _open_path(path: Path) -> None:
        path = path.resolve()
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def open_config(self) -> None:
        if not self.config_store.path.exists():
            self.config_store.path.parent.mkdir(parents=True, exist_ok=True)
            self.config_store.path.touch()
        self._open_path(self.config_store.path)

    def open_sessions(self) -> None:
        sessions = self.home / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        self._open_path(sessions)

    # ------------------------------------------------------------ DSH gateway
    def open_gateway(self) -> None:
        if (
            self._gateway_window is not None
            and self._gateway_window.winfo_exists()
        ):
            self._gateway_window.deiconify()
            self._gateway_window.lift()
            self._gateway_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self._gateway_window = window
        window.title(f"接入 DSH · {APP_NAME} {APP_VERSION}")
        window.geometry("920x780")
        window.minsize(860, 700)
        window.configure(bg="#0b0e12")
        window.protocol("WM_DELETE_WINDOW", self._close_gateway_window)

        shell = ttk.Frame(window, padding=(20, 16))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="接入 DSH", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.gateway_status_var = tk.StringVar(value="未启动")
        self.gateway_status_label = ttk.Label(
            header, textvariable=self.gateway_status_var, style="Subtitle.TLabel"
        )
        self.gateway_status_label.grid(row=0, column=1, sticky="w", padx=(12, 0))
        self.gateway_start_button = ttk.Button(
            header, text="启动网关", style="Primary.TButton",
            command=self._start_gateway,
        )
        self.gateway_start_button.grid(row=0, column=2, sticky="e")
        self.gateway_stop_button = ttk.Button(
            header, text="停止", command=self._stop_gateway, state="disabled"
        )
        self.gateway_stop_button.grid(row=0, column=3, sticky="e", padx=(8, 0))

        ttk.Label(
            shell,
            text=(
                "启动后，把下面的配置写进 DSH 的 cordis.patch.yml，"
                "就能把 luna/max 当作 DSH 的主模型；工具仍由 DSH 执行。\n"
                "下面的模型/思考只用于网关调用（命令行显式指定）；"
                "主界面的“运行设置”决定 Codex 应用本身用什么模型，两边互不影响。"
            ),
            style="Subtitle.TLabel",
            wraplength=840,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        config_card = ttk.Frame(shell, style="Card.TFrame", padding=14)
        config_card.grid(row=2, column=0, sticky="ew")
        config_card.columnconfigure(1, weight=1)
        config_card.columnconfigure(4, weight=0)

        self.gateway_model_var = tk.StringVar(value=self.gateway_config.model)
        self.gateway_effort_var = tk.StringVar(value=self.gateway_config.effort)
        self.gateway_port_var = tk.IntVar(value=self.gateway_config.port)
        self.gateway_parallel_var = tk.IntVar(value=self.gateway_config.max_parallel)
        self.gateway_timeout_var = tk.IntVar(
            value=int(self.gateway_config.timeout_seconds)
        )
        self.gateway_workdir_var = tk.StringVar(
            value=self.gateway_config.workdir or os.getcwd()
        )
        self.gateway_token_var = tk.StringVar(value=self.gateway_config.token)

        ttk.Label(config_card, text="模型", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        self.gateway_model_combo = ttk.Combobox(
            config_card, textvariable=self.gateway_model_var,
            values=self.available_models, state="normal", width=22,
        )
        self.gateway_model_combo.grid(row=0, column=1, sticky="w")
        ttk.Label(config_card, text="思考", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(14, 8)
        )
        self.gateway_effort_combo = ttk.Combobox(
            config_card, textvariable=self.gateway_effort_var,
            values=EFFORTS, state="normal", width=10,
        )
        self.gateway_effort_combo.grid(row=0, column=3, sticky="w")
        ttk.Label(config_card, text="端口", style="CardMuted.TLabel").grid(
            row=0, column=4, sticky="e", padx=(14, 8)
        )
        ttk.Spinbox(
            config_card, from_=1024, to=65535, textvariable=self.gateway_port_var,
            width=8,
        ).grid(row=0, column=5, sticky="e")
        self.gateway_model_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self._sync_gateway_efforts()
        )

        ttk.Label(config_card, text="工作目录", style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Entry(config_card, textvariable=self.gateway_workdir_var).grid(
            row=1, column=1, columnspan=4, sticky="ew", pady=(10, 0)
        )
        ttk.Button(
            config_card, text="浏览", command=self._browse_gateway_workdir
        ).grid(row=1, column=5, sticky="e", pady=(10, 0))

        ttk.Label(config_card, text="访问令牌", style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Entry(config_card, textvariable=self.gateway_token_var).grid(
            row=2, column=1, columnspan=4, sticky="ew", pady=(10, 0)
        )
        token_buttons = ttk.Frame(config_card, style="Card.TFrame")
        token_buttons.grid(row=2, column=5, sticky="e", pady=(10, 0))
        ttk.Button(
            token_buttons, text="生成", command=self._generate_gateway_token
        ).pack(side="left")
        ttk.Button(
            token_buttons, text="写入环境变量", command=self._export_gateway_token
        ).pack(side="left", padx=(6, 0))

        ttk.Label(config_card, text="并发 / 超时(s)", style="CardMuted.TLabel").grid(
            row=3, column=0, sticky="w", pady=(10, 0)
        )
        limits = ttk.Frame(config_card, style="Card.TFrame")
        limits.grid(row=3, column=1, columnspan=5, sticky="w", pady=(10, 0))
        ttk.Spinbox(
            limits, from_=1, to=8, textvariable=self.gateway_parallel_var, width=5
        ).pack(side="left")
        ttk.Spinbox(
            limits, from_=30, to=3600, textvariable=self.gateway_timeout_var, width=7
        ).pack(side="left", padx=(8, 0))
        self.gateway_autostart_var = tk.BooleanVar(
            value=self.gateway_config.auto_start
        )
        ttk.Checkbutton(
            limits, text="随控制台启动", variable=self.gateway_autostart_var,
            style="Panel.TCheckbutton",
        ).pack(side="left", padx=(14, 0))
        self.gateway_subagent_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            limits,
            text="同时把 DSH 子代理设为 DeepSeek",
            variable=self.gateway_subagent_var,
            style="Panel.TCheckbutton",
            command=self._refresh_gateway_ui,
        ).pack(side="left", padx=(14, 0))

        endpoint_bar = ttk.Frame(shell, style="Surface.TFrame")
        endpoint_bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        endpoint_bar.columnconfigure(1, weight=1)
        ttk.Label(endpoint_bar, text="端点", style="FieldLabel.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.gateway_endpoint_var = tk.StringVar(value=self.gateway_config.api_base)
        ttk.Label(
            endpoint_bar, textvariable=self.gateway_endpoint_var, style="Muted.TLabel"
        ).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Button(
            endpoint_bar, text="复制", style="Ghost.TButton",
            command=lambda: self._copy_text(self.gateway_endpoint_var.get()),
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(shell, text="DSH 配置", style="Section.TLabel").grid(
            row=4, column=0, sticky="w", pady=(14, 6)
        )
        snippet_frame = ttk.Frame(shell, style="Card.TFrame", padding=10)
        snippet_frame.grid(row=5, column=0, sticky="ew")
        snippet_frame.columnconfigure(0, weight=1)
        self.gateway_snippet_text = tk.Text(
            snippet_frame,
            height=12,
            background=self.palette["input"],
            foreground=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
            wrap="none",
        )
        self.gateway_snippet_text.grid(row=0, column=0, sticky="ew")
        snippet_scroll = ttk.Scrollbar(
            snippet_frame, orient="vertical", command=self.gateway_snippet_text.yview
        )
        snippet_scroll.grid(row=0, column=1, sticky="ns")
        self.gateway_snippet_text.configure(yscrollcommand=snippet_scroll.set)
        copy_row = ttk.Frame(shell, style="Surface.TFrame")
        copy_row.grid(row=6, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(
            copy_row, text="复制配置", style="Ghost.TButton",
            command=lambda: self._copy_text(self.gateway_snippet_text.get("1.0", "end").strip()),
        ).pack(side="left")
        ttk.Button(
            copy_row, text="写入 DSH 配置", style="Ghost.TButton",
            command=self._install_dsh_provider,
        ).pack(side="left", padx=(8, 0))
        self.gateway_hint_var = tk.StringVar(
            value="路径：~/.dsh/profiles/desktop/cordis.patch.yml；DSH 需要环境变量 CODEX_GATEWAY_TOKEN（点“写入环境变量”）"
        )
        ttk.Label(
            copy_row, textvariable=self.gateway_hint_var, style="SurfaceMuted.TLabel"
        ).pack(side="left", padx=(12, 0))

        ttk.Label(shell, text="请求日志", style="Section.TLabel").grid(
            row=7, column=0, sticky="w", pady=(14, 6)
        )
        log_frame = ttk.Frame(shell, style="Surface.TFrame")
        log_frame.grid(row=8, column=0, sticky="nsew")
        shell.rowconfigure(8, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.gateway_log_tree = ttk.Treeview(
            log_frame,
            columns=("time", "kind", "calls", "seconds", "tokens", "error"),
            show="headings",
            height=6,
        )
        for name, text, width, anchor in (
            ("time", "时间", 90, "center"),
            ("kind", "类型", 90, "center"),
            ("calls", "工具", 60, "center"),
            ("seconds", "耗时", 80, "center"),
            ("tokens", "Token", 110, "center"),
            ("error", "结果", 380, "w"),
        ):
            self.gateway_log_tree.heading(name, text=text)
            self.gateway_log_tree.column(
                name, width=width, minwidth=50, anchor=anchor,
                stretch=name == "error",
            )
        self.gateway_log_tree.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.gateway_log_tree.yview
        )
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.gateway_log_tree.configure(yscrollcommand=log_scroll.set)

        self.gateway_note_var = tk.StringVar(value="")
        ttk.Label(
            shell, textvariable=self.gateway_note_var, style="SurfaceMuted.TLabel",
            wraplength=840, justify="left",
        ).grid(row=9, column=0, sticky="w", pady=(8, 0))

        self._refresh_gateway_ui()
        self._poll_gateway()

    def _sync_gateway_efforts(self) -> None:
        levels = self._efforts_for(self.gateway_model_var.get())
        self.gateway_effort_combo.configure(values=levels)
        if self.gateway_effort_var.get().strip() not in levels:
            self.gateway_effort_var.set(levels[0])

    def _browse_gateway_workdir(self) -> None:
        selected = filedialog.askdirectory(
            parent=self._gateway_window, initialdir=self.gateway_workdir_var.get()
        )
        if selected:
            self.gateway_workdir_var.set(selected)

    def _generate_gateway_token(self) -> None:
        self.gateway_token_var.set(uuid.uuid4().hex)

    def _export_gateway_token(self) -> None:
        """Persist the token as a user environment variable for DSH.

        The pi-ai provider route needs a resolvable credential; without the
        variable DSH never loads the route and the model is absent from the
        picker.
        """
        token = self.gateway_token_var.get().strip()
        if not token:
            messagebox.showerror(APP_NAME, "先点“生成”或填写访问令牌。")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "将把 CODEX_GATEWAY_TOKEN 写入当前用户的持久环境变量（setx）。\n\n"
            "DSH 需要完全重启才会读到它。继续？",
            icon="warning",
        ):
            return
        try:
            completed = subprocess.run(
                ["setx", "CODEX_GATEWAY_TOKEN", token],
                capture_output=True,
                text=True,
                timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            messagebox.showerror(APP_NAME, f"写入环境变量失败：{exc}")
            return
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:200]
            messagebox.showerror(APP_NAME, f"写入环境变量失败：{detail}")
            return
        self.gateway_note_var.set("已写入 CODEX_GATEWAY_TOKEN，完全重启 DSH 后生效")
        messagebox.showinfo(
            APP_NAME,
            "已写入用户环境变量 CODEX_GATEWAY_TOKEN。\n\n"
            "完全退出 DSH 再重新打开，模型列表里才会出现“Codex 本机网关”。",
        )

    def _copy_text(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update_idletasks()
        self.gateway_note_var.set("已复制到剪贴板")

    @staticmethod
    def _dsh_patch_path() -> Path:
        return Path.home() / ".dsh" / "profiles" / "desktop" / "cordis.patch.yml"

    @staticmethod
    def _yaml_list_ok(text: str) -> bool:
        try:
            import yaml  # noqa: PLC0415 - optional, only used for this check
        except ImportError:
            return True
        try:
            node = yaml.compose(text)
        except Exception:
            return False
        return isinstance(node, yaml.SequenceNode)

    def _install_dsh_provider(self) -> None:
        path = self._dsh_patch_path()
        if not path.exists():
            messagebox.showerror(
                APP_NAME,
                f"未找到 DSH 配置：\n{path}\n\n请先启动一次 DSH Desktop，"
                "或点“复制配置”手动粘贴到该文件。",
            )
            return
        config = self._current_gateway_config()
        snippet = dsh_provider_snippet(
            config,
            subagent_model=self._gateway_subagent_model(),
        ).strip()
        if not self._ensure_advisor_config():
            self.gateway_note_var.set("未配置 DeepSeek 顾问，MCP 工具会提示未配置")
        mcp_command, mcp_args = mcp_launch_command()
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"无法读取 DSH 配置：{exc}")
            return
        # Replace our own entries instead of appending duplicates.
        cleaned = strip_dsh_entries(
            existing, ("llm-codex-local", "llm-pi-ai", "tool-subagent", "tool-subagent-fork")
        )
        cleaned = strip_dsh_mcp_insert(cleaned)
        candidate = (
            cleaned.rstrip("\n")
            + "\n"
            + snippet.rstrip("\n")
            + "\n"
            + dsh_mcp_insert(mcp_command, mcp_args)
        )
        if not self._yaml_list_ok(candidate):
            messagebox.showerror(APP_NAME, "写入后的 YAML 无法解析，已取消写入。")
            return
        if not messagebox.askyesno(
            APP_NAME,
            "将写入 DSH 的两处配置（都会先备份）：\n\n"
            f"1. {path}（provider 路由 + 子代理 + DeepSeek 顾问 MCP）\n"
            f"2. {self._dsh_env_path()}（凭据 CODEX_GATEWAY_TOKEN）\n\n"
            "之后完全重启 DSH 才会生效。继续？",
            icon="warning",
        ):
            return
        backup = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        try:
            backup.write_text(existing, encoding="utf-8", newline="\n")
            temporary = path.with_suffix(".tmp")
            temporary.write_text(candidate, encoding="utf-8", newline="\n")
            os.replace(temporary, path)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"写入 DSH 配置失败：{exc}")
            return
        token = config.token or "local"
        try:
            self._write_dsh_env(token)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"写入 DSH 凭据失败：{exc}")
            return
        self.gateway_note_var.set(f"已写入 {path.name} 与 .env（备份：{backup.name}）")
        self._refresh_gateway_ui()
        messagebox.showinfo(
            APP_NAME,
            "已写入 DSH 配置和凭据。\n\n"
            "现在完全退出 DSH 再重新打开（不是关窗口），"
            "模型选择器里会出现“Codex 本机网关 / gpt-6-luna”。\n"
            f"原配置已备份为 {backup.name}。",
        )

    @staticmethod
    def _dsh_env_path() -> Path:
        return Path.home() / ".dsh" / ".env"

    def _write_dsh_env(self, token: str) -> None:
        """DSH resolves provider credentials from $DSH_HOME/.env as a fallback."""
        path = self._dsh_env_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        backup = path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
        if path.exists():
            backup.write_text(existing, encoding="utf-8", newline="\n")
        lines = [
            line
            for line in existing.splitlines()
            if not line.strip().startswith("CODEX_GATEWAY_TOKEN=")
        ]
        lines.append(f"CODEX_GATEWAY_TOKEN={token}")
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8", newline="\n")

    def _gateway_subagent_model(self) -> str:
        """Model DSH should use for its own subagents, or "" to leave it alone."""
        checkbox = getattr(self, "gateway_subagent_var", None)
        if checkbox is None or not checkbox.get():
            return ""
        return self.advisor_config.model or "deepseek-v4.1-flash"

    def _ensure_advisor_config(self) -> bool:
        """Make sure the MCP advisor has an endpoint/key before wiring it in."""
        if self.advisor_config.api_key():
            return True
        try:
            endpoints = self.custom_api_store.load()
        except Exception:  # noqa: BLE001
            endpoints = []
        candidate = next(
            (item for item in endpoints if "deepseek" in item.model.lower()), None
        )
        if candidate is None:
            return False
        base = custom_api_base_url(candidate.url)
        if not messagebox.askyesno(
            APP_NAME,
            "还没有配置 DeepSeek 顾问（luna 通过 MCP 问的那个模型）。\n\n"
            f"用已保存的接口自动配置：\n{base}\n模型：{candidate.model}\n\n继续？",
        ):
            return False
        try:
            key = unprotect_secret(candidate.encrypted_api_key)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"读取已保存的 Key 失败：{exc}")
            return False
        config = AdvisorConfig(
            base_url=base,
            model=candidate.model,
            effort="high",
            encrypted_api_key=encrypt_key(key),
            timeout_seconds=300,
            install_root=str(find_dsh_install() or ""),
        )
        self.advisor_config = config
        try:
            self.advisor_store.save_config(config)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"保存顾问配置失败：{exc}")
            return False
        return True

    def _current_gateway_config(self) -> GatewayConfig:
        workdir = self.gateway_workdir_var.get().strip() or os.getcwd()
        return GatewayConfig(
            host="127.0.0.1",
            port=int(self.gateway_port_var.get()),
            model=self.gateway_model_var.get().strip() or self.gateway_config.model,
            effort=self.gateway_effort_var.get().strip() or "high",
            workdir=workdir,
            token=self.gateway_token_var.get().strip(),
            timeout_seconds=float(self.gateway_timeout_var.get()),
            max_parallel=int(self.gateway_parallel_var.get()),
            auto_start=bool(self.gateway_autostart_var.get()),
        )

    def _autostart_gateway(self) -> None:
        config = self.gateway_config
        if not config.auto_start:
            return
        executable = find_codex_executable()
        if executable is None:
            self.status_var.set("网关未启动：未找到 Codex CLI")
            return
        workdir = Path(config.workdir or os.getcwd())
        if not workdir.is_dir():
            self.status_var.set(f"网关未启动：工作目录不存在 {workdir}")
            return
        gateway = CodexGateway(config, executable)
        try:
            gateway.start()
        except GatewayError as exc:
            self.status_var.set(f"网关未启动：{exc}")
            return
        self._gateway = gateway
        self.status_var.set(f"网关已启动 · {gateway.api_base}")

    def _start_gateway(self) -> None:
        executable = find_codex_executable()
        if executable is None:
            messagebox.showerror(APP_NAME, "未找到可执行的 Codex CLI。")
            return
        try:
            config = self._current_gateway_config()
            workdir = Path(config.workdir).expanduser()
            if not workdir.is_dir():
                raise ValueError(f"工作目录不存在：{workdir}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法启动网关：{exc}")
            return
        if self._gateway is not None:
            self._gateway.stop()
        gateway = CodexGateway(config, executable)
        try:
            gateway.start()
        except GatewayError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self._gateway = gateway
        self.gateway_config = config
        try:
            config.save(self.gateway_config_path)
        except OSError:
            pass
        self.gateway_note_var.set(f"已启动 · {gateway.api_base}")
        self._refresh_gateway_ui()

    def _stop_gateway(self) -> None:
        if self._gateway is not None:
            self._gateway.stop()
            self._gateway = None
            self.gateway_note_var.set("网关已停止")
        self._refresh_gateway_ui()

    def _refresh_gateway_ui(self) -> None:
        if self._gateway_window is None or not self._gateway_window.winfo_exists():
            return
        running = self._gateway is not None and self._gateway.running
        endpoint = (
            self._gateway.api_base
            if running
            else self._current_gateway_config().api_base
        )
        self.gateway_endpoint_var.set(endpoint)
        self.gateway_status_var.set(
            f"运行中 · {len(self._gateway.records())} 次请求" if running and self._gateway else "未启动"
        )
        self.gateway_start_button.configure(state="disabled" if running else "normal")
        self.gateway_stop_button.configure(state="normal" if running else "disabled")
        for widget in (
            self.gateway_model_combo,
            self.gateway_effort_combo,
        ):
            widget.configure(state="disabled" if running else "normal")
        snippet = dsh_provider_snippet(
            self._current_gateway_config(),
            subagent_model=self._gateway_subagent_model(),
        )
        self.gateway_snippet_text.configure(state="normal")
        self.gateway_snippet_text.delete("1.0", "end")
        self.gateway_snippet_text.insert("1.0", snippet)
        self.gateway_snippet_text.configure(state="disabled")

    def _poll_gateway(self) -> None:
        self._gateway_poll_job = None
        if self._gateway_window is None or not self._gateway_window.winfo_exists():
            return
        if self._gateway is not None:
            records = self._gateway.records()
            tree = self.gateway_log_tree
            for item in tree.get_children():
                tree.delete(item)
            for record in reversed(records[-40:]):
                error = record.error or (
                    f"tool_calls ×{record.tool_calls}"
                    if record.kind == "tool_calls"
                    else "final"
                )
                tree.insert(
                    "",
                    "end",
                    values=(
                        time.strftime("%H:%M:%S", time.localtime(record.started_at)),
                        record.kind,
                        record.tool_calls,
                        f"{record.seconds:.1f}s",
                        f"{record.prompt_tokens}/{record.completion_tokens}",
                        error,
                    ),
                )
            self.gateway_status_var.set(
                f"运行中 · {self._gateway.api_base} · {len(records)} 次请求"
            )
        try:
            self._gateway_poll_job = self._gateway_window.after(1000, self._poll_gateway)
        except tk.TclError:
            self._gateway_poll_job = None

    def _close_gateway_window(self) -> None:
        if self._gateway_poll_job is not None:
            try:
                self._gateway_window.after_cancel(self._gateway_poll_job)  # type: ignore[union-attr]
            except tk.TclError:
                pass
            self._gateway_poll_job = None
        if self._gateway_window is not None:
            self._gateway_window.destroy()
        self._gateway_window = None


    # ------------------------------------------------------- Codex advisor
    def open_advisor(self) -> None:
        if self._advisor_window is not None and self._advisor_window.winfo_exists():
            self._advisor_window.deiconify()
            self._advisor_window.lift()
            self._advisor_window.focus_force()
            return

        window = tk.Toplevel(self.root)
        self._advisor_window = window
        window.title(f"接入 Codex · {APP_NAME} {APP_VERSION}")
        window.geometry("940x800")
        window.minsize(880, 720)
        window.configure(bg="#0b0e12")
        window.protocol("WM_DELETE_WINDOW", self._close_advisor_window)

        shell = ttk.Frame(window, padding=(20, 16))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(7, weight=1)

        header = ttk.Frame(shell)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="接入 Codex", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.advisor_status_var = tk.StringVar(value="")
        ttk.Label(
            header, textvariable=self.advisor_status_var, style="Subtitle.TLabel"
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Button(
            header, text="保存配置", style="Primary.TButton", command=self._save_advisor
        ).grid(row=0, column=2, sticky="e")

        ttk.Label(
            shell,
            text=(
                "luna 当主模型，遇到 UI/审美、架构、连续失败或大改动时，"
                "通过 MCP 工具 ask_deepseek 找这个常驻 DeepSeek 子会话。\n"
                "它和“运行设置”里的 Codex 子代理是两条独立链路；"
                "用这个顾问时建议模式选“单代理”，避免同时再派一个 Codex 子代理。"
            ),
            style="Subtitle.TLabel",
            wraplength=860,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        # ---- provider card
        provider = ttk.Frame(shell, style="Card.TFrame", padding=14)
        provider.grid(row=2, column=0, sticky="ew")
        provider.columnconfigure(1, weight=1)
        self.advisor_base_var = tk.StringVar(value=self.advisor_config.base_url)
        self.advisor_model_var = tk.StringVar(value=self.advisor_config.model)
        self.advisor_key_var = tk.StringVar(value="")
        self.advisor_root_var = tk.StringVar(
            value=self.advisor_config.install_root
            or str(find_dsh_install() or "")
        )
        self.advisor_timeout_var = tk.IntVar(
            value=int(self.advisor_config.timeout_seconds)
        )
        self.advisor_effort_var = tk.StringVar(value=self.advisor_config.effort)

        ttk.Label(provider, text="DeepSeek 端点", style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        ttk.Entry(provider, textvariable=self.advisor_base_var).grid(
            row=0, column=1, sticky="ew"
        )
        ttk.Label(provider, text="模型", style="CardMuted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(12, 8)
        )
        ttk.Entry(provider, textvariable=self.advisor_model_var, width=22).grid(
            row=0, column=3, sticky="e"
        )

        ttk.Label(provider, text="API Key", style="CardMuted.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0)
        )
        self.advisor_key_entry = ttk.Entry(
            provider, textvariable=self.advisor_key_var, show="*"
        )
        self.advisor_key_entry.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        buttons = ttk.Frame(provider, style="Card.TFrame")
        buttons.grid(row=1, column=2, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(
            buttons, text="从已存 API 填入", style="Ghost.TButton",
            command=self._fill_advisor_key,
        ).pack(side="left")
        ttk.Button(
            buttons, text="测试端点", style="Ghost.TButton",
            command=self._test_advisor_endpoint,
        ).pack(side="left", padx=(6, 0))

        ttk.Label(provider, text="DSH 安装目录", style="CardMuted.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=(10, 0)
        )
        ttk.Entry(provider, textvariable=self.advisor_root_var).grid(
            row=2, column=1, columnspan=2, sticky="ew", pady=(10, 0)
        )
        ttk.Button(
            provider, text="浏览", command=self._browse_advisor_root
        ).grid(row=2, column=3, sticky="e", pady=(10, 0))

        limits = ttk.Frame(provider, style="Card.TFrame")
        limits.grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Label(limits, text="单次超时(s)", style="CardMuted.TLabel").pack(side="left")
        ttk.Spinbox(
            limits, from_=30, to=3600, textvariable=self.advisor_timeout_var, width=7
        ).pack(side="left", padx=(8, 0))
        ttk.Label(limits, text="思考级别", style="CardMuted.TLabel").pack(
            side="left", padx=(14, 0)
        )
        ttk.Combobox(
            limits,
            textvariable=self.advisor_effort_var,
            values=ADVISOR_EFFORTS,
            state="readonly",
            width=9,
        ).pack(side="left", padx=(8, 0))
        self.advisor_key_state_var = tk.StringVar(value="")
        ttk.Label(
            limits, textvariable=self.advisor_key_state_var, style="CardMuted.TLabel"
        ).pack(side="left", padx=(14, 0))

        # ---- codex integration card
        integration = ttk.Frame(shell, style="Surface.TFrame")
        integration.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        integration.columnconfigure(1, weight=1)
        ttk.Label(integration, text="Codex 集成", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(
            integration, text="写入 MCP 配置", command=self._install_advisor_mcp
        ).grid(row=0, column=2, sticky="e")
        ttk.Button(
            integration, text="写入 AGENTS 规则", command=self._install_advisor_policy
        ).grid(row=0, column=3, sticky="e", padx=(8, 0))
        self.advisor_integration_var = tk.StringVar(value="")
        ttk.Label(
            integration,
            textvariable=self.advisor_integration_var,
            style="SurfaceMuted.TLabel",
            wraplength=860,
            justify="left",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # ---- sessions card
        sessions_card = ttk.Frame(shell, style="Surface.TFrame")
        sessions_card.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        sessions_card.columnconfigure(0, weight=1)
        sessions_header = ttk.Frame(sessions_card, style="Surface.TFrame")
        sessions_header.grid(row=0, column=0, sticky="ew")
        sessions_header.columnconfigure(0, weight=1)
        ttk.Label(
            sessions_header, text="顾问会话", style="Section.TLabel"
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            sessions_header, text="重置所选", style="Ghost.TButton",
            command=self._reset_advisor_session,
        ).grid(row=0, column=1, sticky="e")
        self.advisor_session_tree = ttk.Treeview(
            sessions_card,
            columns=("project", "session", "updated"),
            show="headings",
            height=4,
        )
        for name, text, width, anchor in (
            ("project", "项目目录", 420, "w"),
            ("session", "会话", 260, "w"),
            ("updated", "更新时间", 120, "center"),
        ):
            self.advisor_session_tree.heading(name, text=text)
            self.advisor_session_tree.column(
                name, width=width, minwidth=80, anchor=anchor,
                stretch=name == "project",
            )
        self.advisor_session_tree.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        # ---- trial card
        trial = ttk.Frame(shell, style="Surface.TFrame")
        trial.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        trial.columnconfigure(1, weight=1)
        ttk.Label(trial, text="试问一句", style="Section.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.advisor_trial_var = tk.StringVar(value="页面看起来乱，先给我三条最该改的")
        ttk.Entry(trial, textvariable=self.advisor_trial_var).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0)
        )
        self.advisor_ask_button = ttk.Button(
            trial, text="发送", style="Primary.TButton", command=self._run_advisor_trial
        )
        self.advisor_ask_button.grid(row=1, column=2, sticky="e", padx=(8, 0))
        self.advisor_trial_project_var = tk.StringVar(value=f"目录：{os.getcwd()}")
        ttk.Label(
            trial, textvariable=self.advisor_trial_project_var,
            style="SurfaceMuted.TLabel",
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        # ---- answer log
        ttk.Label(shell, text="回答", style="Section.TLabel").grid(
            row=6, column=0, sticky="w", pady=(12, 6)
        )
        log_frame = ttk.Frame(shell, style="Card.TFrame", padding=10)
        log_frame.grid(row=7, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.advisor_answer_text = tk.Text(
            log_frame,
            height=10,
            background=self.palette["input"],
            foreground=self.palette["text"],
            insertbackground=self.palette["text"],
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 9),
            wrap="word",
        )
        self.advisor_answer_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(
            log_frame, orient="vertical", command=self.advisor_answer_text.yview
        )
        scroll.grid(row=0, column=1, sticky="ns")
        self.advisor_answer_text.configure(yscrollcommand=scroll.set)

        self._refresh_advisor_ui()

    # ---- advisor helpers
    def _advisor_key_secret(self) -> str:
        typed = self.advisor_key_var.get().strip()
        if typed:
            return typed
        return self.advisor_config.api_key()

    def _current_advisor_config(self) -> AdvisorConfig:
        key = self._advisor_key_secret()
        return AdvisorConfig(
            base_url=self.advisor_base_var.get().strip(),
            model=self.advisor_model_var.get().strip(),
            effort=self.advisor_effort_var.get().strip() or "high",
            encrypted_api_key=(
                encrypt_key(key) if key else self.advisor_config.encrypted_api_key
            ),
            timeout_seconds=float(self.advisor_timeout_var.get()),
            install_root=self.advisor_root_var.get().strip(),
        )

    def _refresh_advisor_ui(self) -> None:
        if self._advisor_window is None or not self._advisor_window.winfo_exists():
            return
        has_key = bool(self.advisor_config.api_key())
        self.advisor_key_state_var.set(
            "Key 已保存（留空则不修改）" if has_key else "尚未保存 Key"
        )
        self.advisor_status_var.set(
            f"模型 {self.advisor_config.model} · "
            + ("已就绪" if has_key else "待配置")
        )
        for item in self.advisor_session_tree.get_children():
            self.advisor_session_tree.delete(item)
        for project, session, updated in self.advisor_store.sessions().items():
            self.advisor_session_tree.insert(
                "",
                "end",
                values=(
                    project,
                    session or "（未建立）",
                    time.strftime("%m-%d %H:%M", time.localtime(updated)) if updated else "--",
                ),
            )
        codex_config = self.config_store.path
        has_mcp = (
            codex_config.exists()
            and f"[mcp_servers.{MCP_SERVER_NAME}]" in codex_config.read_text(encoding="utf-8")
        )
        agents_path = self.home / "AGENTS.md"
        has_policy = agents_path.exists() and has_advisor_policy(
            agents_path.read_text(encoding="utf-8")
        )
        self.advisor_integration_var.set(
            "MCP：" + ("已写入" if has_mcp else "未写入")
            + "　规则：" + ("已写入" if has_policy else "未写入")
            + "　（改完配置需要重启 Codex 才生效）"
        )

    def _save_advisor(self) -> None:
        try:
            config = self._current_advisor_config()
            if not config.base_url or not config.model:
                raise ValueError("端点和模型不能为空")
            if not config.api_key():
                raise ValueError("请填写 DeepSeek API Key")
            if not find_dsh_install(config.install_root):
                raise ValueError("找不到 DeepSeek Harness 安装目录")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"无法保存：{exc}")
            return
        self.advisor_config = config
        try:
            self.advisor_store.save_config(config)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"保存失败：{exc}")
            return
        self.advisor_key_var.set("")
        self.advisor_status_var.set(f"已保存 · {config.model}")
        self._refresh_advisor_ui()

    def _fill_advisor_key(self) -> None:
        try:
            endpoints = self.custom_api_store.load()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"读取已存 API 失败：{exc}")
            return
        candidate = next(
            (item for item in endpoints if "deepseek" in item.model.lower()),
            None,
        )
        if candidate is None:
            messagebox.showinfo(APP_NAME, "已保存的自定义 API 里没有 DeepSeek 模型。")
            return
        try:
            key = unprotect_secret(candidate.encrypted_api_key)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"解密失败：{exc}")
            return
        self.advisor_base_var.set(custom_api_base_url(candidate.url))
        self.advisor_model_var.set(candidate.model)
        self.advisor_key_var.set(key)
        self.advisor_status_var.set("已填入，记得点“保存配置”")

    def _browse_advisor_root(self) -> None:
        selected = filedialog.askdirectory(
            parent=self._advisor_window, initialdir=self.advisor_root_var.get() or None
        )
        if selected:
            self.advisor_root_var.set(selected)

    def _test_advisor_endpoint(self) -> None:
        key = self._advisor_key_secret()
        base = self.advisor_base_var.get().strip().rstrip("/")
        model = self.advisor_model_var.get().strip()
        if not key:
            messagebox.showerror(APP_NAME, "请先填写或填入 API Key。")
            return

        def worker() -> None:
            body = json.dumps(
                {
                    "model": model,
                    "messages": [{"role": "user", "content": "只回复两个字符：OK"}],
                    "max_tokens": 8,
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                f"{base}/chat/completions",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = json.loads(response.read().decode("utf-8"))
                message = (data.get("choices") or [{}])[0].get("message") or {}
                self._post(
                    lambda: self._advisor_note(
                        f"端点可用，返回：{str(message.get('content'))[:40]}"
                    )
                )
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:160]
                self._post(lambda: self._advisor_note(f"端点失败 HTTP {exc.code}：{detail}"))
            except Exception as exc:  # noqa: BLE001
                self._post(lambda: self._advisor_note(f"端点失败：{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def _advisor_note(self, message: str) -> None:
        if self._advisor_window is None or not self._advisor_window.winfo_exists():
            return
        self.advisor_status_var.set(message)

    def _install_advisor_mcp(self) -> None:
        config = self._current_advisor_config()
        config_path = self.config_store.path
        if not config_path.exists():
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text("", encoding="utf-8")
        try:
            existing = config_path.read_text(encoding="utf-8")
            command, args = mcp_launch_command()
            updated = merge_mcp_config(existing, command, args)
            tomllib.loads(updated)
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"MCP 配置无效，已取消写入：{exc}")
            return
        backup = config_path.with_name(
            config_path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
        )
        try:
            backup.write_text(existing, encoding="utf-8", newline="")
            ConfigStore._write_text_atomically(config_path, updated)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"写入失败：{exc}")
            return
        self._refresh_advisor_ui()
        messagebox.showinfo(
            APP_NAME,
            "已写入 MCP 配置（备份：%s）。\n\n重启 Codex 后，"
            "主模型就能看到 ask_deepseek 工具。" % backup.name,
        )

    def _install_advisor_policy(self) -> None:
        path = self.home / "AGENTS.md"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        updated = merge_advisor_policy(existing, True)
        if updated == existing:
            messagebox.showinfo(APP_NAME, "规则已经是最新的。")
            return
        backup = (
            path.with_name(path.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
            if path.exists()
            else None
        )
        try:
            if backup is not None:
                backup.write_text(existing, encoding="utf-8", newline="")
            ConfigStore._write_text_atomically(path, updated)
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"写入失败：{exc}")
            return
        self._refresh_advisor_ui()
        messagebox.showinfo(
            APP_NAME,
            "已把“何时必须找子模型”的规则写入 ~/.codex/AGENTS.md。"
            + (f"\n备份：{backup.name}" if backup else ""),
        )

    def _reset_advisor_session(self) -> None:
        selection = self.advisor_session_tree.selection()
        if not selection:
            messagebox.showinfo(APP_NAME, "先在上面选一个项目。")
            return
        values = self.advisor_session_tree.item(selection[0], "values")
        project = Path(str(values[0]))
        self.advisor_store.sessions().clear(project)
        self._refresh_advisor_ui()
        self.advisor_status_var.set(f"已重置 {project.name} 的顾问会话")

    def _run_advisor_trial(self) -> None:
        question = self.advisor_trial_var.get().strip()
        if not question:
            return
        try:
            config = self._current_advisor_config()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, f"配置有误：{exc}")
            return
        project = Path.cwd()
        self.advisor_ask_button.configure(state="disabled")
        self.advisor_status_var.set("正在问子模型…")
        self._append_advisor_answer(f"\n—— 试问：{question}\n")

        def worker() -> None:
            runner = self.advisor_store.runner(config, self.advisor_store.sessions())
            try:
                answer = runner.consult(question, "", project)
            except AdvisorError as exc:
                self._post(lambda: self._finish_advisor_trial(f"失败：{exc}", False))
                return
            except Exception as exc:  # noqa: BLE001
                self._post(lambda: self._finish_advisor_trial(f"异常：{exc}", False))
                return
            text = answer.text if answer.ok else f"失败：{answer.error}"
            self._post(lambda: self._finish_advisor_trial(text, answer.ok, answer.seconds))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_advisor_trial(self, text: str, ok: bool, seconds: float = 0.0) -> None:
        self.advisor_ask_button.configure(state="normal")
        self._append_advisor_answer(text)
        self.advisor_status_var.set(
            f"{'完成' if ok else '失败'} · {seconds:.1f}s" if seconds else ("完成" if ok else "失败")
        )
        self._refresh_advisor_ui()

    def _append_advisor_answer(self, text: str) -> None:
        if self._advisor_window is None or not self._advisor_window.winfo_exists():
            return
        self.advisor_answer_text.configure(state="normal")
        self.advisor_answer_text.insert("end", text + "\n")
        self.advisor_answer_text.see("end")
        self.advisor_answer_text.configure(state="disabled")

    def _close_advisor_window(self) -> None:
        self._save_advisor_silently()
        if self._advisor_window is not None:
            self._advisor_window.destroy()
        self._advisor_window = None

    def _save_advisor_silently(self) -> None:
        try:
            if self.advisor_key_var.get().strip() or self.advisor_config.encrypted_api_key:
                config = self._current_advisor_config()
                if config.base_url and config.model and config.api_key():
                    self.advisor_config = config
                    self.advisor_store.save_config(config)
        except Exception:  # noqa: BLE001
            return


def main() -> None:
    if "--mcp-advisor" in sys.argv[1:]:
        # Packaged builds serve as the MCP entry point too, so Codex can start
        # the same executable without shipping the Python sources.
        import codex_mcp_advisor

        raise SystemExit(codex_mcp_advisor.main())
    root = tk.Tk()
    CodexAgentConsole(root)
    root.mainloop()


if __name__ == "__main__":
    main()
