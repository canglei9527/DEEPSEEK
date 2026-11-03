# -*- coding: utf-8 -*-
"""Persistent DeepSeek advisor sub-session driven through the DeepSeek Harness CLI.

The Codex main model (whatever it is) can call this advisor through the MCP
server in ``codex_mcp_advisor.py``.  Each consultation resumes one long-lived
``dsh --profile headless --session-id <id>`` session per project, so the advisor
keeps its own memory, tools and working directory instead of starting over.

Layout of the pieces:

* ``AdvisorConfig``     — endpoint/model/key and the DSH install location.
* ``AdvisorSessions``   — project directory -> persistent DSH session id.
* ``AdvisorRunner``     — builds and runs one CLI consultation.
* ``AdvisorStore``      — everything persisted under ``~/.codex``.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

ADVISOR_CONFIG_NAME = "codex-agent-console-advisor.json"
ADVISOR_PATCH_NAME = "codex-agent-console-advisor-patch.yml"
ADVISOR_VERSION = "1"
# deepseek-v4.1-flash rejects medium/xhigh with "does not support reasoning
# effort"; these three are the levels the endpoint accepts.
ADVISOR_EFFORTS = ("low", "high", "max")
DEFAULT_TIMEOUT_SECONDS = 300.0
MAX_CONTEXT_CHARS = 60_000
MAX_QUESTION_CHARS = 20_000

ADVISOR_PROMPT = """你是主模型请来的资深工程与设计顾问。

主模型正在推进一个任务，只把当前这个具体问题交给你。规则：
1. 只回答被问的问题，不要接管整个任务，也不要重写无关代码。
2. 需要看代码或文件就直接读，工作目录已经指向项目根目录。
3. 给结论，再给可直接执行的改法：文件路径、要替换的片段、完整的新代码或命令。
4. 涉及界面时，直接给出具体数值（间距、字号、颜色、层级），不要只说“优化一下”。
5. 不确定的地方明确说不知道，不要编造文件内容或命令输出。

