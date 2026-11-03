# -*- coding: utf-8 -*-
"""Warm bridge to Codex's app-server with native tool calls.

``codex exec`` starts a new process for every decision, which costs ~10 s of
startup and re-sends the whole prompt with no cache reuse. The app server keeps
one process and one conversation thread alive, so later turns are ~1.5 s.

The client's tools are registered as Codex *dynamic tools*, so the model calls
them natively — no "emit decision JSON" protocol, no replaced system prompt.
When the model calls a tool we answer the server request and hand the call back
to the client, exactly like the model's own agent loop would.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from codex_gateway import (
    Decision,
    GatewayError,
    RunResult,
    RunUsage,
    ToolCall,
    _content_to_text,
    _clip,
    render_transcript,
)

DEFAULT_TURN_TIMEOUT = 600.0
_THREAD_START_TIMEOUT = 180.0
_MAX_CALLS_PER_TURN = 4
# Codex ships a coding-agent prompt; the bridge needs the runtime's own frame,
# which is short and states who executes the tools.
_BASE_INSTRUCTIONS = """You are an AI agent powered by DeepSeek Harness.

You are a coding agent. You act only by calling the tools listed in this
request; the runtime executes them and returns the real result. You have no
other tools.

The read-only sandbox you can observe belongs to your own scratch space and
says nothing about the task: the runtime holds full permissions and performs
every write, command, and network call for you. Never report that the
filesystem is read-only, never say a file cannot be created, and never fall
back to describing the work instead of doing it — call the tool.

Work directly and concisely. Answer in the user's language.

Non-zero exits are reported as `[exit code: N]`. Investigate failures before
moving on rather than assuming success.

