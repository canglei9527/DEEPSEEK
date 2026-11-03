# -*- coding: utf-8 -*-
"""Local OpenAI-compatible gateway backed by a Codex model.

The DeepSeek Harness (or any other agent runtime) can point a provider at this
server. Each model call becomes one ``codex exec`` run:

* the runtime's system prompt, transcript, tool results and tool catalogue are
  rendered into a single decision prompt;
* Codex answers with strict JSON — either a final message or a list of tool
  calls — which is translated back into the OpenAI Chat Completions shape;
* tools are executed by the calling runtime, so only decisions cross the bridge.

The server binds to loopback only and can require a bearer token.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Sequence

GATEWAY_VERSION = "1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_MAX_PARALLEL = 2
MAX_TRANSCRIPT_CHARS = 120_000
MAX_MESSAGE_CHARS = 24_000
MAX_TOOL_SCHEMA_CHARS = 8_000
MAX_TOOL_CALLS_PER_TURN = 4

_SYSTEM_PROMPT = """You are the reasoning model behind an external agent runtime.
The runtime executes every tool; you only decide what to do next.

Return one JSON object with exactly these keys:
- "kind": "final" or "tool_calls"
- "content": string or null
- "tool_calls": array or null

Rules:
1. The runtime holds full permissions: it reads and writes files, runs commands, and uses the network. You never execute anything yourself, so any sandbox restriction you are told about does not limit the task — never answer that you cannot write a file.
2. This request's own tools are the ones under "本轮可用工具". Use those names only. Ignore your built-in shell, patch, and file tools: they are not the runtime's tools and calling them does nothing for the user.
3. When the user asks you to create or change a file, return "tool_calls" naming the runtime's file tool (for example `write`, `edit`, or `pwsh`) with the complete content in "arguments" — do not paste the file into "content" as the final answer.
4. Put every required argument in the JSON string; never leave a placeholder.
5. If no tool is needed, return "final" with the complete answer in "content". Do not describe what you are about to do.
6. After tool results arrive, continue: call another tool or give the final answer.
7. Answer in the user's language and keep the answer focused on the request.
"""


class GatewayError(Exception):
    """An error that maps to an HTTP status code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: str

    def to_openai(self, index: int) -> dict[str, object]:
        return {
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments},
        }


@dataclass(frozen=True)
class Decision:
    kind: str
    content: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()

    @property
    def is_tool_call(self) -> bool:
        return self.kind == "tool_calls" and bool(self.tool_calls)


@dataclass
class RunUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class RunResult:
    decision: Decision
    usage: RunUsage = field(default_factory=RunUsage)
    seconds: float = 0.0
    thread_id: str = ""


# --------------------------------------------------------------------------- config
def _as_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_float(value: object, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


@dataclass
class GatewayConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    model: str = "gpt-6-luna"
    effort: str = "max"
    workdir: str = ""
    token: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_parallel: int = DEFAULT_MAX_PARALLEL
    auto_start: bool = False

    @property
    def model_id(self) -> str:
        return f"{self.model}@{self.effort}"

    @property
    def api_base(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "model": self.model,
            "effort": self.effort,
            "workdir": self.workdir,
            "token": self.token,
            "timeoutSeconds": self.timeout_seconds,
            "maxParallel": self.max_parallel,
            "autoStart": self.auto_start,
        }

    @classmethod
    def from_dict(cls, value: object) -> GatewayConfig:
        data = value if isinstance(value, dict) else {}
        host = str(data.get("host") or DEFAULT_HOST).strip() or DEFAULT_HOST
        if host not in ("127.0.0.1", "localhost", "::1"):
            host = DEFAULT_HOST
        return cls(
            host=host,
            port=_as_int(data.get("port"), DEFAULT_PORT, 1024, 65535),
            model=str(data.get("model") or "gpt-6-luna").strip() or "gpt-6-luna",
            effort=str(data.get("effort") or "max").strip() or "max",
            workdir=str(data.get("workdir") or "").strip(),
            token=str(data.get("token") or "").strip(),
            timeout_seconds=_as_float(
                data.get("timeoutSeconds"), DEFAULT_TIMEOUT_SECONDS, 30.0, 3600.0
            ),
            max_parallel=_as_int(data.get("maxParallel"), DEFAULT_MAX_PARALLEL, 1, 8),
            auto_start=bool(data.get("autoStart")),
        )

    @classmethod
    def load(cls, path: Path) -> GatewayConfig:
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        os.replace(temporary, path)


# --------------------------------------------------------------------------- prompt
def decision_schema() -> dict[str, object]:
    """Strict JSON schema accepted by Codex ``--output-schema``."""
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["final", "tool_calls"]},
            "content": {"type": ["string", "null"]},
            "tool_calls": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "arguments": {"type": "string"},
                    },
                    "required": ["name", "arguments"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["kind", "content", "tool_calls"],
        "additionalProperties": False,
    }


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(part.get("type"), str):
                    parts.append(f"[{part['type']}]")
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（已截断 {0} 字符）".format(len(text) - limit)