项目根目录：{project}
"""


class AdvisorError(Exception):
    """A consultation failed in a way the caller should see."""


# --------------------------------------------------------------------------- install
def _registry_install_roots() -> list[Path]:
    if sys.platform != "win32":
        return []
    roots: list[Path] = []
    try:
        import winreg
    except ImportError:
        return roots
    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    for hive, subkey in keys:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, name) as entry:
                            display = str(
                                winreg.QueryValueEx(entry, "DisplayName")[0] or ""
                            )
                            if "DeepSeek Harness" not in display:
                                continue
                            location = str(
                                winreg.QueryValueEx(entry, "InstallLocation")[0] or ""
                            )
                            if location and Path(location).is_dir():
                                roots.append(Path(location))
                    except OSError:
                        continue
        except OSError:
            continue
    return roots


def _scan_install_roots() -> list[Path]:
    """Look for a portable install: a folder named after the product.

    Portable copies (the common case for 0.2.x) register nothing, so the only
    signal is the directory name itself. The scan is two levels deep from each
    drive root plus the usual program folders.
    """
    hints = ("harness", "deepseek")
    bases: list[Path] = []
    for letter in "CDEFG":
        bases.append(Path(f"{letter}:\\"))
    for env_name in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)"):
        value = os.environ.get(env_name, "").strip()
        if value:
            bases.append(Path(value))
            bases.append(Path(value) / "Programs")

    def interesting(name: str) -> bool:
        lowered = name.lower()
        return any(hint in lowered for hint in hints)

    roots: list[Path] = []
    for base in bases:
        try:
            children = list(base.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                if not child.is_dir():
                    continue
            except OSError:
                continue
            if interesting(child.name) and _looks_like_install(child):
                roots.append(child)
                continue
            if not interesting(child.name):
                continue
            try:
                grandchildren = list(child.iterdir())
            except OSError:
                continue
            for grandchild in grandchildren:
                try:
                    if grandchild.is_dir() and _looks_like_install(grandchild):
                        roots.append(grandchild)
                except OSError:
                    continue
    return roots


def find_dsh_install(explicit: str = "") -> Path | None:
    """Locate the DeepSeek Harness install root (contains the Electron exe)."""
    candidates: list[Path] = []
    if explicit.strip():
        candidates.append(Path(explicit.strip()))
    env = os.environ.get("DSH_INSTALL_ROOT", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.extend(_registry_install_roots())
    for root in candidates:
        if _looks_like_install(root):
            return root
    for root in _scan_install_roots():
        return root
    return None


def _looks_like_install(root: Path) -> bool:
    # The CLI lives inside app.asar, which only Electron can stat; check the
    # launcher and the app bundle instead.
    return (root / "DeepSeek Harness.exe").is_file() and (
        root / "resources" / "app.asar"
    ).exists()


def dsh_cli_paths(root: Path) -> tuple[Path, Path]:
    executable = root / "DeepSeek Harness.exe"
    cli = (
        root
        / "resources"
        / "app.asar"
        / "dsh"
        / "node_modules"
        / "@deepseek-ai"
        / "dsh-desktop-host"
        / "lib"
        / "cli.js"
    )
    return executable, cli


# --------------------------------------------------------------------------- config
def _decrypt(blob: str) -> str:
    if not blob:
        return ""
    from codex_agent_console import unprotect_secret  # local import avoids a cycle

    return unprotect_secret(blob)


def encrypt_key(plain: str) -> str:
    if not plain:
        return ""
    from codex_agent_console import protect_secret

    return protect_secret(plain)


@dataclass
class AdvisorConfig:
    base_url: str = "https://a-piko.top/v1"
    model: str = "deepseek-v4.1-flash"
    effort: str = "high"
    encrypted_api_key: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    install_root: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "version": ADVISOR_VERSION,
            "baseUrl": self.base_url,
            "model": self.model,
            "effort": self.effort,
            "encryptedApiKey": self.encrypted_api_key,
            "timeoutSeconds": self.timeout_seconds,
            "installRoot": self.install_root,
        }

    @classmethod
    def from_dict(cls, value: object) -> AdvisorConfig:
        data = value if isinstance(value, dict) else {}
        try:
            timeout = float(data.get("timeoutSeconds") or DEFAULT_TIMEOUT_SECONDS)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_SECONDS
        effort = str(data.get("effort") or "high").strip().lower()
        if effort not in ADVISOR_EFFORTS:
            effort = "high"
        return cls(
            base_url=str(data.get("baseUrl") or "https://a-piko.top/v1").strip(),
            model=str(data.get("model") or "deepseek-v4.1-flash").strip(),
            effort=effort,
            encrypted_api_key=str(data.get("encryptedApiKey") or ""),
            timeout_seconds=max(30.0, min(3600.0, timeout)),
            install_root=str(data.get("installRoot") or "").strip(),
        )

    def api_key(self) -> str:
        return _decrypt(self.encrypted_api_key)

    def patch_text(self) -> str:
        return "\n".join(
            [
                "# Managed by Codex Agent Console",
                "- id: llm-deepseek",
                "  name: '@deepseek-ai/dsh-llm-deepseek-api-key'",
                "  config:",
                f"    baseURL: {self.base_url}",
                "    models:",
                f"      - id: {self.model}",
                "        name: DeepSeek V4.1 Flash",
                "        contextWindow: 1000000",
                "        inputModalities:",
                "          - text",
                "- id: agent-default-model",
                "  name: '@deepseek-ai/dsh-agent-default-model'",
                "  config:",
                "    provider: deepseek-official",
                f"    model: {self.model}",
                f"    reasoningEffort: {self.effort}",
                "",
            ]
        )


# --------------------------------------------------------------------------- sessions
class AdvisorSessions:
    """Project directory -> persistent DSH session id."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, object]] = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        sessions = raw.get("sessions") if isinstance(raw, dict) else None
        if not isinstance(sessions, dict):
            return {}
        return {
            str(key): value
            for key, value in sessions.items()
            if isinstance(value, dict) and isinstance(value.get("sessionId"), str)
        }

    def _save(self) -> None:
        payload = {"version": ADVISOR_VERSION, "sessions": self._data}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            os.replace(temporary, self.path)
        except OSError:
            return

    @staticmethod
    def project_key(project: Path) -> str:
        try:
            return str(project.resolve())
        except OSError:
            return str(project.absolute())

    def get(self, project: Path) -> str:
        with self._lock:
            entry = self._data.get(self.project_key(project))
        return str(entry.get("sessionId") or "") if entry else ""

    def set(self, project: Path, session_id: str) -> None:
        with self._lock:
            self._data[self.project_key(project)] = {
                "sessionId": session_id,
                "updated": time.time(),
            }
            self._save()

    def clear(self, project: Path) -> None:
        with self._lock:
            self._data.pop(self.project_key(project), None)
            self._save()

    def items(self) -> list[tuple[str, str, float]]:
        with self._lock:
            rows = [
                (key, str(value.get("sessionId") or ""), float(value.get("updated") or 0))
                for key, value in self._data.items()
            ]
        return sorted(rows, key=lambda row: -row[2])


