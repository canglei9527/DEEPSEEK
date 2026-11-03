"""Live owner/follower connection to the running Codex Desktop on Windows.

The private IPC contract was inspected in Desktop 26.1013.4894.0. No second
app-server is started. Readers block on overlapped pipe I/O and are cancellable.
The native start-turn contract has NO compare-and-swap/expected-turn guard.
resume() uses two fresh owner snapshots plus a final local generation check;
there remains a narrow race between that check and Desktop accepting input.
An unconfirmed mutation is never retried by this transport.
"""
from __future__ import annotations

import copy
import ctypes
import json
import os
import struct
import threading
import time
import uuid
from typing import Any, Callable

PIPE_PATH = r"\\.\pipe\codex-ipc"
MAX_FRAME = 268435456
STREAM_VERSION = 11


class DesktopError(RuntimeError):
    pass


class DesktopDisconnected(DesktopError):
    pass


class DesktopProtocolError(DesktopError):
    pass


class DesktopResumeRejected(DesktopError):
    pass


def apply_patches(state: Any, patches: list[dict[str, Any]]) -> Any:
    """Apply Desktop's Immer patches transactionally; reject unknown operations."""
    # Copy only patched ancestors. Copying all tool output on each text delta
    # makes following a busy conversation needlessly expensive.
    result = state
    for patch in patches:
        operation, path = patch.get("op"), patch.get("path")
        if operation not in ("add", "replace", "remove") or not isinstance(path, list):
            raise DesktopProtocolError("Unsupported Desktop state patch")
        if not path:
            if operation == "remove":
                raise DesktopProtocolError("Cannot remove the state root")
            result = copy.deepcopy(patch["value"])
            continue
        try:
            result = copy.copy(result)
            target = result
            for key in path[:-1]:
                child = copy.copy(target[key])
                target[key] = child
                target = child
            key = path[-1]
            if isinstance(target, list):
                if not isinstance(key, int) or isinstance(key, bool) or key < 0:
                    raise ValueError("invalid list index")
                if operation == "add":
                    if key > len(target):
                        raise IndexError(key)
                    target.insert(key, copy.deepcopy(patch["value"]))
                elif operation == "remove":
                    del target[key]
                else:
                    target[key] = copy.deepcopy(patch["value"])
            elif isinstance(target, dict):
                if operation != "add" and key not in target:
                    raise KeyError(key)
                if operation == "remove":
                    del target[key]
                else:
                    target[key] = copy.deepcopy(patch["value"])
            else:
                raise ValueError("patch parent is not a collection")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DesktopProtocolError("Invalid Desktop state patch path") from exc
    return result