def render_tools(tools: object) -> str:
    if not isinstance(tools, list) or not tools:
        return "（没有可用工具，请直接给出最终回答）"
    lines: list[str] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function") if isinstance(entry.get("function"), dict) else entry
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        description = str(function.get("description") or "").strip()
        parameters = function.get("parameters")
        schema = json.dumps(parameters, ensure_ascii=False) if parameters else "{}"
        schema = _clip(schema, MAX_TOOL_SCHEMA_CHARS)
        lines.append(f"- {name}: {description}\n  parameters: {schema}")
    return "\n".join(lines) if lines else "（没有可用工具，请直接给出最终回答）"


def render_transcript(messages: object) -> str:
    if not isinstance(messages, list):
        return ""
    blocks: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        if role == "tool":
            name = str(message.get("name") or message.get("tool_call_id") or "tool")
            body = _clip(_content_to_text(message.get("content")), MAX_MESSAGE_CHARS)
            blocks.append(f"[工具结果 · {name}]\n{body}")
            continue
        body = _content_to_text(message.get("content"))
        if body:
            blocks.append(f"[{role}]\n{_clip(body, MAX_MESSAGE_CHARS)}")
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(function.get("name") or "tool")
            arguments = function.get("arguments")
            argument_text = (
                arguments if isinstance(arguments, str) else json.dumps(arguments, ensure_ascii=False)
            )
            blocks.append(f"[assistant → 调用 {name}]\n{_clip(argument_text, 4000)}")
    text = "\n\n".join(blocks)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        # Keep the beginning (the task) and the end (the latest tool results).
        head = text[: MAX_TRANSCRIPT_CHARS // 3]
        tail = text[-(MAX_TRANSCRIPT_CHARS * 2 // 3) :]
        text = f"{head}\n\n…（中间对话已省略）…\n\n{tail}"
    return text


def build_instructions() -> str:
    """The stable protocol text handed to Codex as its system instructions."""
    return _SYSTEM_PROMPT.format(max_calls=MAX_TOOL_CALLS_PER_TURN)


def build_native_prompt(messages: object) -> str:
    """Prompt for the native-tool path: the tools are registered, not described.

    Handing the model its own decision protocol here would make it answer with
    JSON instead of calling the tools it was given.
    """
    system_parts = [
        _content_to_text(message.get("content"))
        for message in (messages or [])
        if isinstance(message, dict) and message.get("role") == "system"
    ]
    system_text = "\n\n".join(part for part in system_parts if part).strip()
    sections: list[str] = []
    if system_text:
        sections.append("## 运行时给系统提示\n" + _clip(system_text, MAX_MESSAGE_CHARS * 2))
    sections.append("## 对话记录\n" + render_transcript(messages))
    return "\n\n".join(sections)


def build_prompt(messages: object, tools: object) -> str:
    system_parts = [
        _content_to_text(message.get("content"))
        for message in (messages or [])
        if isinstance(message, dict) and message.get("role") == "system"
    ]
    system_text = "\n\n".join(part for part in system_parts if part).strip()
    instructions = build_instructions()
    sections = [instructions]
    if system_text:
        sections.append("## 运行时给系统提示\n" + _clip(system_text, MAX_MESSAGE_CHARS * 2))
    sections.append("## 可用工具\n" + render_tools(tools))
    sections.append("## 对话记录\n" + render_transcript(messages))
    sections.append(
        "## 你的决定\n只输出符合给定 JSON Schema 的对象。"
        "需要工具时 kind=tool_calls，否则 kind=final。"
    )
    return "\n\n".join(sections)


def _extract_json_object(text: str) -> dict[str, object] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", stripped, flags=re.S)
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    match = re.search(r"\{.*\}", stripped, re.S)
    if match:
        try:
            value = json.loads(match.group(0))
            if isinstance(value, dict):
                return value
        except ValueError:
            return None
    return None


def _normalize_arguments(value: object) -> str:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "{}"
        try:
            json.loads(text)
        except ValueError:
            return json.dumps({"_raw": text}, ensure_ascii=False)
        return text
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "{}"


def parse_decision(text: str) -> Decision:
    """Turn the model's structured answer into a Decision, tolerating prose."""
    data = _extract_json_object(text)
    if data is None:
        return Decision(kind="final", content=text.strip())
    kind = str(data.get("kind") or "").strip()
    raw_calls = data.get("tool_calls")
    calls: list[ToolCall] = []
    if isinstance(raw_calls, list):
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            calls.append(
                ToolCall(name=name, arguments=_normalize_arguments(item.get("arguments")))
            )
    if kind == "tool_calls" and calls:
        return Decision(kind="tool_calls", content=None, tool_calls=tuple(calls))
    content = data.get("content")
    if not isinstance(content, str):
        content = "" if content is None else str(content)
    if not content.strip() and calls:
        return Decision(kind="tool_calls", content=None, tool_calls=tuple(calls))
    return Decision(kind="final", content=content)


# --------------------------------------------------------------------------- runner
class CodexCliRunner:
    """Runs one ``codex exec`` decision and parses its structured answer."""

    def __init__(self, executable: Path, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS):
        self.executable = executable
        self.timeout_seconds = timeout_seconds

    def build_command(
        self,
        model: str,
        effort: str,
        workdir: Path,
        schema_path: Path,
        output_path: Path,
    ) -> list[str]:
        return [
            str(self.executable),
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
            "-s",
            "read-only",
            "-C",
            str(workdir),
            "-m",
            model,
            "-c",
            f"model_reasoning_effort={effort}",
            "-c",
            "approval_policy=never",
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            "-",
        ]

    def run(
        self,
        prompt: str,
        model: str,
        effort: str,
        workdir: Path,
    ) -> RunResult:
        started = time.perf_counter()
        if not workdir.exists():
            raise GatewayError(f"工作目录不存在：{workdir}", status=500)
        with tempfile.TemporaryDirectory(prefix="cac-gateway-") as temporary:
            schema_path = Path(temporary) / "schema.json"
            output_path = Path(temporary) / "decision.json"
            schema_path.write_text(
                json.dumps(decision_schema(), ensure_ascii=False), encoding="utf-8"
            )
            command = self.build_command(model, effort, workdir, schema_path, output_path)
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    cwd=str(workdir),
                    # A windowed build has no console of its own; without this
                    # every codex call would pop a Windows Terminal window.
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except subprocess.TimeoutExpired as exc:
                raise GatewayError(
                    f"Codex 执行超时（{self.timeout_seconds:.0f}s）", status=504
                ) from exc
            except OSError as exc:
                raise GatewayError(f"无法启动 Codex CLI：{exc}", status=500) from exc
            usage = RunUsage()
            thread_id = ""
            agent_text = ""
            failure = ""
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
                if event.get("type") == "thread.started" and isinstance(
                    event.get("thread_id"), str
                ):
                    thread_id = event["thread_id"]
                if event.get("type") == "turn.completed":
                    raw_usage = event.get("usage")
                    if isinstance(raw_usage, dict):
                        usage = RunUsage(
                            input_tokens=_as_int(raw_usage.get("input_tokens"), 0, 0, 10**12),
                            cached_input_tokens=_as_int(
                                raw_usage.get("cached_input_tokens"), 0, 0, 10**12
                            ),
                            output_tokens=_as_int(
                                raw_usage.get("output_tokens"), 0, 0, 10**12
                            ),
                        )
                if event.get("type") in ("turn.failed", "error"):
                    message = event.get("message")
                    if isinstance(message, dict):
                        message = message.get("message")
                    if isinstance(message, str) and message.strip():
                        failure = message.strip()
                item = event.get("item")
                if (
                    event.get("type") == "item.completed"
                    and isinstance(item, dict)
                    and item.get("type") in ("agent_message", "message")
                    and isinstance(item.get("text"), str)
                ):
                    agent_text = item["text"]
            answer = ""
            if output_path.exists():
                answer = output_path.read_text(encoding="utf-8", errors="replace")
            if not answer.strip():
                answer = agent_text
            elapsed = time.perf_counter() - started
            if not answer.strip():
                detail = failure or (completed.stderr or "").strip()[-400:]
                raise GatewayError(
                    f"Codex 没有返回可用结果（退出码 {completed.returncode}）：{detail or '无输出'}",
                    status=502,
                )
            return RunResult(
                decision=parse_decision(answer),
                usage=usage,
                seconds=elapsed,
                thread_id=thread_id,
            )


# --------------------------------------------------------------------------- server
@dataclass
class RequestRecord:
    started_at: float
    model: str
    kind: str
    seconds: float
    tool_calls: int
    prompt_tokens: int
    completion_tokens: int
    error: str = ""


def dsh_mcp_insert(
    command: str,
    args: Sequence[str],
    server_name: str = "deepseek",
    tool_timeout_ms: int = 300000,
) -> str:
    """A profile patch entry that adds the advisor MCP server to DSH.

    New plugins need an ``insert`` patch: an id-targeted entry is only allowed
    to override an entry the bundles already define.
    """
    lines = [
        "- insert:",
        "    - id: mcp-deepseek-advisor",
        "      name: '@deepseek-ai/dsh-mcp-client'",
        "      config:",
        "        transport: stdio",
        f"        serverName: {json.dumps(server_name)}",
        f"        command: {json.dumps(command)}",
        "        args:",
    ]
    lines.extend(f"          - {json.dumps(arg)}" for arg in args)
    lines.append(f"        toolCallTimeoutMs: {int(tool_timeout_ms)}")
    return "\n".join(lines) + "\n"


def strip_dsh_mcp_insert(text: str, marker: str = "mcp-deepseek-advisor") -> str:
    """Remove a previously written ``- insert:`` block that adds our server."""
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].startswith("- insert:"):
            end = index + 1
            while end < len(lines) and not lines[end].startswith("- "):
                end += 1
            block = "\n".join(lines[index:end])
            if marker in block:
                index = end
                continue
            out.extend(lines[index:end])
            index = end
            continue
        out.append(lines[index])
        index += 1
    return "\n".join(out).rstrip() + "\n"


def strip_dsh_entries(text: str, entry_ids: Sequence[str]) -> str:
    """Drop top-level profile entries by id, preserving every other line.

    Used to replace the entries this console previously wrote instead of
    appending duplicates.
    """
    targets = {entry_id.strip() for entry_id in entry_ids}
    kept: list[str] = []
    skipping = False
    for line in text.splitlines():
        if line.startswith("# Adds the Codex gateway route"):
            skipping = True
            continue
        if line.startswith("- id:"):
            entry_id = line.split(":", 1)[1].strip()
            skipping = entry_id in targets
        elif line.startswith("- ") or (line and not line[0].isspace()):
            skipping = False
        if not skipping:
            kept.append(line)
    return "\n".join(kept).rstrip() + "\n"


def dsh_provider_snippet(
    config: GatewayConfig,
    *,
    subagent_model: str = "",
    subagent_provider: str = "deepseek-official",
    subagent_max_tokens: int = 32768,
) -> str:
    """The profile patch entries a DeepSeek Harness user pastes into their config.

    ``subagent_model`` additionally pins DSH's own subagent tools to a DeepSeek
    route, so the gateway's expensive model stays the main model while delegated
    work runs on the cheaper one.
    """
    lines = [
        "# Adds the Codex gateway route to the existing pi-ai provider plugin.",
        "- id: llm-pi-ai",
        "  config:",
        "    providers:",
        "      codex-local:",
        "        displayName: Codex 本机网关",
        "        api: openai-completions",
        f"        baseURL: {config.api_base}",
    ]
    # Always name the credential: pi-ai refuses a route without one, and DSH
    # resolves it from $DSH_HOME/.env (the console writes that file too).
    lines.append("        apiKeyEnv: CODEX_GATEWAY_TOKEN")
    lines.extend(
        [
            "        models:",
            f"          - id: {config.model_id}",
            f"            name: {config.model} / {config.effort}",
            "            contextWindow: 272000",
        ]
    )
    if subagent_model:
        for tool_id, tool_name in (
            ("tool-subagent", "subagent"),
            ("tool-subagent-fork", "subagent_fork"),
        ):
            provider = "spawn" if tool_name == "subagent" else "fork"
            lines.extend(
                [
                    f"- id: {tool_id}",
                    "  name: '@deepseek-ai/dsh-tool-subagent'",
                    "  config:",
                    f"    provider: {provider}",
                    f"    toolName: {tool_name}",
                    "    agentOptions:",
                    f"      provider: {subagent_provider}",
                    f"      model: {subagent_model}",
                    f"      maxTokens: {subagent_max_tokens}",
                ]
            )
    return "\n".join(lines) + "\n"


class CodexGateway:
    """Owns the HTTP server and the Codex runner behind it."""

    def __init__(
        self,
        config: GatewayConfig,
        executable: Path,
        runner_factory: Callable[[Path, float], CodexCliRunner] | None = None,
    ):
        self.config = config
        self.executable = executable
        self._runner_factory = runner_factory or (
            lambda path, timeout: CodexCliRunner(path, timeout)
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._semaphore = threading.BoundedSemaphore(max(1, config.max_parallel))
        self._lock = threading.Lock()
        self._records: list[RequestRecord] = []
        self._started_at = 0.0
        self.last_error = ""
        self._warm: object | None = None
        self._warm_retry_at = 0.0
        # A custom runner factory means tests or an embedding host; keep the
        # deterministic CLI path there.
        self._warm_enabled = runner_factory is None

    # -- lifecycle
    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def api_base(self) -> str:
        if self._server is not None:
            host, port = self._server.server_address[:2]
            return f"http://{host}:{port}/v1"
        return self.config.api_base

    def start(self) -> None:
        if self._server is not None:
            return
        try:
            server = ThreadingHTTPServer(
                (self.config.host, self.config.port), _GatewayHandler
            )
        except OSError as exc:
            raise GatewayError(f"无法监听 {self.config.host}:{self.config.port}：{exc}", 500) from exc
        server.daemon_threads = True
        server.gateway = self  # type: ignore[attr-defined]
        self._server = server
        self._started_at = time.time()
        self._thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        thread, self._thread = self._thread, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=3)
        warm = self._warm
        self._warm = None
        if warm is not None:
            close = getattr(warm, "close", None)
            if callable(close):
                close()

    # -- requests
    def run_decision(
        self, messages: object, tools: object, model: str, effort: str
    ) -> RunResult:
        prompt = build_prompt(messages, tools)
        native_prompt = build_native_prompt(messages)
        workdir = Path(self.config.workdir or os.getcwd()).expanduser()
        runner = self._runner_factory(self.executable, self.config.timeout_seconds)
        with self._semaphore:
            result = self._run_warm(
                native_prompt, messages, tools, model, effort, workdir
            )
            if result is None:
                result = runner.run(prompt, model, effort, workdir)
        self._remember(result)
        return result

    def _run_warm(
        self,
        prompt: str,
        messages: object,
        tools: object,
        model: str,
        effort: str,
        workdir: Path,
    ) -> RunResult | None:
        """Try the persistent app server; None means fall back to `codex exec`."""
        if os.environ.get("CAC_DISABLE_WARM", "").strip() == "1":
            return None
        if not self._warm_enabled:
            return None
        if time.time() < self._warm_retry_at:
            return None
        warm = self._warm
        if warm is None:
            try:
                from codex_appserver import WarmRunner  # local: optional path
            except Exception:  # noqa: BLE001 - bundler may omit the module
                self._warm_enabled = False
                return None

            warm = WarmRunner(self.executable, self.config.timeout_seconds)
            self._warm = warm
        try:
            return warm.run(
                prompt,
                model,
                effort,
                workdir,
                messages=[m for m in messages if isinstance(m, dict)]
                if isinstance(messages, list)
                else [],
                tools=tools,
                # No decision-JSON protocol here: the tools are registered as
                # native dynamic tools, so the model calls them directly.
                instructions="",
            )
        except GatewayError as exc:
            self.last_error = f"app-server 回退：{exc}"
            self._warm_retry_at = time.time() + 300
            if warm is not None:
                warm.close()
            self._warm = None
            return None

    def _remember(self, result: RunResult) -> None:
        record = RequestRecord(
            started_at=time.time(),
            model=self.config.model_id,
            kind=result.decision.kind,
            seconds=result.seconds,
            tool_calls=len(result.decision.tool_calls),
            prompt_tokens=result.usage.input_tokens,
            completion_tokens=result.usage.output_tokens,
        )
        with self._lock:
            self._records.append(record)
            del self._records[:-50]

    def note_error(self, message: str) -> None:
        self.last_error = message

    def records(self) -> list[RequestRecord]:
        with self._lock:
            return list(self._records)

    def stats(self) -> dict[str, object]:
        records = self.records()
        return {
            "running": self.running,
            "apiBase": self.api_base,
            "model": self.config.model_id,
            "requests": len(records),
            "uptimeSeconds": (time.time() - self._started_at) if self.running else 0.0,
            "lastError": self.last_error,
        }

    # -- payload shaping
    def chat_completion(
        self, payload: dict[str, object]
    ) -> tuple[dict[str, object], str]:
        model_id = str(payload.get("model") or self.config.model_id)
        model, effort = self.resolve_model(model_id)
        result = self.run_decision(
            payload.get("messages"), payload.get("tools"), model, effort
        )
        return self.completion_body(payload, model_id, result), model_id

    def resolve_model(self, requested: str) -> tuple[str, str]:
        text = (requested or "").strip()
        if "@" in text:
            model, effort = text.rsplit("@", 1)
            if model and effort:
                return model, effort
        return (text or self.config.model, self.config.effort)

    def completion_body(
        self, payload: dict[str, object], model_id: str, result: RunResult
    ) -> dict[str, object]:
        decision = result.decision
        message: dict[str, object] = {"role": "assistant"}
        if decision.is_tool_call:
            message["content"] = None
            message["tool_calls"] = [
                call.to_openai(index) for index, call in enumerate(decision.tool_calls)
            ]
            finish_reason = "tool_calls"
        else:
            message["content"] = decision.content or ""
            finish_reason = "stop"
        prompt_tokens = result.usage.input_tokens
        completion_tokens = result.usage.output_tokens
        cached_tokens = min(result.usage.cached_input_tokens, prompt_tokens)
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_id,
            "choices": [
                {"index": 0, "message": message, "finish_reason": finish_reason}
            ],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                # Codex reports cache hits separately; surface them so the
                # client's cache-hit rate is not permanently 0%.
                "prompt_tokens_details": {"cached_tokens": cached_tokens},
            },
        }

    @staticmethod
    def stream_chunks(
        body: dict[str, object], model_id: str
    ) -> list[dict[str, object]]:
        choice = body["choices"][0]  # type: ignore[index]
        message = choice["message"]  # type: ignore[index]
        base = {
            "id": body["id"],
            "object": "chat.completion.chunk",
            "created": body["created"],
            "model": model_id,
        }
        chunks: list[dict[str, object]] = [
            {**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
        ]
        if message.get("tool_calls"):
            delta_calls = []
            for index, call in enumerate(message["tool_calls"]):  # type: ignore[union-attr]
                delta_calls.append(
                    {
                        "index": index,
                        "id": call["id"],
                        "type": "function",
                        "function": call["function"],
                    }
                )
            chunks.append(
                {**base, "choices": [{"index": 0, "delta": {"tool_calls": delta_calls}, "finish_reason": None}]}
            )
        else:
            chunks.append(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": message.get("content") or ""},
                            "finish_reason": None,
                        }
                    ],
                }
            )
        chunks.append(
            {
                **base,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}
                ],
                "usage": body["usage"],
            }
        )
        return chunks


