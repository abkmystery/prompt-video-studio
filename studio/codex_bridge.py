"""Local Codex app-server client. Credentials stay managed by Codex itself.

Protocol: https://learn.chatgpt.com/docs/app-server
Validated against the installed codex-cli 0.153.4 JSON schema. Note that the
thread sandbox enum uses ``read-only``; turn sandboxPolicy uses ``readOnly``.
"""
from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse


class CodexError(RuntimeError):
    """A safe, user-visible error which never includes credential data."""


class CodexBridge:
    """One local app-server, shared auth, and serialized video production turns.

    Constructing the client is inert. ``status`` starts the transport but does
    not start a login, refresh credentials, or consume a model turn. No method
    logs out of Codex. Call ``close`` during the web server's shutdown.
    """

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace).resolve()
        self._proc = None
        self._ready = False
        self._closed = False
        self._lifecycle = threading.RLock()
        self._write_lock = threading.Lock()
        self._state = threading.Condition(threading.RLock())
        self._generation_lock = threading.Lock()
        self._login_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._next_id = 1
        self._events = deque(maxlen=8192)
        self._sequence = 0
        self._login = None
        self._login_error = None
        self._reader = None
        self._approvals = {}
        self._approval_serial = threading.Lock()
        self._tool_items = {}
        self._production_callback = None
        self._active_thread = None
        self._production_parents = {}
        self._verifying_approvals = set()
        self._production_cancel_event = None

    @staticmethod
    def _find_command():
        explicit = os.environ.get("PROMPT_VIDEO_CODEX")
        candidates = [explicit] if explicit else []
        candidates += [shutil.which("codex")]
        if os.name == "nt":
            base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
            if base.is_dir():
                candidates.extend(str(p) for p in sorted(base.glob("*/codex.exe"),
                    key=lambda p: p.stat().st_mtime, reverse=True))
        for candidate in candidates:
            if not candidate or not Path(candidate).is_file():
                continue
            path = Path(candidate)
            if path.suffix.lower() in (".cmd", ".bat", ".ps1"):
                # Run npm's JavaScript entry point directly, never through a shell.
                script = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
                node = shutil.which("node")
                if script.is_file() and node:
                    return [node, str(script)]
                continue
            return [str(path)]
        return None

    def _start(self):
        with self._lifecycle:
            if self._closed:
                raise CodexError("The Codex connection is closed. Restart Prompt Video Studio.")
            if self._proc is not None and self._proc.poll() is None and self._ready:
                return
            self._ready = False
            if self._proc is not None:
                self._stop_process()
            command = self._find_command()
            if not command:
                raise CodexError("Codex CLI was not found. Install Codex or add it to PATH, then restart the studio.")
            # Keep the installed Codex configuration, models, plugins and tools.
            # Permissions are scoped explicitly on each production thread/turn.
            arguments = command + ["app-server"]
            try:
                self._proc = subprocess.Popen(arguments, cwd=str(self.workspace),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, ValueError):
                raise CodexError("Codex could not start. Check its installation and restart the studio.") from None
            with self._state:
                self._events.clear()
                self._tool_items.clear()
                self._approvals.clear()
                self._login = None
            self._reader = threading.Thread(target=self._read_loop, args=(self._proc,),
                                            name="studio-codex", daemon=True)
            self._reader.start()
            try:
                self._rpc("initialize", {"clientInfo": {"name": "prompt_video_studio",
                    "title": "Prompt Video Studio", "version": "0.1.0"}}, timeout=20)
                self._send({"method": "initialized", "params": {}})
                self._ready = True
            except Exception:
                self._stop_process()
                raise

    def _send(self, message):
        with self._write_lock:
            proc = self._proc
            if proc is None or proc.poll() is not None or proc.stdin is None:
                raise CodexError("The Codex connection stopped. Try again.")
            try:
                proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
                proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                raise CodexError("The Codex connection stopped. Try again.") from None

    def _rpc(self, method, params, timeout=20):
        result_queue = queue.Queue(maxsize=1)
        with self._state:
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = result_queue
        try:
            self._send({"id": request_id, "method": method, "params": params})
            try:
                message = result_queue.get(timeout=max(0.01, timeout))
            except queue.Empty:
                raise CodexError(f"Codex timed out during {method}. Try again.") from None
            if "error" in message:
                # Upstream error text and stderr can contain private file paths,
                # prompts, URLs or tokens. Never return them to the web client.
                code = message.get("error", {}).get("code")
                suffix = f" (code {code})" if isinstance(code, int) else ""
                raise CodexError(f"Codex could not complete {method}{suffix}. Check Codex sign-in and account access.")
            result = message.get("result")
            if not isinstance(result, dict):
                raise CodexError("Codex returned an unexpected response. Update Codex and try again.")
            return result
        finally:
            with self._state:
                self._pending.pop(request_id, None)

    def _deny_server_request(self, message):
        method = message.get("method", "")
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            reply = {"result": {"decision": "cancel"}}
        elif method in ("execCommandApproval", "applyPatchApproval"):
            reply = {"result": {"decision": "abort"}}
        elif method == "item/permissions/requestApproval":
            reply = {"result": {"permissions": {}, "scope": "turn"}}
        elif method == "mcpServer/elicitation/request":
            reply = {"result": {"action": "cancel", "content": None}}
        elif method == "item/tool/requestUserInput":
            reply = {"result": {"answers": {}}}
        else:
            reply = {"error": {"code": -32601, "message": "This interactive request is not supported by the studio."}}
        self._send({"id": message["id"], **reply})

    def _read_loop(self, proc):
        try:
            while True:
                line = proc.stdout.readline(2_000_001)
                if not line:
                    break
                if len(line) > 2_000_000:
                    # Media notifications may embed large images. Drain this
                    # record, then keep reading approvals and completion events.
                    while line and not line.endswith("\n"):
                        line = proc.stdout.readline(2_000_001)
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(message, dict):
                    continue
                if "id" in message and "method" in message:
                    self._handle_server_request(message)
                    continue
                with self._state:
                    if "id" in message:
                        result_queue = self._pending.get(message["id"])
                        if result_queue is not None and result_queue.empty():
                            result_queue.put_nowait(message)
                    else:
                        method = message.get("method")
                        params = message.get("params") or {}
                        self._observe_production_ancestry(method, params)
                        if method == "account/login/completed" and self._login and params.get("loginId") == self._login["loginId"]:
                            self._login = None
                            self._login_error = None if params.get("success") else "Codex sign-in was not completed. Try signing in again."
                        # Only retain turn events, never account/auth payloads.
                        if method == "serverRequest/resolved":
                            self._approvals.pop(str(params.get("requestId")), None)
                        if method in ("item/started", "item/completed"):
                            item = params.get("item") or {}
                            if item.get("type") in ("fileChange", "commandExecution") and item.get("id"):
                                self._tool_items[(params.get("threadId"), item["id"])] = {
                                    key: item[key] for key in ("command", "cwd", "changes") if key in item}
                                if len(self._tool_items) > 512:
                                    self._tool_items.pop(next(iter(self._tool_items)))
                        if method in ("item/started", "item/completed", "turn/completed", "error", "turn/plan/updated"):
                            self._sequence += 1
                            self._events.append((self._sequence, message))
                    self._state.notify_all()
        except (OSError, ValueError, CodexError):
            pass
        finally:
            with self._state:
                if self._proc is proc:
                    self._ready = False
                    for result_queue in self._pending.values():
                        if result_queue.empty():
                            result_queue.put_nowait({"error": {"code": -32000}})
                self._state.notify_all()

    def status(self) -> dict:
        available = self._find_command() is not None
        result = {"available": available, "connected": False, "auth_mode": None, "plan": None}
        if not available:
            result["error"] = "Install the Codex CLI to use ChatGPT sign-in."
            return result
        try:
            self._start()
            account = self._rpc("account/read", {"refreshToken": False}).get("account")
            if isinstance(account, dict):
                mode = account.get("type")
                known = {"chatgpt", "chatgptAuthTokens", "apiKey", "amazonBedrock", "agentIdentity", "personalAccessToken"}
                result["auth_mode"] = mode if mode in known else "other"
                result["connected"] = True
                plan = account.get("planType")
                if isinstance(plan, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", plan):
                    result["plan"] = plan
            with self._state:
                result["login_pending"] = self._login is not None
                if self._login_error:
                    result["error"] = self._login_error
        except CodexError as exc:
            result["error"] = str(exc)
        return result

    def login(self) -> dict:
        with self._login_lock:
            self._start()
            with self._state:
                if self._login:
                    return dict(self._login)
                self._login_error = None
            response = self._rpc("account/login/start", {"type": "chatgpt"})
            url, login_id = response.get("authUrl"), response.get("loginId")
            parsed = urlparse(url) if isinstance(url, str) else None
            host = (parsed.hostname or "").lower() if parsed else ""
            if (not parsed or parsed.scheme != "https" or parsed.username or parsed.password
                    or not (host == "chatgpt.com" or host.endswith(".chatgpt.com")
                            or host == "openai.com" or host.endswith(".openai.com"))
                    or not isinstance(login_id, str) or not login_id or len(login_id) > 200):
                raise CodexError("Codex returned an unexpected sign-in response. Update Codex and retry.")
            with self._state:
                self._login = {"authUrl": url, "loginId": login_id}
                return dict(self._login)

    def cancel_login(self, login_id):
        with self._login_lock:
            with self._state:
                if not self._login or login_id != self._login["loginId"]:
                    return {"cancelled": False}
            self._rpc("account/login/cancel", {"loginId": login_id})
            with self._state:
                self._login = None
            return {"cancelled": True}

    @staticmethod
    def _safe_text(value, limit=2000):
        """Bound UI/log payloads and redact common credential forms."""
        if not isinstance(value, str):
            return ""
        value = re.sub(r"(?i)\bBearer\s+[^\s\"']+", "Bearer [redacted]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[redacted]", value)
        value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted]", value)
        value = re.sub(r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|client[_-]?secret)[\"']?\s*[:=]\s*)[^\r\n,;]+", r"\1[redacted]", value)
        return value[:limit] + ("..." if len(value) > limit else "")

    def _emit(self, event):
        callback = self._production_callback
        if callback:
            try:
                callback(event)
            except Exception:
                # A disconnected browser or failing progress logger must not
                # kill the stdout reader or approve a pending operation.
                pass

    @staticmethod
    def _thread_parent(thread):
        if not isinstance(thread, dict):
            return None
        explicit = thread.get("parentThreadId")
        source = thread.get("source") or {}
        subagent = source.get("subAgent") if isinstance(source, dict) else None
        spawn = subagent.get("thread_spawn") if isinstance(subagent, dict) else None
        inherited = spawn.get("parent_thread_id") if isinstance(spawn, dict) else None
        if explicit and inherited and explicit != inherited:
            return None
        parent = explicit or inherited
        return parent if isinstance(parent, str) and parent else None

    def _is_production_thread(self, thread_id):
        with self._state:
            seen = set()
            while isinstance(thread_id, str) and thread_id not in seen:
                if thread_id == self._active_thread and self._active_thread is not None:
                    return True
                seen.add(thread_id)
                thread_id = self._production_parents.get(thread_id)
            return False

    def _observe_production_ancestry(self, method, params):
        """Only server-provided parent links or successful spawn calls add scope."""
        if self._active_thread is None:
            return
        if method == "thread/started":
            thread = params.get("thread") or {}
            parent = self._thread_parent(thread)
            child = thread.get("id")
            if parent and isinstance(child, str) and child != parent:
                self._production_parents[child] = parent
        elif method in ("item/started", "item/completed"):
            item = params.get("item") or {}
            sender = item.get("senderThreadId")
            if (item.get("type") == "collabAgentToolCall" and item.get("tool") == "spawnAgent"
                    and item.get("status") == "completed" and sender == params.get("threadId")
                    and self._is_production_thread(sender)):
                for child in item.get("receiverThreadIds", []):
                    if isinstance(child, str) and child != sender:
                        self._production_parents[child] = sender
        if len(self._production_parents) > 1024:
            # Bound metadata from unrelated server activity without extending
            # permissions. Verified active descendants retain their links.
            self._production_parents = {child: parent for child, parent in self._production_parents.items()
                                        if self._is_production_thread(child)}

    def _verify_production_descendant(self, thread_id, production_root):
        """Read thread metadata, never resume or message a thread to verify it."""
        links = {}
        current = thread_id
        for _ in range(12):
            with self._state:
                if self._active_thread != production_root:
                    return False
                if self._is_production_thread(current):
                    self._production_parents.update(links)
                    return True
            if not isinstance(current, str) or current in links:
                return False
            response = self._rpc("thread/read", {"threadId": current, "includeTurns": False}, timeout=5)
            thread = response.get("thread") or {}
            if thread.get("id") != current:
                return False
            parent = self._thread_parent(thread)
            if not parent or parent == current:
                return False
            links[current] = parent
            current = parent
        return False

    def _verify_and_route_approval(self, message, production_root, proc):
        key = str(message["id"])
        params = message.get("params") or {}
        request_thread = params.get("threadId", params.get("conversationId"))
        try:
            try:
                verified = self._verify_production_descendant(request_thread, production_root)
            except CodexError:
                verified = False
            with self._state:
                same_connection = self._proc is proc
                current_job = self._active_thread == production_root
            if not same_connection:
                return
            if verified and current_job:
                self._handle_server_request(message, verify_unknown=False)
            else:
                self._deny_server_request(message)
        except CodexError:
            pass
        finally:
            with self._state:
                self._verifying_approvals.discard(key)

    def _handle_server_request(self, message, verify_unknown=True):
        method = message.get("method")
        params = message.get("params") or {}
        supported = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval",
                     "item/permissions/requestApproval", "execCommandApproval", "applyPatchApproval"}
        request_thread = params.get("threadId", params.get("conversationId"))
        cancelled = self._production_cancel_event is not None and self._production_cancel_event.is_set()
        if (self._production_callback is not None and method in supported and self._active_thread
                and not cancelled and not self._is_production_thread(request_thread) and verify_unknown):
            key = str(message["id"])
            with self._state:
                if key in self._verifying_approvals:
                    return
                if len(self._verifying_approvals) < 16:
                    self._verifying_approvals.add(key)
                    verifier = threading.Thread(target=self._verify_and_route_approval,
                        args=(message, self._active_thread, self._proc), daemon=True,
                        name="studio-codex-ancestry")
                else:
                    verifier = None
            if verifier:
                verifier.start()
                return
        if (self._production_callback is not None and method in supported
                and not cancelled and self._is_production_thread(request_thread)):
            approval_id = str(message["id"])
            with self._state:
                item = dict(self._tool_items.get((request_thread, params.get("itemId", params.get("callId"))), {}))
            command = params.get("command") or item.get("command")
            if isinstance(command, list):
                command = subprocess.list2cmdline([str(part) for part in command])
            titles = {"item/commandExecution/requestApproval": "Allow this command?",
                      "execCommandApproval": "Allow this command?",
                      "item/fileChange/requestApproval": "Allow these file changes?",
                      "applyPatchApproval": "Allow these file changes?",
                      "item/permissions/requestApproval": "Allow these additional permissions?"}
            public = {"id": approval_id, "method": method, "title": titles[method],
                      "thread_id": request_thread, "is_subagent": request_thread != self._active_thread,
                      "reason": self._safe_text(params.get("reason") or params.get("justification") or "Codex needs your approval to continue."),
                      "scope": "this action", "details_truncated": False, "reviewable": True}
            def detail(key, value, limit=24000):
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                public[key] = self._safe_text(text, limit)
                if len(text) > limit:
                    public["details_truncated"] = True
                    public["reviewable"] = False
            if command:
                detail("command", command)
            cwd = params.get("cwd") or item.get("cwd")
            if cwd:
                detail("cwd", cwd, 2000)
            if params.get("networkApprovalContext"):
                detail("network_context", params["networkApprovalContext"])
            extra = params.get("additionalPermissions") or params.get("permissions")
            if extra is not None:
                detail("permissions", extra)
            if method == "item/permissions/requestApproval":
                public["scope"] = "this turn only"
                if not isinstance(params.get("permissions"), dict):
                    public["reviewable"] = False
            if method in ("item/commandExecution/requestApproval", "execCommandApproval") and not command and not params.get("networkApprovalContext"):
                public["reviewable"] = False
            if method in ("item/fileChange/requestApproval", "applyPatchApproval"):
                changes = params.get("changes") or params.get("fileChanges") or item.get("changes") or {}
                if isinstance(changes, dict):
                    paths = list(changes)
                elif isinstance(changes, list):
                    paths = [change.get("path", "") for change in changes if isinstance(change, dict)]
                else:
                    paths = []
                public["files"] = [self._safe_text(path, 2000) for path in paths[:100]]
                if changes:
                    detail("changes", changes)
                if not paths or len(paths) > 100 or any(len(path) > 2000 for path in paths):
                    public["reviewable"] = False
                if params.get("grantRoot"):
                    detail("grant_root", params["grantRoot"], 2000)
                    # The protocol describes grantRoot as a session-wide write
                    # grant. This studio approves individual actions only.
                    public["reviewable"] = False
                    public["scope"] = "session-wide root grant (not supported)"
            if not public["reviewable"]:
                public["reason"] += " This request cannot be fully reviewed as one action; decline it and ask Codex to use a smaller, action-scoped request."
            with self._state:
                self._approvals[approval_id] = {"message": message, "public": public}
                self._state.notify_all()
            self._emit({"type": "approval", **public})
            return
        if self._production_callback and method in ("item/tool/requestUserInput", "mcpServer/elicitation/request"):
            self._emit({"type": "message", "text": "Codex requested an interactive tool form. This studio cannot fill that form; the request was declined. You can stop and revise the prompt if needed."})
        self._deny_server_request(message)

    def pending_approvals(self):
        with self._state:
            return [dict(item["public"]) for item in self._approvals.values()]

    def resolve_approval(self, approval_id, approved: bool):
        if not isinstance(approved, bool):
            raise CodexError("Approval must be an explicit yes or no.")
        # Serialize responses but never hold the state lock during pipe I/O or
        # a caller callback. The job manager may call pending_approvals while
        # holding its own lock; callbacks must be free to acquire that lock.
        with self._approval_serial:
            with self._state:
                record = self._approvals.get(str(approval_id))
                if not record:
                    raise CodexError("This approval is no longer pending.")
                if approved and not record["public"].get("reviewable", True):
                    raise CodexError("This request cannot be fully reviewed as one action. Decline it and ask Codex for a smaller, action-scoped request.")
                message = record["message"]
            method = message["method"]
            if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                result = {"decision": "accept" if approved else "decline"}
            elif method in ("execCommandApproval", "applyPatchApproval"):
                result = {"decision": "approved" if approved else {"denied": {"rejection": "The user declined this action."}}}
            elif method == "item/permissions/requestApproval":
                result = {"permissions": message["params"].get("permissions", {}) if approved else {}, "scope": "turn"}
            else:
                raise CodexError("This request is not supported by the approval screen.")
            self._send({"id": message["id"], "result": result})
            with self._state:
                self._approvals.pop(str(approval_id), None)
        self._emit({"type": "stage", "stage": "working", "text": "Approval accepted." if approved else "Approval declined. Codex can choose another approach."})
        return {"resolved": True, "approved": approved}

    def _decline_remaining_approvals(self):
        for record in self.pending_approvals():
            try:
                self.resolve_approval(record["id"], False)
            except CodexError:
                pass

    def start_production(self, workspace: Path, prompt: str, on_event,
                         cancel_event, thread_id=None, approval_mode="manual", kind="video") -> dict:
        """Run a real Codex turn with local tools; output verification is the caller's job.

        The job thread is persisted by Codex and can be resumed after a stopped
        run. No completion message is treated as proof that a video was made.
        This call waits until completion, cancellation or transport failure;
        long renders do not have the short planner's 180-second timeout.
        """
        job_path = Path(workspace).resolve()
        if not job_path.is_dir() or not job_path.is_relative_to(self.workspace):
            raise CodexError("The production folder must be inside this studio's Downloads folder.")
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 100000:
            raise CodexError("Enter a production prompt shorter than 100,000 characters.")
        if not callable(on_event) or not hasattr(cancel_event, "is_set"):
            raise CodexError("The production job configuration is invalid.")
        if thread_id is not None and (not isinstance(thread_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", thread_id)):
            raise CodexError("Invalid Codex task identifier.")
        if approval_mode not in {"manual", "automatic"}:
            raise CodexError("Approval mode must be manual or automatic.")
        if kind not in {"video", "asset"}:
            raise CodexError("Production kind must be video or asset.")
        reviewer = "auto_review" if approval_mode == "automatic" else "user"
        if not self._generation_lock.acquire(blocking=False):
            raise CodexError("Codex is already producing a video. Wait for it to finish or cancel it first.")
        turn_id = None
        active_thread = None
        with self._state:
            self._production_parents.clear()
            self._production_cancel_event = cancel_event
        self._production_callback = on_event
        try:
            self._start()
            auth = self.status()
            if not auth.get("connected") or auth.get("auth_mode") not in ("chatgpt", "chatgptAuthTokens"):
                raise CodexError("Sign in with ChatGPT in Codex before starting production.")
            if cancel_event.is_set():
                return {"thread_id": thread_id, "status": "cancelled", "text": "Production cancelled before it started."}
            deliverable = ("a complete video with audio" if kind == "video" else
                           "a self-contained reusable 3D asset and preview")
            rules = (f"You are producing {deliverable} for Prompt Video Studio. "
                     "Use only free local software, free downloadable models, and media licensed for the requested use. "
                     "Do not use paid generation APIs, buy services, or publish/upload/send anything. "
                     "Use available configured tools, including computer control when available, to do real production work. "
                     "Create and verify the requested deliverables; do not stop after writing a script or plan. "
                     "Keep production outputs inside the current job folder and shared downloaded software/models "
                     "inside the studio tools or assets folders. Do not put project files in OneDrive. "
                     "Respect the configured sandbox and request approval for additional permissions; do not bypass denials. "
                     "Never display credentials or read credential files. Report missing tools or access honestly. "
                     "The user's requested duration may be up to 20 minutes; choose a feasible production style and work in scenes. "
                     "The app independently verifies the final file before showing the job as complete.")
            params = {"cwd": str(job_path), "approvalPolicy": "on-request", "approvalsReviewer": reviewer,
                      "sandbox": "workspace-write", "developerInstructions": rules}
            if thread_id:
                params.update({"threadId": thread_id, "excludeTurns": True})
                thread = self._rpc("thread/resume", params, timeout=40)
            else:
                params["serviceName"] = "prompt_video_studio"
                thread = self._rpc("thread/start", params, timeout=40)
            active_thread = thread.get("thread", {}).get("id")
            if not isinstance(active_thread, str):
                raise CodexError("Codex could not open the production task.")
            self._active_thread = active_thread
            self._emit({"type": "thread", "thread_id": active_thread})
            if cancel_event.is_set():
                return {"thread_id": active_thread, "status": "cancelled", "text": "Production cancelled before its model turn started."}
            with self._state:
                cursor = self._sequence
            self._emit({"type": "stage", "stage": "working", "text": f"Codex is creating the {kind} with available free tools."})
            turn = self._rpc("turn/start", {"threadId": active_thread,
                "input": [{"type": "text", "text": prompt}], "cwd": str(job_path),
                "approvalPolicy": "on-request", "approvalsReviewer": reviewer,
                "sandboxPolicy": {"type": "workspaceWrite", "networkAccess": False,
                    "writableRoots": [str(job_path), str(self.workspace / "tools"), str(self.workspace / "assets")]}
                }, timeout=40)
            turn_id = turn.get("turn", {}).get("id")
            if not isinstance(turn_id, str):
                raise CodexError("Codex could not start production.")
            final_text = ""
            interrupt_sent = False
            interrupt_deadline = None
            while True:
                if cancel_event.is_set() and not interrupt_sent:
                    self._decline_remaining_approvals()
                    self._rpc("turn/interrupt", {"threadId": active_thread, "turnId": turn_id}, timeout=5)
                    interrupt_sent = True
                    interrupt_deadline = time.monotonic() + 15
                    self._emit({"type": "stage", "stage": "cancelling", "text": "Stopping this production turn."})
                if interrupt_deadline and time.monotonic() > interrupt_deadline:
                    self._stop_process()
                    return {"thread_id": active_thread, "status": "cancelled", "text": "Production cancelled."}
                with self._state:
                    events = [(seq, event) for seq, event in self._events if seq > cursor]
                    if not events:
                        if not self._ready:
                            raise CodexError("Codex disconnected during production. Resume this job to continue from its saved task.")
                        self._state.wait(.5)
                        continue
                for seq, event in events:
                    cursor = seq
                    info = event.get("params") or {}
                    if info.get("threadId") != active_thread:
                        continue
                    method = event.get("method")
                    if method in ("item/started", "item/completed") and info.get("turnId") == turn_id:
                        item = info.get("item") or {}
                        kind = item.get("type")
                        if kind == "agentMessage" and method == "item/completed":
                            text = self._safe_text(item.get("text"), 12000)
                            if text:
                                final_text = text
                                self._emit({"type": "message", "text": text})
                        elif kind not in ("agentMessage", "reasoning", "userMessage"):
                            state = "started" if method == "item/started" else "completed"
                            entry = {"type": "tool", "name": self._safe_text(item.get("tool") or kind or "tool", 100),
                                     "status": self._safe_text(item.get("status") or state, 80)}
                            if item.get("command"):
                                command = item["command"]
                                entry["command"] = self._safe_text(command if isinstance(command, str) else str(command), 1500)
                            self._emit(entry)
                    elif method == "turn/plan/updated":
                        self._emit({"type": "stage", "stage": "working", "text": self._safe_text(info.get("explanation") or "Production plan updated.", 1500)})
                    elif method == "error":
                        self._emit({"type": "stage", "stage": "working", "text": "Codex reported a production issue and may retry."})
                    elif method == "turn/completed":
                        completed = info.get("turn") or {}
                        if completed.get("id") != turn_id:
                            continue
                        for item in completed.get("items", []):
                            if item.get("type") == "agentMessage" and item.get("text"):
                                final_text = self._safe_text(item["text"], 12000)
                        state = completed.get("status")
                        if interrupt_sent or state == "interrupted":
                            status = "cancelled"
                        elif state == "completed":
                            status = "completed"
                        else:
                            status = "failed"
                            error = completed.get("error") or {}
                            final_text = self._safe_text(error.get("message"), 1500) or final_text or "Codex did not complete this production turn. Check account access and usage limits."
                        return {"thread_id": active_thread, "status": status, "text": final_text}
        except Exception:
            if active_thread and turn_id:
                try:
                    self._rpc("turn/interrupt", {"threadId": active_thread, "turnId": turn_id}, timeout=5)
                except CodexError:
                    self._stop_process()
            elif active_thread:
                self._stop_process()
            raise
        finally:
            self._decline_remaining_approvals()
            with self._state:
                self._active_thread = None
                self._production_parents.clear()
                self._production_cancel_event = None
            self._production_callback = None
            self._generation_lock.release()

    def generate_plan(self, prompt: str, schema: dict, instructions: str, timeout=180) -> dict:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 30000:
            raise CodexError("Enter a video prompt shorter than 30,000 characters.")
        if not isinstance(schema, dict) or not isinstance(instructions, str):
            raise CodexError("The video planner configuration is invalid.")
        if not self._generation_lock.acquire(blocking=False):
            raise CodexError("Codex is already planning a video. Wait for that request to finish.")
        thread_id = turn_id = None
        try:
            self._start()
            auth = self.status()
            if not auth.get("connected"):
                raise CodexError("Sign in to Codex before creating the video.")
            if auth.get("auth_mode") not in ("chatgpt", "chatgptAuthTokens"):
                raise CodexError("Sign in with ChatGPT in Codex to use this studio.")
            deadline = time.monotonic() + max(1, float(timeout))
            guard = ("You are the JSON story planner for a local video application. "
                     "Only produce the requested structured video plan from the supplied prompt. "
                     "Do not call tools, run commands, inspect files, browse, modify files, "
                     "install anything, or ask for permissions. The application renders the plan.\n\n")
            thread = self._rpc("thread/start", {"cwd": str(self.workspace), "ephemeral": True,
                "approvalPolicy": "never", "sandbox": "read-only",
                "baseInstructions": guard, "developerInstructions": instructions,
                "serviceName": "prompt_video_studio"}, timeout=min(30, max(0.01, deadline-time.monotonic())))
            thread_id = thread.get("thread", {}).get("id")
            if not isinstance(thread_id, str):
                raise CodexError("Codex could not start the video planner.")
            with self._state:
                cursor = self._sequence
            turn = self._rpc("turn/start", {"threadId": thread_id,
                "input": [{"type": "text", "text": prompt}], "approvalPolicy": "never",
                "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                "outputSchema": schema}, timeout=min(30, max(0.01, deadline-time.monotonic())))
            turn_id = turn.get("turn", {}).get("id")
            if not isinstance(turn_id, str):
                raise CodexError("Codex could not start the video plan.")
            messages = {}
            final_text = None
            while True:
                with self._state:
                    events = [(seq, event) for seq, event in self._events if seq > cursor]
                    if not events:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise CodexError("Codex planning timed out. Try a shorter prompt.")
                        if not self._ready:
                            raise CodexError("Codex disconnected while planning. Try again.")
                        self._state.wait(min(remaining, 1))
                        continue
                for seq, event in events:
                    cursor = seq
                    params = event.get("params") or {}
                    if params.get("threadId") != thread_id:
                        continue
                    if event.get("method") == "item/completed" and params.get("turnId") == turn_id:
                        item = params.get("item") or {}
                        if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                            messages[item.get("id", str(seq))] = item["text"]
                            if item.get("phase") == "final_answer":
                                final_text = item["text"]
                    elif event.get("method") == "turn/completed":
                        completed = params.get("turn") or {}
                        if completed.get("id") != turn_id:
                            continue
                        if completed.get("status") != "completed":
                            raise CodexError("Codex did not complete the plan. Check your Codex usage limits and try again.")
                        for item in completed.get("items", []):
                            if item.get("type") == "agentMessage" and isinstance(item.get("text"), str):
                                messages[item.get("id", str(len(messages)))] = item["text"]
                                if item.get("phase") == "final_answer":
                                    final_text = item["text"]
                        text = final_text or (next(reversed(messages.values())) if messages else "")
                        try:
                            plan = json.loads(text)
                        except (ValueError, TypeError):
                            raise CodexError("Codex returned an incomplete video plan. Try again.") from None
                        if not isinstance(plan, dict):
                            raise CodexError("Codex returned an invalid video plan. Try again.")
                        return plan
                if time.monotonic() >= deadline:
                    raise CodexError("Codex planning timed out. Try a shorter prompt.")
        except Exception:
            if thread_id and turn_id:
                try:
                    self._rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
                except CodexError:
                    self._stop_process()
            elif thread_id:
                # A timed-out turn/start might still have begun generation.
                self._stop_process()
            raise
        finally:
            if thread_id and self._ready:
                try:
                    self._rpc("thread/unsubscribe", {"threadId": thread_id}, timeout=5)
                except CodexError:
                    pass
            self._generation_lock.release()

    def _stop_process(self):
        with self._lifecycle:
            proc = self._proc
            self._ready = False
            if proc is None:
                return
            try:
                if proc.stdin:
                    proc.stdin.close()
                proc.wait(timeout=2)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            if proc.stdout:
                proc.stdout.close()
            self._proc = None
            with self._state:
                self._approvals.clear()
                self._login = None
                self._state.notify_all()

    def close(self):
        self._closed = True
        self._stop_process()