# --------------------------------------------------------------------------- runner
@dataclass
class AdvisorAnswer:
    text: str
    session_id: str
    seconds: float
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def build_advisor_prompt(question: str, context: str, project: Path) -> str:
    question = _clip(question.strip(), MAX_QUESTION_CHARS)
    context = _clip(context.strip(), MAX_CONTEXT_CHARS)
    parts = [ADVISOR_PROMPT.format(project=project), "\n## 要回答的问题\n", question]
    if context:
        parts.extend(["\n## 主模型补充的片段/路径\n", context])
    return "\n".join(parts)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断 {len(text) - limit} 字符）"


class AdvisorRunner:
    """Runs one consultation as a fresh ``dsh`` process resuming a stored session."""

    def __init__(
        self,
        config: AdvisorConfig,
        sessions: AdvisorSessions,
        patch_path: Path,
    ):
        self.config = config
        self.sessions = sessions
        self.patch_path = patch_path

    def _write_patch(self) -> None:
        self.patch_path.parent.mkdir(parents=True, exist_ok=True)
        self.patch_path.write_text(
            self.config.patch_text(), encoding="utf-8", newline="\n"
        )

    def build_command(self, question: str, session_id: str) -> list[str]:
        root = find_dsh_install(self.config.install_root)
        if root is None:
            raise AdvisorError(
                "找不到 DeepSeek Harness 安装目录，请在“接入 Codex”里指定安装路径"
            )
        executable, cli = dsh_cli_paths(root)
        command = [
            str(executable),
            "--expose-internals",
            str(cli),
            "--profile",
            "headless",
            "--patch",
            str(self.patch_path),
            "--json",
        ]
        if session_id:
            command.extend(["--session-id", session_id])
        command.append(question)
        return command

    def consult(
        self,
        question: str,
        context: str,
        project: Path,
        on_progress: Callable[[str], None] | None = None,
    ) -> AdvisorAnswer:
        key = self.config.api_key()
        if not key:
            raise AdvisorError("还没有配置 DeepSeek API Key")
        self._write_patch()
        session_id = self.sessions.get(project)
        prompt = build_advisor_prompt(question, context, project)
        command = self.build_command(prompt, session_id)
        env = dict(os.environ)
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["DEEPSEEK_API_KEY"] = key
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                cwd=str(project),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.config.timeout_seconds,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except subprocess.TimeoutExpired:
            return AdvisorAnswer(
                text="",
                session_id=session_id,
                seconds=time.perf_counter() - started,
                error=f"子模型超时（{self.config.timeout_seconds:.0f}s）",
            )
        except OSError as exc:
            return AdvisorAnswer(
                text="",
                session_id=session_id,
                seconds=time.perf_counter() - started,
                error=f"无法启动子会话：{exc}",
            )
        elapsed = time.perf_counter() - started
        new_session = session_id
        final_text = ""
        error = ""
        for line in (completed.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "session":
                value = event.get("sessionId")
                if isinstance(value, str) and value:
                    new_session = value
            elif event.get("type") == "final":
                value = event.get("text")
                if isinstance(value, str):
                    final_text = value
            elif event.get("type") == "status" and isinstance(event.get("reason"), dict):
                if event["reason"].get("kind") == "error":
                    error = _event_error(event["reason"])
            if on_progress is not None and event.get("type") == "status":
                phase = event.get("phase")
                if isinstance(phase, str):
                    on_progress(phase)
        if new_session and new_session != session_id:
            self.sessions.set(project, new_session)
        if completed.returncode != 0 and not final_text:
            detail = (completed.stderr or "").strip()[-500:]
            return AdvisorAnswer(
                text="",
                session_id=new_session,
                seconds=elapsed,
                error=error or detail or f"子会话退出码 {completed.returncode}",
            )
        if not final_text.strip():
            return AdvisorAnswer(
                text="",
                session_id=new_session,
                seconds=elapsed,
                error=error or "子模型没有返回内容",
            )
        return AdvisorAnswer(
            text=final_text.strip(), session_id=new_session, seconds=elapsed
        )


def _event_error(reason: dict[str, object]) -> str:
    error = reason.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    if isinstance(error, str):
        return error.strip()
    return "子会话执行失败"


# --------------------------------------------------------------------------- store
@dataclass
class AdvisorStore:
    config_path: Path
    sessions_path: Path
    patch_path: Path

    @classmethod
    def for_home(cls, home: Path) -> AdvisorStore:
        return cls(
            config_path=home / ADVISOR_CONFIG_NAME,
            sessions_path=home / "codex-agent-console-advisor-sessions.json",
            patch_path=home / ADVISOR_PATCH_NAME,
        )

    def load_config(self) -> AdvisorConfig:
        try:
            return AdvisorConfig.from_dict(
                json.loads(self.config_path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError):
            return AdvisorConfig()

    def save_config(self, config: AdvisorConfig) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, self.config_path)

    def sessions(self) -> AdvisorSessions:
        return AdvisorSessions(self.sessions_path)

    def runner(self, config: AdvisorConfig, sessions: AdvisorSessions) -> AdvisorRunner:
        return AdvisorRunner(config, sessions, self.patch_path)


# --------------------------------------------------------------------------- policy
ADVISOR_POLICY_START = "[Codex Agent Console: deepseek advisor]"
ADVISOR_POLICY_END = "[/Codex Agent Console: deepseek advisor]"
_POLICY_RE = re.compile(
    rf"{re.escape(ADVISOR_POLICY_START)}.*?{re.escape(ADVISOR_POLICY_END)}",
    re.DOTALL,
)


def build_advisor_policy() -> str:
    return f"""{ADVISOR_POLICY_START}
A DeepSeek advisor is available through the `deepseek_advisor` MCP server (tool `ask_deepseek`). It is a persistent session per project: same memory, its own tools, and the same working directory.

Call `ask_deepseek` before acting when any of these is true:
1. UI work: layout, spacing, typography, colour, visual hierarchy, interaction flow, product copy.
2. Architecture: module boundaries, directory structure, interface design, dependency choice, data model.
3. The same error or failing test has resisted two fixes.
4. A change touches three or more files, or deletes/migrates existing code.

How to ask:
- Put the concrete question in `question`; put file paths and line ranges in `context`. Do not paste the whole transcript.
- The advisor reads files itself, so prefer paths over long excerpts.
- Keep your own plan: you own execution, verification and the final report. The advisor gives direction, not a takeover.

If `ask_deepseek` fails or times out, continue with your own judgement and state in the final response that the advisor was unavailable. Do not silently pretend it was consulted.
{ADVISOR_POLICY_END}"""


def merge_advisor_policy(text: str, enabled: bool) -> str:
    existing = text if isinstance(text, str) else ""
    preserved = _POLICY_RE.sub("", existing).strip()
    if not enabled:
        return preserved
    return f"{preserved}\n\n{build_advisor_policy()}".strip()


def has_advisor_policy(text: object) -> bool:
    return isinstance(text, str) and bool(_POLICY_RE.search(text))


# --------------------------------------------------------------------------- codex config
MCP_SERVER_NAME = "deepseek_advisor"


def build_mcp_toml(command: str, args: list[str]) -> str:
    return "\n".join(
        [
            f"[mcp_servers.{MCP_SERVER_NAME}]",
            f"command = {json.dumps(command)}",
            f"args = {json.dumps(args)}",
            "",
        ]
    )


def mcp_launch_command() -> tuple[str, list[str]]:
    """How Codex should start the advisor MCP server.

    A frozen build runs the packaged executable again with ``--mcp-advisor``,
    so the entry stays valid without shipping the Python sources.  Running from
    source uses the interpreter plus the server script.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ["--mcp-advisor"]
    script = Path(__file__).resolve().parent / "codex_mcp_advisor.py"
    executable = Path(sys.executable)
    if executable.name.lower().startswith("python"):
        return str(executable), [str(script)]
    return "py", ["-3.11", str(script)]


def merge_mcp_config(text: str, command: str, args: list[str]) -> str:
    """Insert or replace the advisor MCP entry, leaving other tables untouched."""
    existing = text if isinstance(text, str) else ""
    lines = existing.splitlines()
    output: list[str] = []
    skipping = False
    inserted = False
    for line in lines:
        stripped = line.strip()
        is_table = stripped.startswith("[") and stripped.endswith("]")
        if is_table:
            if stripped == f"[mcp_servers.{MCP_SERVER_NAME}]":
                skipping = True
                continue
            if skipping:
                skipping = False
            if not inserted and stripped.startswith("[mcp_servers."):
                output.extend(build_mcp_toml(command, args).splitlines())
                output.append("")
                inserted = True
        if skipping:
            # drop the old entry's keys and its env sub-table
            if is_table and stripped == f"[mcp_servers.{MCP_SERVER_NAME}.env]":
                continue
            if not is_table:
                continue
        output.append(line)
    if not inserted:
        if output and output[-1].strip():
            output.append("")
        output.extend(build_mcp_toml(command, args).splitlines())
    return "\n".join(output).rstrip() + "\n"