Paths use native Windows form (`C:\\...`); read environment variables with
`$env:NAME`."""
# Told to Codex when it calls a tool: the runtime takes over, and the real
# result arrives with the next request.
_QUEUED = "已交给运行时执行，真实结果会在下一步返回。不要重复调用同一个工具。"


class AppServerError(GatewayError):
    """The app server could not be started, or a turn failed."""


@dataclass
class ThreadState:
    thread_id: str
    sent: int = 0
    digests: list[str] = field(default_factory=list)
    tools_signature: str = ""


def dynamic_tools(tools: Any) -> list[dict[str, Any]]:
    """Convert OpenAI function tools into Codex dynamic tool specs."""
    if not isinstance(tools, list):
        return []
    specs: list[dict[str, Any]] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        function = entry.get("function") if isinstance(entry.get("function"), dict) else entry
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        parameters = function.get("parameters")
        schema = parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}}
        specs.append(
            {
                "type": "function",
                "name": name,
                "description": str(function.get("description") or name)[:4000],
                "inputSchema": schema,
            }
        )
    return specs


class AppServer:
    """One long-lived ``codex app-server`` process speaking JSON-RPC on stdio."""

    def __init__(self, executable: Path, cwd: Path) -> None:
        self.executable = executable
        self.cwd = cwd
        self._process: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._next_id = 1
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        environment = dict(os.environ)
        environment.setdefault("NO_COLOR", "1")
        try:
            self._process = subprocess.Popen(
                [str(self.executable), "app-server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self.cwd),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=environment,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            raise AppServerError(f"无法启动 codex app-server：{exc}") from exc
        threading.Thread(target=self._pump, daemon=True).start()
        reply = self.request(
            "initialize",
            {
                "clientInfo": {"name": "codex-agent-console", "version": "1"},
                # dynamicTools is an experimental surface; opt in explicitly.
                "capabilities": {"experimentalApi": True},
            },
            timeout=60.0,
        )
        if "error" in reply:
            raise AppServerError(f"app-server 初始化失败：{reply['error']}")

    def _pump(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if line.strip():
                self._lines.put(line)

    def alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _write(self, payload: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise AppServerError("app-server 已退出")
        try:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
        except OSError as exc:
            raise AppServerError(f"写入 app-server 失败：{exc}") from exc

    def request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
            )

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                line = self._lines.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") == request_id and (
                "result" in message or "error" in message
            ):
                return message
        raise AppServerError(f"app-server 未响应 {method}")

    def answer(self, request_id: Any, result: dict[str, Any]) -> None:
        try:
            self._write({"jsonrpc": "2.0", "id": request_id, "result": result})
        except AppServerError:
            pass

    def refuse(self, request_id: Any) -> None:
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "handled by the runtime"},
                }
            )
        except AppServerError:
            pass

    def collect_turn(
        self, timeout: float, thread_id: str = ""
    ) -> tuple[str, dict[str, Any], float, list[dict[str, Any]]]:
        """Run the turn to completion, answering tool calls as they arrive."""
        deadline = time.time() + timeout
        text = ""
        usage: dict[str, Any] = {}
        calls: list[dict[str, Any]] = []
        while time.time() < deadline:
            try:
                line = self._lines.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            method = message.get("method")
            params = message.get("params") or {}
            if method == "item/tool/call":
                calls.append(
                    {
                        "id": params.get("callId") or f"call_{len(calls)}",
                        "name": str(params.get("tool") or ""),
                        "arguments": params.get("arguments"),
                    }
                )
                self.answer(
                    message.get("id"),
                    {"success": True, "contentItems": [{"type": "inputText", "text": _QUEUED}]},
                )
                if len(calls) >= _MAX_CALLS_PER_TURN:
                    try:
                        self.request(
                            "turn/interrupt",
                            {"threadId": thread_id or params.get("threadId")},
                            timeout=30.0,
                        )
                    except AppServerError:
                        pass
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("type") == "agentMessage":
                    text = item.get("text") or text
            elif method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage") or usage
            elif method == "error":
                raise AppServerError(str(params)[:300])
            elif method == "turn/completed":
                turn = params.get("turn") or {}
                return text, usage, float(turn.get("durationMs") or 0) / 1000.0, calls
            elif method and "id" in message:
                self.refuse(message.get("id"))
        raise AppServerError("turn 超时")

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.terminate()
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _message_digest(message: dict[str, Any]) -> str:
    return _digest(json.dumps(message, ensure_ascii=False, sort_keys=True, default=str))


def _usage_from(usage: dict[str, Any]) -> RunUsage:
    last = usage.get("last") if isinstance(usage, dict) else None
    source = last if isinstance(last, dict) else (usage or {})
    return RunUsage(
        input_tokens=int(source.get("inputTokens") or 0),
        cached_input_tokens=int(source.get("cachedInputTokens") or 0),
        output_tokens=int(source.get("outputTokens") or 0),
    )


class WarmRunner:
    """Drives app-server threads; same interface as the CLI runner."""

    def __init__(self, executable: Path, timeout_seconds: float = DEFAULT_TURN_TIMEOUT):
        self.executable = executable
        self.timeout_seconds = timeout_seconds
        self._servers: dict[str, AppServer] = {}
        self._threads: dict[str, ThreadState] = {}
        self._lock = threading.Lock()

    def _server_for(self, workdir: Path) -> AppServer:
        key = str(workdir)
        server = self._servers.get(key)
        if server is None or not server.alive():
            server = AppServer(self.executable, workdir)
            server.start()
            self._servers[key] = server
        return server

    def run(
        self,
        prompt: str,
        model: str,
        effort: str,
        workdir: Path,
        *,
        messages: Sequence[dict[str, Any]] | None = None,
        tools: Any = None,
        instructions: str = "",
    ) -> RunResult:
        with self._lock:
            started = time.time()
            server = self._server_for(workdir)
            conversation = _conversation_key(messages or [])
            specs = dynamic_tools(tools)
            signature = _digest(json.dumps(specs, ensure_ascii=False, sort_keys=True))
            state = self._threads.get(conversation)
            if state is not None and state.tools_signature != signature:
                # The tool catalogue changed; the thread was built around the
                # old one, so start a fresh thread.
                state = None
            delta_text, is_new = self._next_turn_text(state, messages, prompt)
            if state is None:
                thread_id = self._start_thread(server, model, workdir, specs, instructions)
                state = ThreadState(thread_id=thread_id, tools_signature=signature)
                self._threads[conversation] = state
            text, usage, seconds, calls = self._run_turn(server, state, delta_text, effort)
            self._advance(state, messages, is_new)
            if calls:
                tool_calls = tuple(
                    ToolCall(name=call["name"], arguments=_arguments_text(call["arguments"]))
                    for call in calls
                    if call["name"]
                )
                if tool_calls:
                    return RunResult(
                        decision=Decision(
                            kind="tool_calls",
                            content=(text.strip() or None) if text else None,
                            tool_calls=tool_calls,
                        ),
                        usage=_usage_from(usage),
                        seconds=seconds or (time.time() - started),
                    )
            if not text.strip():
                raise AppServerError("app-server 没有返回内容")
            return RunResult(
                decision=Decision(kind="final", content=text),
                usage=_usage_from(usage),
                seconds=seconds or (time.time() - started),
            )

    def _start_thread(
        self,
        server: AppServer,
        model: str,
        workdir: Path,
        specs: list[dict[str, Any]],
        instructions: str,
    ) -> str:
        params: dict[str, Any] = {
            "cwd": str(workdir),
            "approvalPolicy": "never",
            # Read-only is the enforcement point: Codex's own file and shell
            # tools must fail, so every real action goes through the runtime
            # instead of silently bypassing the client's permission policy.
            "sandbox": "read-only",
            "ephemeral": True,
        }
        if specs:
            params["dynamicTools"] = specs
        if instructions:
            params["developerInstructions"] = instructions
        params["baseInstructions"] = _BASE_INSTRUCTIONS
        if model and "@" not in model:
            params["model"] = model
        reply = server.request("thread/start", params, timeout=_THREAD_START_TIMEOUT)
        if "error" in reply:
            raise AppServerError(f"thread/start 失败：{reply['error']}")
        thread = (reply.get("result") or {}).get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise AppServerError("thread/start 没有返回线程 id")
        return str(thread_id)

    def _run_turn(
        self, server: AppServer, state: ThreadState, text: str, effort: str
    ) -> tuple[str, dict[str, Any], float, list[dict[str, Any]]]:
        params: dict[str, Any] = {
            "threadId": state.thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if effort:
            params["effort"] = effort
        reply = server.request("turn/start", params, timeout=60.0)
        if "error" in reply:
            raise AppServerError(f"turn/start 失败：{reply['error']}")
        return server.collect_turn(self.timeout_seconds, state.thread_id)

    def _next_turn_text(
        self,
        state: ThreadState | None,
        messages: Sequence[dict[str, Any]] | None,
        prompt: str,
    ) -> tuple[str, bool]:
        if state is None or not messages:
            return prompt, True
        digests = [_message_digest(message) for message in messages]
        if len(digests) < state.sent or digests[: state.sent] != state.digests:
            # The conversation was rewritten; a delta would be meaningless.
            self._threads.pop(_conversation_key(messages), None)
            return prompt, True
        fresh = list(messages)[state.sent :]
        if not fresh:
            return prompt, True
        return "## 新增对话\n" + _clip(render_transcript(fresh), 120000), False

    def _advance(
        self,
        state: ThreadState,
        messages: Sequence[dict[str, Any]] | None,
        is_new: bool,
    ) -> None:
        if not messages:
            return
        state.sent = len(messages)
        state.digests = [_message_digest(message) for message in messages]

    def close(self) -> None:
        with self._lock:
            for server in self._servers.values():
                server.close()
            self._servers.clear()
            self._threads.clear()


def _arguments_text(arguments: Any) -> str:
    if isinstance(arguments, str):
        return arguments
    if arguments is None:
        return "{}"
    return json.dumps(arguments, ensure_ascii=False)


def _conversation_key(messages: Sequence[dict[str, Any]]) -> str:
    first_user = ""
    system = ""
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role == "system" and not system:
            system = _content_to_text(message.get("content"))
        elif role == "user" and not first_user:
            first_user = _content_to_text(message.get("content"))
        if system and first_user:
            break
    return _digest(system + "\u0000" + first_user)[:20]
