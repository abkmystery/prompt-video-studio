import io
import json
import queue
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from studio.codex_bridge import CodexBridge, CodexError


class PlanningBridge(CodexBridge):
    def __init__(self, events, fail_start=False):
        super().__init__(Path.cwd())
        self.events = events
        self.calls = []
        self.fail_start = fail_start

    def _start(self):
        self._ready = True

    def status(self):
        return {"available": True, "connected": True, "auth_mode": "chatgpt", "plan": "plus"}

    def _rpc(self, method, params, timeout=20):
        self.calls.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread1"}}
        if method == "turn/start":
            if self.fail_start:
                raise CodexError("Timeout")
            with self._state:
                for event in self.events:
                    self._sequence += 1
                    self._events.append((self._sequence, event))
            return {"turn": {"id": "turn1"}}
        return {}


def completed(status="completed", text=None):
    items = [] if text is None else [{"type": "agentMessage", "id": "answer", "text": text}]
    return {"method": "turn/completed", "params": {"threadId": "thread1", "turn":
        {"id": "turn1", "status": status, "items": items}}}


class CodexBridgeTests(unittest.TestCase):
    def test_status_strips_credentials_and_email(self):
        bridge = CodexBridge(Path.cwd())
        with patch.object(bridge, "_find_command", return_value=["codex"]), \
             patch.object(bridge, "_start"), patch.object(bridge, "_rpc", return_value={
                 "account": {"type": "chatgpt", "email": "private@example.test",
                             "planType": "plus", "accessToken": "SECRET", "refreshToken": "SECRET2"}}) as rpc:
            status = bridge.status()
        self.assertEqual(status["plan"], "plus")
        self.assertTrue(status["connected"])
        self.assertNotIn("SECRET", json.dumps(status))
        self.assertNotIn("private@", json.dumps(status))
        rpc.assert_called_once_with("account/read", {"refreshToken": False})

    def test_login_is_official_and_deduplicated_and_cancel_owned(self):
        bridge = CodexBridge(Path.cwd())
        with patch.object(bridge, "_start"), patch.object(bridge, "_rpc", return_value={
            "type": "chatgpt", "loginId": "l1", "authUrl": "https://auth.openai.com/authorize?state=example"}) as rpc:
            first = bridge.login()
            self.assertEqual(first, bridge.login())
            self.assertEqual(rpc.call_count, 1)
            self.assertEqual(bridge.cancel_login("someone-else"), {"cancelled": False})
            self.assertEqual(bridge.cancel_login("l1"), {"cancelled": True})
            self.assertEqual(rpc.call_count, 2)
            self.assertNotIn("account/logout", str(rpc.call_args_list))

    def test_login_rejects_non_openai_url(self):
        bridge = CodexBridge(Path.cwd())
        with patch.object(bridge, "_start"), patch.object(bridge, "_rpc", return_value={
            "loginId": "l1", "authUrl": "https://attacker.test/signin"}):
            with self.assertRaises(CodexError):
                bridge.login()

    def test_rpc_routes_by_id_and_redacts_upstream_error(self):
        bridge = CodexBridge(Path.cwd())
        messages = []
        def send(message):
            messages.append(message)
            bridge._pending[message["id"]].put({"id": message["id"],
                "error": {"code": -1, "message": "SECRET credential=abc"}})
        with patch.object(bridge, "_send", side_effect=send):
            with self.assertRaises(CodexError) as caught:
                bridge._rpc("account/read", {})
        self.assertNotIn("SECRET", str(caught.exception))
        self.assertEqual(bridge._pending, {})
        self.assertIsInstance(messages[0]["id"], int)

    def test_rpc_timeout_cleans_pending(self):
        bridge = CodexBridge(Path.cwd())
        with patch.object(bridge, "_send"):
            with self.assertRaises(CodexError):
                bridge._rpc("account/read", {}, timeout=.01)
        self.assertEqual(bridge._pending, {})

    def test_approval_and_unknown_tool_requests_are_rejected(self):
        bridge = CodexBridge(Path.cwd())
        with patch.object(bridge, "_send") as send:
            bridge._deny_server_request({"id": 90, "method": "item/commandExecution/requestApproval"})
            self.assertEqual(send.call_args.args[0], {"id": 90, "result": {"decision": "cancel"}})
            bridge._deny_server_request({"id": 91, "method": "account/chatgptAuthTokens/refresh"})
            self.assertEqual(send.call_args.args[0]["error"]["code"], -32601)

    def test_early_events_complete_plan_and_other_thread_is_ignored(self):
        irrelevant = {"method": "turn/completed", "params": {"threadId": "elsewhere", "turn": {"id": "turn1", "status": "failed"}}}
        item = {"method": "item/completed", "params": {"threadId": "thread1", "turnId": "turn1",
                "item": {"type": "agentMessage", "id": "a", "phase": "final_answer", "text": '{"title":"A small kindness"}'}}}
        bridge = PlanningBridge([irrelevant, item, completed()])
        schema = {"type": "object"}
        self.assertEqual(bridge.generate_plan("kindness", schema, "Make a story."), {"title": "A small kindness"})
        thread = bridge.calls[0][1]
        turn = bridge.calls[1][1]
        self.assertEqual(thread["sandbox"], "read-only")
        self.assertTrue(thread["ephemeral"])
        self.assertEqual(turn["approvalPolicy"], "never")
        self.assertFalse(turn["sandboxPolicy"]["networkAccess"])
        self.assertEqual(turn["outputSchema"], schema)
        self.assertNotIn("model", thread)
        self.assertNotIn("model", turn)
        self.assertEqual(bridge.calls[-1][0], "thread/unsubscribe")

    def test_final_items_fallback(self):
        bridge = PlanningBridge([completed(text='{"title":"Hello"}')])
        self.assertEqual(bridge.generate_plan("Hello", {}, "Plan"), {"title": "Hello"})

    def test_failed_turn_is_interrupted_and_not_returned(self):
        bridge = PlanningBridge([completed("failed")])
        with self.assertRaises(CodexError):
            bridge.generate_plan("Hello", {}, "Plan")
        self.assertIn("turn/interrupt", [method for method, _ in bridge.calls])

    def test_timed_out_start_stops_process_to_avoid_orphan_generation(self):
        bridge = PlanningBridge([], fail_start=True)
        with patch.object(bridge, "_stop_process") as stop:
            with self.assertRaises(CodexError):
                bridge.generate_plan("Hello", {}, "Plan")
            stop.assert_called_once()

    def test_invalid_json_never_becomes_code(self):
        bridge = PlanningBridge([completed(text='__import__("os").system("bad")')])
        with self.assertRaises(CodexError):
            bridge.generate_plan("Hello", {}, "Plan")

    def test_production_preserves_normal_tools_and_uses_scoped_permissions(self):
        bridge = PlanningBridge([completed(text="Created final.mp4")])
        events = []
        result = bridge.start_production(Path.cwd(), "Make a free video", events.append, threading.Event())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["thread_id"], "thread1")
        thread = bridge.calls[0][1]
        turn = bridge.calls[1][1]
        self.assertEqual(thread["approvalPolicy"], "on-request")
        self.assertEqual(thread["sandbox"], "workspace-write")
        self.assertEqual(thread["approvalsReviewer"], "user")
        self.assertNotIn("model", thread)
        self.assertNotIn("config", thread)
        self.assertNotIn("baseInstructions", thread)
        self.assertNotIn("outputSchema", turn)
        self.assertFalse(turn["sandboxPolicy"]["networkAccess"])
        self.assertEqual(len(turn["sandboxPolicy"]["writableRoots"]), 3)
        self.assertEqual(events[0], {"type": "thread", "thread_id": "thread1"})

    def test_production_resume_uses_existing_thread(self):
        bridge = PlanningBridge([completed(text="Resumed")])
        original_rpc = bridge._rpc
        def rpc(method, params, timeout=20):
            if method == "thread/resume":
                bridge.calls.append((method, params))
                return {"thread": {"id": "thread1"}}
            return original_rpc(method, params, timeout)
        bridge._rpc = rpc
        result = bridge.start_production(Path.cwd(), "Continue", lambda event: None, threading.Event(), thread_id="thread1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(bridge.calls[0][0], "thread/resume")
        self.assertTrue(bridge.calls[0][1]["excludeTurns"])

    def test_production_cancel_before_start_uses_no_turn(self):
        bridge = PlanningBridge([])
        cancelled = threading.Event()
        cancelled.set()
        result = bridge.start_production(Path.cwd(), "Make a video", lambda event: None, cancelled)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(bridge.calls, [])

    def test_production_rejects_workspace_outside_studio(self):
        bridge = PlanningBridge([])
        with self.assertRaises(CodexError):
            bridge.start_production(Path.cwd().parent, "Make a video", lambda event: None, threading.Event())

    def test_approvals_queue_and_resolve_only_on_explicit_decision(self):
        bridge = CodexBridge(Path.cwd())
        events = []
        bridge._production_callback = events.append
        bridge._active_thread = "thread1"
        with patch.object(bridge, "_send") as send:
            bridge._handle_server_request({"id": 90, "method": "item/commandExecution/requestApproval", "params": {
                "threadId": "thread1", "command": "echo hello", "reason": "Run a command"}})
            send.assert_not_called()
            self.assertEqual(bridge.pending_approvals()[0]["command"], "echo hello")
            self.assertEqual(events[0]["type"], "approval")
            bridge.resolve_approval("90", True)
            self.assertEqual(send.call_args.args[0], {"id": 90, "result": {"decision": "accept"}})
            self.assertEqual(bridge.pending_approvals(), [])
            with self.assertRaises(CodexError):
                bridge.resolve_approval("90", True)

    def test_permissions_request_is_turn_scoped_and_never_auto_accepted(self):
        bridge = CodexBridge(Path.cwd())
        bridge._production_callback = lambda event: None
        bridge._active_thread = "thread1"
        permissions = {"network": {"enabled": True}}
        with patch.object(bridge, "_send") as send:
            bridge._handle_server_request({"id": "p1", "method": "item/permissions/requestApproval", "params": {
                "threadId": "thread1", "permissions": permissions}})
            send.assert_not_called()
            bridge.resolve_approval("p1", True)
            self.assertEqual(send.call_args.args[0]["result"], {"permissions": permissions, "scope": "turn"})

    def test_approval_for_unrelated_thread_is_denied(self):
        bridge = CodexBridge(Path.cwd())
        bridge._production_callback = lambda event: None
        bridge._active_thread = "thread1"
        with patch.object(bridge, "_send") as send:
            bridge._handle_server_request({"id": 99, "method": "item/fileChange/requestApproval", "params": {"threadId": "other"}}, verify_unknown=False)
            self.assertEqual(send.call_args.args[0]["result"]["decision"], "cancel")
            self.assertEqual(bridge.pending_approvals(), [])

    def test_verified_descendant_approval_is_queued(self):
        bridge = CodexBridge(Path.cwd())
        bridge._production_callback = lambda event: None
        bridge._active_thread = "parent"
        bridge._observe_production_ancestry("thread/started", {"thread":{"id":"child","parentThreadId":"parent"}})
        with patch.object(bridge, "_send") as send:
            bridge._handle_server_request({"id":101,"method":"item/commandExecution/requestApproval","params":{
                "threadId":"child","command":"echo test","cwd":str(Path.cwd())}})
            send.assert_not_called()
            self.assertEqual(bridge.pending_approvals()[0]["id"],"101")
            bridge.resolve_approval("101",False)
            self.assertEqual(send.call_args.args[0]["result"]["decision"],"decline")

    def test_metadata_verification_rejects_unrelated_parent(self):
        bridge = CodexBridge(Path.cwd())
        bridge._active_thread = "parent"
        with patch.object(bridge,"_rpc",return_value={"thread":{"id":"unrelated"}}) as rpc:
            self.assertFalse(bridge._verify_production_descendant("unrelated","parent"))
            self.assertEqual(rpc.call_args.args[0],"thread/read")
        self.assertFalse(bridge._is_production_thread("unrelated"))

    def test_progress_redacts_common_credentials(self):
        text = CodexBridge._safe_text('Authorization: Bearer secretstuff api_key=sk-1234567890')
        self.assertNotIn("secretstuff", text)
        self.assertNotIn("1234567890", text)

    def test_reader_routes_response_and_does_not_retain_account_notifications(self):
        bridge = CodexBridge(Path.cwd())
        response_queue = queue.Queue()
        bridge._pending[7] = response_queue
        class FakeProcess:
            stdout = io.StringIO(json.dumps({"id": 7, "result": {"okay": True}}) + "\n" +
                json.dumps({"method": "account/updated", "params": {"authMode": "chatgpt", "private": "SECRET"}}) + "\n")
        proc = FakeProcess()
        bridge._proc = proc
        bridge._ready = True
        bridge._read_loop(proc)
        self.assertEqual(response_queue.get_nowait()["result"], {"okay": True})
        self.assertEqual(list(bridge._events), [])
        self.assertFalse(bridge._ready)

    def test_old_process_eof_does_not_cancel_new_process_requests(self):
        bridge = CodexBridge(Path.cwd())
        bridge._proc = object()
        bridge._ready = True
        response_queue = queue.Queue()
        bridge._pending[8] = response_queue
        class OldProcess:
            stdout = io.StringIO("")
        bridge._read_loop(OldProcess())
        self.assertTrue(response_queue.empty())
        self.assertTrue(bridge._ready)

    def test_truncated_request_cannot_be_approved_blindly(self):
        bridge = CodexBridge(Path.cwd())
        bridge._production_callback = lambda event: None
        bridge._active_thread = "thread1"
        with patch.object(bridge, "_send") as send:
            bridge._handle_server_request({"id": "big", "method": "item/commandExecution/requestApproval", "params": {
                "threadId": "thread1", "command": "x" * 25000}})
            self.assertFalse(bridge.pending_approvals()[0]["reviewable"])
            with self.assertRaises(CodexError):
                bridge.resolve_approval("big", True)
            send.assert_not_called()
            bridge.resolve_approval("big", False)
            self.assertEqual(send.call_args.args[0]["result"]["decision"], "decline")

    def test_large_media_record_does_not_break_following_response(self):
        bridge = CodexBridge(Path.cwd())
        response_queue = queue.Queue()
        bridge._pending[7] = response_queue
        class FakeProcess:
            stdout = io.StringIO('x' * 2_000_010 + "\n" + json.dumps({"id": 7, "result": {"okay": True}}) + "\n")
        proc = FakeProcess()
        bridge._proc = proc
        bridge._read_loop(proc)
        self.assertEqual(response_queue.get_nowait()["result"], {"okay": True})

    def test_callbacks_and_pipe_io_are_outside_state_lock(self):
        bridge = CodexBridge(Path.cwd())
        bridge._active_thread = "thread1"
        callbacks = []
        callback_lock_states = []
        def callback(event):
            callbacks.append(event)
            callback_lock_states.append(bridge._state._is_owned())
        bridge._production_callback = callback
        def send(message):
            self.assertFalse(bridge._state._is_owned())
        with patch.object(bridge, "_send", side_effect=send):
            bridge._handle_server_request({"id": "safe", "method": "item/commandExecution/requestApproval", "params": {
                "threadId": "thread1", "command": "echo hello"}})
            bridge.resolve_approval("safe", True)
        self.assertEqual(len(callbacks), 2)
        self.assertEqual(callback_lock_states, [False, False])

    def test_file_approval_uses_prior_item_paths_and_diff(self):
        bridge = CodexBridge(Path.cwd())
        bridge._active_thread = "thread1"
        bridge._production_callback = lambda event: None
        changes = [{"path": "output/script.py", "kind": {"type": "update"}, "diff": "+print('hello')"}]
        bridge._tool_items[("thread1", "file1")] = {"changes": changes}
        with patch.object(bridge, "_send") as send:
            bridge._handle_server_request({"id": "file", "method": "item/fileChange/requestApproval", "params": {
                "threadId": "thread1", "itemId": "file1"}})
            approval = bridge.pending_approvals()[0]
            self.assertEqual(approval["files"], ["output/script.py"])
            self.assertIn("print", approval["changes"])
            self.assertTrue(approval["reviewable"])
            bridge.resolve_approval("file", True)
            self.assertEqual(send.call_args.args[0]["result"]["decision"], "accept")

    def test_file_root_grant_and_missing_file_details_are_decline_only(self):
        for request in ({"grantRoot": "C:/"}, {}):
            bridge = CodexBridge(Path.cwd())
            bridge._active_thread = "thread1"
            bridge._production_callback = lambda event: None
            with patch.object(bridge, "_send"):
                bridge._handle_server_request({"id": "file", "method": "item/fileChange/requestApproval", "params": {
                    "threadId": "thread1", "itemId": "missing", **request}})
                self.assertFalse(bridge.pending_approvals()[0]["reviewable"])
                with self.assertRaises(CodexError):
                    bridge.resolve_approval("file", True)
                bridge.resolve_approval("file", False)

    def test_legacy_approval_response_is_action_scoped(self):
        for method, fields in (("execCommandApproval", {"command": ["python", "script.py"]}),
                               ("applyPatchApproval", {"fileChanges": {"out.py": {"add": {"content": "print(1)"}}}})):
            bridge = CodexBridge(Path.cwd())
            bridge._active_thread = "thread1"
            bridge._production_callback = lambda event: None
            with patch.object(bridge, "_send") as send:
                bridge._handle_server_request({"id": "legacy", "method": method, "params": {
                    "conversationId": "thread1", **fields}})
                bridge.resolve_approval("legacy", True)
                self.assertEqual(send.call_args.args[0]["result"]["decision"], "approved")

    def test_cancel_after_thread_creation_does_not_start_model_turn(self):
        bridge = PlanningBridge([])
        cancelled = threading.Event()
        def callback(event):
            if event["type"] == "thread":
                cancelled.set()
        result = bridge.start_production(Path.cwd(), "Make a video", callback, cancelled)
        self.assertEqual(result["status"], "cancelled")
        self.assertNotIn("turn/start", [method for method, params in bridge.calls])

    def test_denied_permissions_use_valid_empty_turn_grant(self):
        bridge = CodexBridge(Path.cwd())
        with patch.object(bridge, "_send") as send:
            bridge._deny_server_request({"id": "no", "method": "item/permissions/requestApproval"})
            self.assertEqual(send.call_args.args[0]["result"], {"permissions": {}, "scope": "turn"})

    def test_close_is_idempotent_and_does_not_logout(self):
        bridge = CodexBridge(Path.cwd())
        with patch.object(bridge, "_rpc") as rpc:
            bridge.close()
            bridge.close()
            rpc.assert_not_called()
        with self.assertRaises(CodexError):
            bridge._start()


if __name__ == "__main__":
    unittest.main()
