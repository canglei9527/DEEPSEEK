# -*- coding: utf-8 -*-
"""Minimal stdio MCP server exposing the persistent DeepSeek advisor to Codex.

Protocol: newline-delimited JSON-RPC 2.0 on stdin/stdout (MCP stdio transport).
stdout carries protocol messages only; diagnostics go to stderr.

Tools:
* ``ask_deepseek``     — consult the persistent advisor for this project.
* ``advisor_session``  — report or reset the advisor session for this project.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from codex_advisor import (  # noqa: E402
    AdvisorError,
    AdvisorStore,
    ADVISOR_VERSION,
    dsh_cli_paths,
    find_dsh_install,
)

SERVER_NAME = "deepseek-advisor"
PROTOCOL_VERSION = "2024-11-05"
LOG_PREFIX = "[deepseek-advisor]"


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", file=sys.stderr, flush=True)


def _store() -> AdvisorStore:
    home = Path(os.environ.get("CODEX_HOME", "")).expanduser()
    if not str(home) or str(home) == ".":
        home = Path.home() / ".codex"
    return AdvisorStore.for_home(home)


TOOLS = [
    {
        "name": "ask_deepseek",
        "description": (
            "Consult a persistent DeepSeek advisor for this project. It keeps one "
            "long-lived session per project (remembering earlier reviews) and has its "
            "own tools and the project working directory. Use it for UI/layout/visual "
            "and interaction decisions, architecture or interface trade-offs, hard "
            "failures, and pre-review of large changes. Send the concrete question plus "
            "file paths or line ranges; it reads files itself. You keep ownership of "
            "execution and verification."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The precise question or decision you need.",
                },
                "context": {
                    "type": "string",
                    "description": (
                        "Optional short context: file paths, line ranges, the failing "
                        "command, or a few key snippets. Do not paste the whole transcript."
                    ),
                },
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "advisor_session",
        "description": (
            "Inspect or reset the persistent advisor session for this project. "
            "action=status reports the current session id; action=reset starts a fresh "
            "advisor on the next consultation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "reset"]},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]


def tool_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    store = _store()
    config = store.load_config()
    sessions = store.sessions()
    project = Path.cwd()

    if name == "ask_deepseek":
        question = str(arguments.get("question") or "").strip()
        context = str(arguments.get("context") or "")
        if not question:
            return tool_result("ask_deepseek 需要 question 参数", True)
        if not config.api_key():
            return tool_result(
                "子模型尚未配置：请在 Codex Agent Console 的“接入 Codex”里填入 DeepSeek API Key",
                True,
            )
        runner = store.runner(config, sessions)
        try:
            answer = runner.consult(question, context, project)
        except AdvisorError as exc:
            return tool_result(f"子模型不可用：{exc}", True)
        except Exception as exc:  # pragma: no cover - defensive
            log(traceback.format_exc())
            return tool_result(f"子模型调用异常：{exc}", True)
        if not answer.ok:
            return tool_result(
                f"子模型不可用：{answer.error}\n"
                "请继续按自己的判断完成，并在最终回复里说明没有拿到顾问意见。",
                True,
            )
        return tool_result(
            f"{answer.text}\n\n——\n"
            f"（DeepSeek 顾问 · {answer.seconds:.1f}s · session {answer.session_id[:20]}…）"
        )

    if name == "advisor_session":
        action = str(arguments.get("action") or "status")
        if action == "reset":
            sessions.clear(project)
            return tool_result("已重置当前项目的顾问会话，下次咨询会开新会话。")
        session_id = sessions.get(project)
        return tool_result(
            json.dumps(
                {
                    "project": str(project),
                    "session": session_id or None,
                    "hasKey": bool(config.api_key()),
                    "model": config.model,
                    "baseUrl": config.base_url,
                    "installRoot": config.install_root or None,
                },
                ensure_ascii=False,
            )
        )

    return tool_result(f"未知工具：{name}", True)


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    is_notification = "id" not in request

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": ADVISOR_VERSION},
        }
    elif method in ("notifications/initialized", "initialized"):
        return None
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, -32602, "invalid tools/call params")
        try:
            result = call_tool(name, arguments)
        except Exception as exc:  # pragma: no cover - defensive
            log(traceback.format_exc())
            result = tool_result(f"工具执行异常：{exc}", True)
    elif method in ("shutdown", "exit"):
        return None
    else:
        if is_notification:
            return None
        return _error(request_id, -32601, f"method not found: {method}")

    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def main() -> int:
    log(f"starting; cwd={Path.cwd()}")
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            log(f"ignoring non-JSON line: {line[:120]}")
            continue
        if not isinstance(request, dict):
            continue
        response = handle(request)
        if response is None:
            continue
        # ASCII-escaped JSON survives any parent decoder; raw UTF-8 does not.
        sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
        sys.stdout.flush()
    log("stdin closed; exiting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
