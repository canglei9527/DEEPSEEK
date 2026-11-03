"""Desktop wire contract tests; no model turns or desktop mutations are sent."""
import copy
import json
import struct
import threading
import unittest

from codex_desktop_ipc import (
    DesktopClient, DesktopDisconnected, DesktopProtocolError,
    DesktopResumeRejected, apply_patches, normalize_state,
)


def raw_state():
    return {"id": "thread", "title": "Test", "latestModel": "model",
            "threadRuntimeStatus": {"type": "idle"}, "threadGoal": None,
            "turns": [], "requests": [],
            "turnHistory": {"kind": "canonical", "history": {
                "entitiesByKey": {"entry": {"turnId": "failed-turn", "status": "failed",
                    "error": {"message": "quota", "codexErrorInfo": "usageLimitExceeded"},
                    "items": [{"text": "large tool contents"}]}},
                "islands": [{"entries": [{"value": "entry"}]}]}}}


class FakePipe:
    def __init__(self, *_):
        self.condition = threading.Condition()
        self.buffer = bytearray()
        self.closed = False
        self.raw = raw_state()
        self.messages = []
        self.mutations = []
        self.snapshots = 0

    def feed(self, message):
        encoded = json.dumps(message).encode()
        with self.condition:
            self.buffer.extend(struct.pack("<I", len(encoded)) + encoded)
            self.condition.notify_all()

    def read(self, size):
        with self.condition:
            while not self.buffer and not self.closed:
                self.condition.wait()
            # Exercise partial framing, including a header split across reads.
            amount = min(size, len(self.buffer), 19)
            result = bytes(self.buffer[:amount])
            del self.buffer[:amount]
            return result

    def write(self, frame):
        message = json.loads(frame[4:])
        self.messages.append(message)
        method = message.get("method")
        if message.get("type") == "request":
            if method == "initialize":
                result = {"clientId": "client"}
            elif method == "thread-owner-discovery":
                result = {"supportsUntrustedAppInput": True}
            elif method == "thread-follower-start-turn":
                self.mutations.append(message)
                result = {"result": {"turn": {"id": "new-turn"}}}
            else:
                raise AssertionError(method)
            self.feed({"type": "response", "requestId": message["requestId"],
                       "method": method, "resultType": "success", "result": result,
                       "handledByClientId": "owner"})
        elif method == "thread-stream-following-changed" and message["params"]["following"]:
            self.snapshots += 1
            self.feed({"type": "broadcast", "method": "thread-stream-state-changed",
                       "version": 11, "sourceClientId": "owner",
                       "params": {"conversationId": "thread", "hostId": "local",
                                  "change": {"type": "snapshot", "revision": 1,
                                             "conversationState": copy.deepcopy(self.raw)}}})

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()