class _GatewayHandler(BaseHTTPRequestHandler):
    server_version = f"CodexAgentConsoleGateway/{GATEWAY_VERSION}"
    protocol_version = "HTTP/1.1"

    # -- plumbing
    @property
    def gateway(self) -> CodexGateway:
        return self.server.gateway  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return

    def _authorized(self) -> bool:
        token = self.gateway.config.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        return header.strip() == f"Bearer {token}"

    def _send_json(self, status: int, body: object) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_body(self) -> dict[str, object]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            raise GatewayError("请求体为空", status=400)
        if length > 8 * 1024 * 1024:
            raise GatewayError("请求体过大", status=413)
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise GatewayError("请求体不是合法 JSON", status=400) from exc
        if not isinstance(value, dict):
            raise GatewayError("请求体必须是 JSON 对象", status=400)
        return value

    # -- routes
    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": {"message": "invalid token"}})
            return
        if self.path.rstrip("/") in ("/health", "/v1/health"):
            self._send_json(200, {"ok": True, **self.gateway.stats()})
            return
        if self.path.rstrip("/") == "/v1/models":
            config = self.gateway.config
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": config.model_id,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "codex-agent-console",
                        }
                    ],
                },
            )
            return
        self._send_json(404, {"error": {"message": f"unknown path {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": {"message": "invalid token"}})
            return
        path = self.path.rstrip("/")
        if path != "/v1/chat/completions":
            self._send_json(404, {"error": {"message": f"unknown path {self.path}"}})
            return
        try:
            payload = self._read_body()
            stream = bool(payload.get("stream"))
            if stream:
                self._handle_stream(payload)
                return
            body, _model = self.gateway.chat_completion(payload)
        except GatewayError as exc:
            self.gateway.note_error(str(exc))
            self._send_json(exc.status, {"error": {"message": str(exc)}})
            return
        except Exception as exc:  # pragma: no cover - defensive
            self.gateway.note_error(str(exc))
            self._send_json(500, {"error": {"message": str(exc)}})
            return
        self._send_json(200, body)

    def _handle_stream(self, payload: dict[str, object]) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

        def emit(data: object) -> None:
            chunk = f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
            self.wfile.write(chunk.encode("utf-8"))
            self.wfile.flush()

        heartbeat = threading.Event()
        done = threading.Event()

        def keep_alive() -> None:
            while not done.wait(15):
                try:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
                except OSError:
                    return

        threading.Thread(target=keep_alive, daemon=True).start()
        try:
            body, model_id = self.gateway.chat_completion(payload)
            for chunk in CodexGateway.stream_chunks(body, model_id):
                emit(chunk)
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except GatewayError as exc:
            self.gateway.note_error(str(exc))
            emit({"error": {"message": str(exc)}})
            self.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            heartbeat.set()
            done.set()
            self.close_connection = True