def normalize_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Expose factual live metadata without carrying message/tool item payloads."""
    turns: list[dict[str, Any]] = []
    history = raw.get("turnHistory")
    if isinstance(history, dict) and history.get("kind") == "canonical":
        canonical = history.get("history", {})
        entities = canonical.get("entitiesByKey", {})
        for island in reversed(canonical.get("islands", [])):
            for entry in reversed(island.get("entries", [])):
                turn = entities.get(entry.get("value"))
                if isinstance(turn, dict):
                    turns.append(turn)
                if len(turns) >= 8:
                    break
            if len(turns) >= 8:
                break
        turns.reverse()
    # Desktop can retain optimistic/current turns outside canonical history.
    for turn in raw.get("turns", []):
        if not isinstance(turn, dict):
            continue
        identity = turn.get("turnId") or turn.get("id")
        index = next((i for i, item in enumerate(turns)
                      if identity and (item.get("turnId") or item.get("id")) == identity), None)
        if index is None:
            turns.append(turn)
        else:
            turns[index] = turn
    compact = []
    for turn in turns[-8:]:
        compact.append({
            "id": turn.get("turnId") or turn.get("id"),
            "status": turn.get("status"),
            "error": copy.deepcopy(turn.get("error")),
            "startedAt": turn.get("turnStartedAtMs", turn.get("startedAt")),
            "completedAt": turn.get("completedAt"),
        })
    return {
        "id": raw.get("id") or raw.get("sessionId"),
        "title": raw.get("title") or raw.get("name") or "",
        "model": raw.get("latestModel") or raw.get("model") or "",
        "status": copy.deepcopy(raw.get("threadRuntimeStatus") or {"type": "unknown"}),
        "goal": copy.deepcopy(raw.get("threadGoal")),
        "archived": raw.get("archived") is True,
        "turns": compact,
        "pendingRequests": bool(raw.get("requests")),
        "pendingSubmissions": any(item.get("terminal") is not True
                                  for item in raw.get("unconfirmedTurnSubmissions", [])
                                  if isinstance(item, dict)),
    }


class _WindowsPipe:
    """Separate OVERLAPPED reads/writes avoid synchronous handle serialization."""

    def __init__(self, path: str, timeout: float) -> None:
        if os.name != "nt":
            raise DesktopDisconnected("Codex Desktop IPC currently requires Windows")
        from ctypes import wintypes as w
        self._w = w
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)

        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                        ("Offset", w.DWORD), ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]

        self._overlapped = Overlapped
        signatures = {
            "CreateFileW": ([w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD, w.HANDLE], w.HANDLE),
            "CreateEventW": ([ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR], w.HANDLE),
            "WaitNamedPipeW": ([w.LPCWSTR, w.DWORD], w.BOOL),
            "ReadFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(Overlapped)], w.BOOL),
            "WriteFile": ([w.HANDLE, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.POINTER(Overlapped)], w.BOOL),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetOverlappedResult": ([w.HANDLE, ctypes.POINTER(Overlapped), ctypes.POINTER(w.DWORD), w.BOOL], w.BOOL),
            "CancelIoEx": ([w.HANDLE, ctypes.c_void_p], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._kernel, name)
            function.argtypes, function.restype = arguments, result
        self._handle = self._kernel.CreateFileW(path, 0xC0000000, 0, None, 3, 0x40000000, None)
        if self._handle == ctypes.c_void_p(-1).value:
            error = ctypes.get_last_error()
            if error == 231 and self._kernel.WaitNamedPipeW(path, int(timeout * 1000)):
                self._handle = self._kernel.CreateFileW(path, 0xC0000000, 0, None, 3, 0x40000000, None)
            if self._handle == ctypes.c_void_p(-1).value:
                raise DesktopDisconnected("Codex Desktop IPC is unavailable")

    def _io(self, operation: str, buffer: Any, size: int, timeout: float | None) -> int:
        kernel, handle = self._kernel, self._handle
        event = kernel.CreateEventW(None, True, False, None)
        overlapped = self._overlapped()
        overlapped.hEvent = event
        transferred = self._w.DWORD()
        try:
            ok = getattr(kernel, operation)(handle, buffer, size, ctypes.byref(transferred), ctypes.byref(overlapped))
            if not ok:
                error = ctypes.get_last_error()
                if error != 997:
                    raise DesktopDisconnected(f"Desktop pipe I/O failed ({error})")
                wait = kernel.WaitForSingleObject(event, 0xFFFFFFFF if timeout is None else max(1, int(timeout * 1000)))
                if wait != 0:
                    kernel.CancelIoEx(handle, ctypes.byref(overlapped))
                    kernel.WaitForSingleObject(event, 0xFFFFFFFF)
                    raise DesktopDisconnected("Desktop pipe write timed out")
                if not kernel.GetOverlappedResult(handle, ctypes.byref(overlapped), ctypes.byref(transferred), False):
                    raise DesktopDisconnected("Desktop pipe disconnected")
            return transferred.value
        finally:
            kernel.CloseHandle(event)

    def read(self, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        transferred = self._io("ReadFile", buffer, size, None)
        return buffer.raw[:transferred]

    def write(self, payload: bytes) -> None:
        buffer = ctypes.create_string_buffer(payload)
        if self._io("WriteFile", buffer, len(payload), 5.0) != len(payload):
            raise DesktopDisconnected("Short Desktop pipe write")

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            self._kernel.CancelIoEx(handle, None)
            self._kernel.CloseHandle(handle)


class DesktopClient:
    """Callbacks run on the reader thread; enqueue work instead of waiting in them."""

    def __init__(self, on_state: Callable[[str, dict[str, Any]], None] | None = None,
                 on_disconnect: Callable[[str], None] | None = None, *,
                 pipe_factory: Callable[..., Any] | None = None, timeout: float = 5.0,
                 discovery_timeout: float = 2.0) -> None:
        self.on_state, self.on_disconnect = on_state, on_disconnect
        self.timeout = timeout
        self.discovery_timeout = discovery_timeout
        self._factory = pipe_factory or _WindowsPipe
        self._pipe: Any = None
        self._reader: threading.Thread | None = None
        self._condition = threading.Condition(threading.RLock())
        self._write_lock = threading.Lock()
        self._operations = threading.RLock()
        self._pending: dict[str, dict[str, Any]] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._owners: dict[str, str] = {}
        self._revisions: dict[str, int] = {}
        self._snapshots: dict[str, int] = {}
        self._generations: dict[str, int] = {}
        self._client_id = "initializing-client"
        self._closed = True

    def connect(self) -> None:
        with self._operations:
            if self._pipe is not None:
                return
            self._closed = False
            self._client_id = "initializing-client"
            self._pipe = self._factory(PIPE_PATH, self.timeout)
            self._reader = threading.Thread(target=self._read_loop, args=(self._pipe,), daemon=True,
                                            name="codex-desktop-ipc")
            self._reader.start()
            try:
                reply = self._request("initialize", {"clientType": "codex-agent-console"}, 0)
                self._client_id = reply["result"]["clientId"]
            except Exception:
                self.close()
                raise

    def reconnect(self) -> None:
        self.close()
        self.connect()

    def _send(self, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if not payload or len(payload) > MAX_FRAME:
            raise DesktopProtocolError("Invalid Desktop IPC frame length")
        with self._write_lock:
            pipe = self._pipe
            if pipe is None:
                raise DesktopDisconnected("Codex Desktop is disconnected")
            pipe.write(struct.pack("<I", len(payload)) + payload)

    def _request(self, method: str, params: dict[str, Any], version: int, *,
                 target: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        request_timeout = self.timeout if timeout is None else timeout
        identity = str(uuid.uuid4())
        pending: dict[str, Any] = {"event": threading.Event()}
        with self._condition:
            self._pending[identity] = pending
        try:
            request = {"type": "request", "requestId": identity, "sourceClientId": self._client_id,
                       "version": version, "method": method, "params": params,
                       "timeoutMs": int(request_timeout * 1000)}
            if target:
                request["targetClientId"] = target
            self._send(request)
            if not pending["event"].wait(request_timeout + 0.25):
                raise DesktopError(f"Desktop request timed out: {method}; outcome may be unknown")
            if "error" in pending:
                raise DesktopDisconnected(pending["error"])
            reply = pending["response"]
            if reply.get("resultType") != "success":
                raise DesktopError(str(reply.get("error", "Desktop request failed")))
            return reply
        finally:
            with self._condition:
                self._pending.pop(identity, None)

    def _broadcast_follow(self, thread_id: str, owner: str, following: bool) -> None:
        self._send({"type": "broadcast", "method": "thread-stream-following-changed",
                    "version": 1, "sourceClientId": self._client_id, "targetClientIds": [owner],
                    "params": {"hostId": "local", "conversationId": thread_id, "following": following}})

    def follow(self, thread_id: str) -> dict[str, Any]:
        return self.refresh(thread_id)

    def unfollow(self, thread_id: str) -> None:
        """Stop owner deltas and release cached history for a terminal thread."""
        with self._operations:
            with self._condition:
                owner = self._owners.get(thread_id)
            if owner and self._pipe is not None:
                self._broadcast_follow(thread_id, owner, False)
            with self._condition:
                for cache in (self._owners, self._states, self._revisions,
                              self._snapshots, self._generations):
                    cache.pop(thread_id, None)

    def refresh(self, thread_id: str) -> dict[str, Any]:
        """Wait for a new full owner snapshot, even if its revision is unchanged."""
        with self._operations:
            reply = self._request("thread-owner-discovery", {"hostId": "local", "conversationId": thread_id}, 1,
                                  timeout=min(self.timeout, self.discovery_timeout))
            owner = reply.get("handledByClientId")
            if not isinstance(owner, str):
                raise DesktopProtocolError("Desktop did not identify the thread owner")
            with self._condition:
                before = self._snapshots.get(thread_id, 0)
                self._owners[thread_id] = owner
            self._broadcast_follow(thread_id, owner, False)
            self._broadcast_follow(thread_id, owner, True)
            deadline = time.monotonic() + self.timeout
            with self._condition:
                while self._snapshots.get(thread_id, 0) <= before:
                    if self._pipe is None:
                        raise DesktopDisconnected("Desktop disconnected while refreshing")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise DesktopError("No fresh Desktop thread snapshot received")
                    self._condition.wait(remaining)
                return normalize_state(self._states[thread_id])

    @staticmethod
    def _check_resume(state: dict[str, Any], expected_turn_id: str) -> None:
        from codex_quota_resume import failure_identity
        if state.get("status", {}).get("type") not in ("idle", "systemError"):
            raise DesktopResumeRejected("Thread is active or its live status is unavailable")
        if state.get("pendingRequests") or state.get("pendingSubmissions"):
            raise DesktopResumeRejected("Thread has pending input or an unconfirmed submission")
        if failure_identity(state) != expected_turn_id:
            raise DesktopResumeRejected("The quota failure changed or was manually stopped")

    def resume(self, thread_id: str, expected_turn_id: str, prompt: str, *,
               should_continue: Callable[[], bool] | None = None) -> dict[str, Any] | bool:
        """Double-check live state, then dispatch once to its existing Desktop owner."""
        with self._operations:
            permitted = should_continue or (lambda: True)
            if not permitted():
                return False
            self._check_resume(self.refresh(thread_id), expected_turn_id)
            if not permitted():
                return False
            self._check_resume(self.refresh(thread_id), expected_turn_id)
            if not permitted():
                return False
            with self._condition:
                generation = self._generations.get(thread_id)
                self._check_resume(normalize_state(self._states[thread_id]), expected_turn_id)
                owner = self._owners[thread_id]
            # No retry on timeout: Desktop might already have accepted the request.
            with self._condition:
                if self._generations.get(thread_id) != generation:
                    raise DesktopResumeRejected("Thread changed immediately before resume")
            if not permitted():
                return False
            return self._request("thread-follower-start-turn", {
                "conversationId": thread_id,
                "turnStart": {"request": {"threadId": thread_id,
                    "input": [{"type": "text", "text": prompt, "text_elements": []}],
                    "clientUserMessageId": str(uuid.uuid4())},
                    "context": {"inheritThreadSettings": True}},
            }, 2, target=owner)

    def _read_loop(self, pipe: Any) -> None:
        def read_exact(size: int) -> bytes:
            chunks, remaining = [], size
            while remaining:
                data = pipe.read(remaining)
                if not data:
                    raise DesktopDisconnected("Codex Desktop closed its IPC connection")
                chunks.append(data)
                remaining -= len(data)
            return b"".join(chunks)
        try:
            while True:
                length = struct.unpack("<I", read_exact(4))[0]
                if length <= 0 or length > MAX_FRAME:
                    raise DesktopProtocolError("Invalid Desktop IPC frame length")
                self._handle_message(json.loads(read_exact(length).decode("utf-8")))
        except Exception as exc:
            self._disconnect(str(exc), pipe)

    def _handle_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "response":
            with self._condition:
                pending = self._pending.get(message.get("requestId"))
                if pending is not None:
                    pending["response"] = message
                    pending["event"].set()
            return
        if kind == "client-discovery-request":
            self._send({"type": "client-discovery-response", "requestId": message.get("requestId"),
                        "response": {"canHandle": False}})
            return
        if kind != "broadcast" or message.get("method") != "thread-stream-state-changed":
            return
        if message.get("version") != STREAM_VERSION:
            raise DesktopProtocolError("Unsupported Desktop thread stream version")
        params = message.get("params", {})
        thread_id, change = params.get("conversationId"), params.get("change", {})
        with self._condition:
            if params.get("hostId") != "local" or self._owners.get(thread_id) != message.get("sourceClientId"):
                return
            revision = change.get("revision")
            if not isinstance(revision, int):
                raise DesktopProtocolError("Missing Desktop stream revision")
            if change.get("type") == "snapshot":
                state = change.get("conversationState")
                if not isinstance(state, dict) or state.get("id") != thread_id:
                    raise DesktopProtocolError("Invalid Desktop thread snapshot")
                self._states[thread_id] = state
                self._snapshots[thread_id] = self._snapshots.get(thread_id, 0) + 1
            elif change.get("type") == "patches":
                if self._revisions.get(thread_id) != change.get("baseRevision"):
                    raise DesktopProtocolError("Desktop stream revision gap; reconnect required")
                self._states[thread_id] = apply_patches(self._states[thread_id], change.get("patches", []))
            else:
                raise DesktopProtocolError("Unsupported Desktop stream change")
            self._revisions[thread_id] = revision
            self._generations[thread_id] = self._generations.get(thread_id, 0) + 1
            normalized = normalize_state(self._states[thread_id])
            self._condition.notify_all()
        if self.on_state:
            self.on_state(thread_id, normalized)

    def _disconnect(self, reason: str, pipe: Any) -> None:
        with self._condition:
            if self._pipe is not pipe:
                return
            self._pipe = None
            self._states.clear()
            self._owners.clear()
            self._revisions.clear()
            self._snapshots.clear()
            self._generations.clear()
            for pending in self._pending.values():
                pending["error"] = reason
                pending["event"].set()
            self._condition.notify_all()
        pipe.close()
        if not self._closed and self.on_disconnect:
            self.on_disconnect(reason)

    def close(self) -> None:
        self._closed = True
        pipe = self._pipe
        if pipe is not None:
            self._disconnect("Desktop client closed", pipe)
        reader = self._reader
        if reader and reader is not threading.current_thread():
            reader.join(timeout=2.0)
        self._reader = None