class DesktopProtocolTests(unittest.TestCase):
    def test_canonical_and_current_overlay_preserve_real_error(self):
        raw = raw_state()
        raw["turns"] = [{"turnId": "newer", "status": "interrupted", "error": None}]
        state = normalize_state(raw)
        self.assertEqual([turn["id"] for turn in state["turns"]], ["failed-turn", "newer"])
        self.assertEqual(state["turns"][0]["error"]["codexErrorInfo"], "usageLimitExceeded")
        self.assertNotIn("items", state["turns"][0])
        self.assertIsNone(state["turns"][-1]["error"])

    def test_unknown_status_is_not_invented_as_idle(self):
        self.assertEqual(normalize_state({})["status"], {"type": "unknown"})

    def test_patches_are_transactional_and_support_array_changes(self):
        original = {"turns": [{"status": "inProgress"}], "old": True}
        result = apply_patches(original, [
            {"op": "replace", "path": ["turns", 0, "status"], "value": "failed"},
            {"op": "add", "path": ["turns", 1], "value": {"status": "completed"}},
            {"op": "remove", "path": ["old"]},
        ])
        self.assertEqual(original["turns"][0]["status"], "inProgress")
        self.assertEqual(result, {"turns": [{"status": "failed"}, {"status": "completed"}]})
        with self.assertRaises(DesktopProtocolError):
            apply_patches(original, [{"op": "remove", "path": ["missing"]}])
        self.assertIn("old", original)

    def make_client(self, **kwargs):
        pipe = FakePipe()
        client = DesktopClient(pipe_factory=lambda *_: pipe, timeout=0.5, **kwargs)
        client.connect()
        self.addCleanup(client.close)
        return client, pipe

    def test_real_framing_owner_follow_and_same_revision_freshness(self):
        callback = []
        client, pipe = self.make_client(on_state=lambda thread, state: callback.append((thread, state)))
        self.assertEqual(client.follow("thread")["turns"][-1]["id"], "failed-turn")
        self.assertEqual(client.refresh("thread")["status"]["type"], "idle")
        self.assertEqual(pipe.snapshots, 2)
        self.assertEqual(len(callback), 2)
        self.assertFalse(pipe.mutations)

    def test_resume_dispatches_once_to_existing_owner_without_setting_overrides(self):
        client, pipe = self.make_client()
        reply = client.resume("thread", "failed-turn", "continue")
        self.assertEqual(reply["resultType"], "success")
        self.assertEqual(len(pipe.mutations), 1)
        mutation = pipe.mutations[0]
        self.assertEqual(mutation["targetClientId"], "owner")
        self.assertEqual(mutation["version"], 2)
        request = mutation["params"]["turnStart"]["request"]
        self.assertEqual(request["input"][0]["text"], "continue")
        self.assertNotIn("model", request)
        self.assertNotIn("approvalPolicy", request)

    def test_manual_stop_pause_or_new_turn_prevents_dispatch(self):
        for change in ("interrupted", "paused", "new-turn", "active", "pending"):
            with self.subTest(change=change):
                client, pipe = self.make_client()
                turn = pipe.raw["turnHistory"]["history"]["entitiesByKey"]["entry"]
                if change == "interrupted":
                    turn["status"], turn["error"] = "interrupted", None
                elif change == "paused":
                    pipe.raw["threadGoal"] = {"status": "paused"}
                elif change == "new-turn":
                    turn["turnId"] = "other-turn"
                elif change == "active":
                    pipe.raw["threadRuntimeStatus"] = {"type": "active"}
                else:
                    pipe.raw["requests"] = [{"type": "userInput"}]
                with self.assertRaises(DesktopResumeRejected):
                    client.resume("thread", "failed-turn", "continue")
                self.assertFalse(pipe.mutations)
                client.close()

    def test_cancel_after_second_live_read_prevents_dispatch(self):
        client, pipe = self.make_client()
        calls = []
        def permitted():
            calls.append(True)
            return len(calls) < 3
        self.assertFalse(client.resume("thread", "failed-turn", "continue", should_continue=permitted))
        self.assertEqual(pipe.snapshots, 2)
        self.assertFalse(pipe.mutations)

    def test_revision_gap_and_future_protocol_fail_closed(self):
        client, pipe = self.make_client()
        client.follow("thread")
        message = {"type": "broadcast", "method": "thread-stream-state-changed", "version": 11,
                   "sourceClientId": "owner", "params": {"hostId": "local", "conversationId": "thread",
                   "change": {"type": "patches", "baseRevision": 9, "revision": 10, "patches": []}}}
        with self.assertRaises(DesktopProtocolError):
            client._handle_message(message)
        message["version"] = 12
        with self.assertRaises(DesktopProtocolError):
            client._handle_message(message)
        self.assertFalse(pipe.mutations)

    def test_close_unblocks_reader_and_reconnect_can_be_explicit(self):
        client, pipe = self.make_client()
        reader = client._reader
        client.close()
        self.assertFalse(reader.is_alive())
        self.assertTrue(pipe.closed)
        with self.assertRaises(DesktopDisconnected):
            client.refresh("thread")
        replacement = FakePipe()
        client._factory = lambda *_: replacement
        client.reconnect()
        self.assertEqual(client.follow("thread")["id"], "thread")


if __name__ == "__main__":
    unittest.main()
