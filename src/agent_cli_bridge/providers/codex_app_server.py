"""Fail-closed Codex app-server session transport.

This provider exposes the reusable session core proven in a larger deployment:

* seed a persisted Codex thread with ``thread/start``;
* fork an existing parent with ``thread/fork``;
* run exactly one user turn on the candidate child;
* fork a clean sibling and inject one plain assistant history item without a
  model turn;
* translate app-server notifications into the ``codex exec --json`` event
  shape consumed by :class:`CodexExecParser`;
* never mutate the parent thread during a foreground turn.
"""
from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .codex_app_server_errors import (
    LOW_RISK_NATIVE_ITEM_TYPES,
    native_item_tool_event,
)


DISABLED_NATIVE_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode",
    "code_mode_host",
    "computer_use",
    "deferred_executor",
    "enable_fanout",
    "enable_mcp_apps",
    "multi_agent",
    "multi_agent_v2",
    "plugins",
    "skill_mcp_dependency_install",
    "shell_tool",
    "tool_suggest",
    "unified_exec",
    "workspace_dependencies",
)
APP_SERVER_COMMAND = (
    "codex",
    "app-server",
    "--stdio",
    "--strict-config",
    "-c",
    'web_search="live"',
    "-c",
    "mcp_servers={}",
    *(
        argument
        for feature in DISABLED_NATIVE_FEATURES
        for argument in ("--disable", feature)
    ),
)
MAX_SYSTEM_CHARS = 500_000
MAX_IMAGES = 12
MODEL_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_GENERATED_PATH_RE = re.compile(
    r"/[^\s\"'<>]*/\.codex/generated_images/[^\s\"'<>]+"
)

# Stable enum values documented by the v2 app-server protocol.  Free-form
# provider messages and additionalDetails must never cross the bridge.
_CODEX_ERROR_INFO_KINDS = frozenset({
    "contextWindowExceeded",
    "sessionBudgetExceeded",
    "usageLimitExceeded",
    "serverOverloaded",
    "cyberPolicy",
    "misalignmentPolicyViolation",
    "internalServerError",
    "unauthorized",
    "badRequest",
    "threadRollbackFailed",
    "sandboxError",
    "other",
    "httpConnectionFailed",
    "responseStreamConnectionFailed",
    "responseStreamDisconnected",
    "responseTooManyFailedAttempts",
    "activeTurnNotSteerable",
})


def _safe_codex_error_kind(error) -> str | None:
    """Extract only a documented enum label from a provider error object."""
    if not isinstance(error, dict):
        return None
    pending = [error.get("codexErrorInfo")]
    seen = 0
    while pending and seen < 16:
        value = pending.pop()
        seen += 1
        if isinstance(value, str):
            if value in _CODEX_ERROR_INFO_KINDS:
                return value
            continue
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in _CODEX_ERROR_INFO_KINDS:
                    return key
                if key in {"type", "kind", "code"}:
                    pending.append(nested)
    return None


class CodexAppServerError(RuntimeError):
    """Base error for the Codex app-server session adapter."""


class CodexAppServerProtocolError(CodexAppServerError):
    """The app-server returned a malformed or unexpected protocol message."""


class CodexAppServerParentUnavailable(CodexAppServerProtocolError):
    """A requested fork parent is no longer present in the app-server store."""


class CodexAppServerTurnError(CodexAppServerError):
    """The candidate Codex turn did not complete successfully."""

    def __init__(self, message: str, *, error_code: str = "app_server_turn_failed", tool_event=None):
        super().__init__(message)
        self.error_code = error_code
        self.tool_event = tool_event


def _uuid(value: str, *, field: str) -> str:
    try:
        return str(uuid.UUID(str(value or "")))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid {field}") from exc


def _absolute_file(value: str, *, field: str) -> str:
    path = Path(str(value or ""))
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"invalid {field}")
    return str(path)


def _absolute_dir(value: str, *, field: str) -> str:
    path = Path(str(value or ""))
    if not path.is_absolute() or not path.is_dir():
        raise ValueError(f"invalid {field}")
    return str(path)


@dataclass(frozen=True)
class CodexSessionSpec:
    mode: str
    cwd: str
    system_file: str
    parent_thread_id: str | None = None
    model: str | None = None
    image_paths: tuple[str, ...] = ()
    paid_image_generation_enabled: bool = False
    web_search_enabled: bool = True

    def __post_init__(self) -> None:
        if self.mode not in {"seed", "fork"}:
            raise ValueError("invalid Codex session mode")
        object.__setattr__(self, "cwd", _absolute_dir(self.cwd, field="cwd"))
        object.__setattr__(
            self,
            "system_file",
            _absolute_file(self.system_file, field="system file"),
        )
        if self.mode == "fork":
            object.__setattr__(
                self,
                "parent_thread_id",
                _uuid(self.parent_thread_id or "", field="parent thread id"),
            )
        elif self.parent_thread_id is not None:
            raise ValueError("seed mode cannot include a parent thread id")
        if self.model is not None and not MODEL_RE.fullmatch(self.model):
            raise ValueError("invalid model identifier")
        if len(self.image_paths) > MAX_IMAGES:
            raise ValueError("too many Codex session images")
        object.__setattr__(
            self,
            "image_paths",
            tuple(
                _absolute_file(path, field="image path")
                for path in self.image_paths
            ),
        )
        if not isinstance(self.web_search_enabled, bool):
            raise ValueError("invalid web search eligibility")

    def system_text(self) -> str:
        text = Path(self.system_file).read_text(encoding="utf-8")
        if len(text) > MAX_SYSTEM_CHARS:
            raise ValueError("Codex system prompt is too large")
        return text


class SubprocessJsonRpcTransport:
    """Line-delimited JSON transport for ``codex app-server --stdio``."""

    def __init__(
        self,
        command: Iterable[str] = APP_SERVER_COMMAND,
        *,
        cwd: str,
        env: dict[str, str] | None = None,
    ) -> None:
        self._stdout_queue: queue.Queue[object] = queue.Queue()
        self._write_lock = threading.Lock()
        try:
            self._proc = subprocess.Popen(
                list(command),
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            raise CodexAppServerError("failed to start Codex app-server") from exc
        self._stdout_thread = threading.Thread(
            target=self._read_stdout, daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr, daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._stdout_queue.put(json.loads(line))
            except json.JSONDecodeError:
                self._stdout_queue.put(
                    CodexAppServerProtocolError(
                        "Codex app-server returned malformed JSON"
                    )
                )
        self._stdout_queue.put(None)

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        # Drain stderr to prevent the child from blocking. Do not retain or
        # forward it: app-server diagnostics may contain local paths or provider
        # details that should not be exposed on the public response surface.
        for _line in self._proc.stderr:
            pass

    def send(self, message: dict) -> None:
        if self._proc.poll() is not None:
            raise CodexAppServerError("Codex app-server exited unexpectedly")
        assert self._proc.stdin is not None
        encoded = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        )
        with self._write_lock:
            self._proc.stdin.write(encoded + "\n")
            self._proc.stdin.flush()

    def receive(self, timeout: float) -> dict:
        try:
            item = self._stdout_queue.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("Codex app-server response timeout") from exc
        if isinstance(item, Exception):
            raise item
        if item is None:
            raise CodexAppServerError("Codex app-server closed its output")
        if not isinstance(item, dict):
            raise CodexAppServerProtocolError(
                "Codex app-server returned a non-object message"
            )
        return item

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._proc.wait(timeout=3)
        for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass


_PASSIVE_ITEM_TYPES = {
    "userMessage",
    "hookPrompt",
    "enteredReviewMode",
    "exitedReviewMode",
    "contextCompaction",
}


_TOOL_ITEM_MAP = {
    "commandExecution": "command_execution",
    "fileChange": "file_change",
    "mcpToolCall": "mcp_tool_call",
    "dynamicToolCall": "dynamic_tool_call",
    "collabAgentToolCall": "collab_agent_tool_call",
    "subAgentActivity": "sub_agent_activity",
    "webSearch": "web_search",
    "imageView": "image_view",
    "imageGeneration": "image_generation",
    "sleep": "sleep",
}


class CodexAppServerEventTranslator:
    """Translate v2 app-server notifications to ``codex exec --json`` events."""

    def __init__(self, *, thread_id: str, turn_id: str) -> None:
        self.thread_id = _uuid(thread_id, field="thread id")
        self.turn_id = _uuid(turn_id, field="turn id")
        self.usage: dict = {}
        self.saw_agent_message = False
        self.saw_accepted_artifact = False
        self.image_generation_item_id: str | None = None
        self.native_tool_attempted = False
        self.native_tool_events: list[dict] = []
        self.completed = False
        self.terminal_status: str | None = None
        self.terminal_error: str | None = None

    def _matches(self, params: dict) -> bool:
        return (
            str(params.get("threadId") or "") == self.thread_id
            and str(params.get("turnId") or "") == self.turn_id
        )

    def translate(self, message: dict) -> list[dict]:
        if "id" in message and "method" in message and "result" not in message:
            raise CodexAppServerProtocolError(
                "Codex app-server requested an interactive action"
            )
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            raise CodexAppServerProtocolError(
                "Codex app-server notification params are invalid"
            )

        if method == "thread/tokenUsage/updated":
            if not self._matches(params):
                return []
            token_usage = params.get("tokenUsage") or {}
            last = token_usage.get("last") or token_usage.get("total") or {}
            self.usage = {
                "input_tokens": int(last.get("inputTokens") or 0),
                "cached_input_tokens": int(last.get("cachedInputTokens") or 0),
                "output_tokens": int(last.get("outputTokens") or 0),
                "reasoning_output_tokens": int(
                    last.get("reasoningOutputTokens") or 0
                ),
            }
            return []

        if method == "item/agentMessage/delta":
            if not self._matches(params) or self.native_tool_attempted:
                return []
            delta = str(params.get("delta") or "")
            if not delta:
                return []
            item_id = str(params.get("itemId") or "")
            return [{
                "type": "item.agent_message.delta",
                "item_id": item_id,
                "delta": _GENERATED_PATH_RE.sub("[generated image]", delta),
            }]

        if method == "item/started":
            if not self._matches(params):
                return []
            item = params.get("item") or {}
            if not isinstance(item, dict):
                raise CodexAppServerProtocolError(
                    "Codex started item is invalid"
                )
            if str(item.get("type") or "") != "imageGeneration":
                return []
            item_id = str(item.get("id") or "")
            if (
                self.saw_accepted_artifact
                or (
                    self.image_generation_item_id is not None
                    and self.image_generation_item_id != item_id
                )
            ):
                raise CodexAppServerProtocolError(
                    "Codex attempted more than one image generation"
                )
            self.image_generation_item_id = item_id
            return []

        if method == "item/completed":
            if not self._matches(params):
                return []
            item = params.get("item") or {}
            if not isinstance(item, dict):
                raise CodexAppServerProtocolError(
                    "Codex completed item is invalid"
                )
            item_type = str(item.get("type") or "")
            item_id = str(item.get("id") or "")
            if (
                self.native_tool_attempted
                and item_type in {"agentMessage", "reasoning", "plan"}
            ):
                # Never forward model output produced after a blocked capability.
                return []
            if item_type in _PASSIVE_ITEM_TYPES:
                return []
            if item_type == "agentMessage":
                text = _GENERATED_PATH_RE.sub(
                    "[generated image]", str(item.get("text") or "")
                )
                if not text:
                    return []
                self.saw_agent_message = True
                return [{
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": "agent_message",
                        "text": text,
                    },
                }]
            if item_type == "reasoning":
                parts = item.get("summary") or item.get("content") or []
                if isinstance(parts, str):
                    text = parts
                elif isinstance(parts, list):
                    text = "\n".join(str(part) for part in parts if part)
                else:
                    text = ""
                if not text:
                    return []
                return [{
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": "reasoning",
                        "summary": text,
                    },
                }]
            if item_type == "plan":
                text = str(item.get("text") or "")
                if not text:
                    return []
                return [{
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": "reasoning",
                        "summary": text,
                    },
                }]
            native_type = _TOOL_ITEM_MAP.get(item_type)
            if self.native_tool_attempted:
                return []
            if native_type == "image_generation":
                if (
                    self.saw_accepted_artifact
                    or (
                        self.image_generation_item_id is not None
                        and self.image_generation_item_id != item_id
                    )
                ):
                    raise CodexAppServerProtocolError(
                        "Codex attempted more than one image generation"
                    )
                self.image_generation_item_id = item_id
                saved_path = str(item.get("savedPath") or "")
                status = str(item.get("status") or "")
                if status != "completed" or not saved_path:
                    raise CodexAppServerProtocolError(
                        "Codex image generation result is incomplete"
                    )
                self.saw_accepted_artifact = True
                return [{
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": native_type,
                        "status": "completed",
                        # Consumed and replaced by the bridge before this event
                        # crosses the provider boundary.
                        "saved_path": saved_path,
                    },
                }]
            if native_type in LOW_RISK_NATIVE_ITEM_TYPES:
                tool_event = native_item_tool_event(
                    native_type,
                    item_id=item_id,
                    phase="app_server",
                    status="completed",
                )
                return [{
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": native_type,
                        "status": "completed",
                        "tool_event": tool_event,
                    },
                }]
            if native_type:
                self.native_tool_attempted = True
                tool_event = native_item_tool_event(
                    native_type, item_id=item_id, phase="app_server",
                )
                self.native_tool_events.append(tool_event)
                self.native_tool_events = self.native_tool_events[-32:]
                return [{
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": native_type,
                        "status": "blocked",
                        "tool_event": tool_event,
                    },
                }]
            if item_type:
                # Fail closed on every app-server item type that has not been
                # explicitly classified as passive output or low-risk above.
                # This covers newly added native capabilities without
                # depending on a version-specific schema allowlist.
                self.native_tool_attempted = True
                tool_event = native_item_tool_event(
                    item_type, item_id=item_id, phase="app_server_unknown",
                )
                self.native_tool_events.append(tool_event)
                self.native_tool_events = self.native_tool_events[-32:]
                return [{
                    "type": "item.completed",
                    "item": {
                        "id": item_id,
                        "type": "native_tool",
                        "native_item_type": item_type,
                        "status": "blocked",
                        "tool_event": tool_event,
                    },
                }]
            raise CodexAppServerProtocolError(
                "Codex completed item type is missing"
            )

        if method == "turn/completed":
            thread_id = str(params.get("threadId") or "")
            turn = params.get("turn") or {}
            if thread_id != self.thread_id or str(turn.get("id") or "") != self.turn_id:
                return []
            status = str(turn.get("status") or "")
            self.completed = True
            self.terminal_status = status
            if status != "completed":
                # Keep the worker event surface stable. Only a documented enum
                # label may be retained; raw provider messages/details are not
                # forwarded to callers or persisted in chat logs.
                error_kind = _safe_codex_error_kind(turn.get("error"))
                self.terminal_error = (
                    "Codex app-server turn status: "
                    + (status or "unknown")
                    + (f" [{error_kind}]" if error_kind else "")
                )
                return [{
                    "type": "turn.failed",
                    "message": self.terminal_error,
                }]
            return [{"type": "turn.completed", "usage": dict(self.usage)}]
        return []


def _response_result(
    message: dict,
    request_id: int,
    *,
    expected_fork_parent: str | None = None,
) -> dict | None:
    if message.get("id") != request_id:
        return None
    error = message.get("error")
    if error is not None:
        # Codex app-server currently reports an unavailable persisted parent as
        # JSON-RPC -32600 with exactly "no rollout found for thread id <uuid>".
        # Recognize only the exact requested UUID; every other RPC failure stays
        # a generic protocol error so callers never reseed on an ambiguous fault.
        if expected_fork_parent is not None and isinstance(error, dict):
            code = error.get("code")
            message_text = error.get("message")
            expected_text = f"no rollout found for thread id {expected_fork_parent}"
            if code == -32600 and message_text == expected_text:
                raise CodexAppServerParentUnavailable(
                    "Codex fork parent is unavailable"
                )
        raise CodexAppServerProtocolError(
            "Codex app-server request failed"
        )
    result = message.get("result")
    if not isinstance(result, dict):
        raise CodexAppServerProtocolError(
            "Codex app-server response result is invalid"
        )
    return result


def _remaining(deadline: float, *, message: str) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError(message)
    return remaining


def _receive_response(
    transport,
    request_id: int,
    *,
    timeout: float,
    buffered_notifications: list[dict] | None = None,
    expected_fork_parent: str | None = None,
) -> dict:
    deadline = time.monotonic() + float(timeout)
    while True:
        message = transport.receive(_remaining(
            deadline, message="Codex app-server response timeout"
        ))
        result = _response_result(
            message, request_id, expected_fork_parent=expected_fork_parent
        )
        if result is not None:
            return result
        if "id" in message and "method" in message and "result" not in message:
            raise CodexAppServerProtocolError(
                "Codex app-server requested an interactive action"
            )
        if buffered_notifications is not None and message.get("method"):
            buffered_notifications.append(message)


def _thread_from_result(
    result: dict, *, expected_parent: str | None
) -> tuple[str, str]:
    thread = result.get("thread") or {}
    if not isinstance(thread, dict):
        raise CodexAppServerProtocolError("Codex thread response is invalid")
    thread_id = _uuid(thread.get("id") or "", field="returned thread id")
    if thread.get("ephemeral") is not False:
        raise CodexAppServerProtocolError(
            "Codex app-server returned an ephemeral thread"
        )
    if expected_parent is not None:
        forked_from = _uuid(
            thread.get("forkedFromId") or "", field="fork parent id"
        )
        if forked_from != expected_parent or thread_id == expected_parent:
            raise CodexAppServerProtocolError(
                "Codex fork did not return an isolated child thread"
            )
    actual_model = str(result.get("model") or "").strip()
    if not actual_model or not MODEL_RE.fullmatch(actual_model):
        raise CodexAppServerProtocolError(
            "Codex app-server returned an invalid model identity"
        )
    return thread_id, actual_model


def commit_assistant_history(
    spec: CodexSessionSpec,
    assistant_text: str,
    *,
    emit: Callable[[dict], None],
    transport_factory: Callable[..., object] = SubprocessJsonRpcTransport,
    env: dict[str, str] | None = None,
    response_timeout: float = 30,
) -> str:
    """Fork one clean sibling and append only a plain assistant history item.

    This operation performs no model turn.  It is used to commit a model result
    that was produced on a disposable decision branch without carrying the
    decision branch's transient user control into the promoted lineage.
    """
    if spec.mode != "fork" or not spec.parent_thread_id:
        raise ValueError("assistant history commit requires a fork parent")
    if not isinstance(assistant_text, str) or not assistant_text.strip():
        raise ValueError("assistant history commit requires text")
    if len(assistant_text) > 20_000:
        raise ValueError("assistant history commit text is too large")
    if spec.image_paths:
        raise ValueError("assistant history commit cannot include images")
    system_text = spec.system_text()
    transport = transport_factory(cwd=spec.cwd, env=env)
    try:
        transport.send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "agent-cli-bridge",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": True},
            },
        })
        _receive_response(transport, 1, timeout=response_timeout)
        transport.send({"method": "initialized", "params": {}})

        common = {
            "cwd": spec.cwd,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": False,
            "baseInstructions": system_text,
            "developerInstructions": "",
            "config": {
                "web_search": "disabled",
                "features": {"image_generation": False},
            },
        }
        if spec.model:
            common["model"] = spec.model
        transport.send({
            "id": 2,
            "method": "thread/fork",
            "params": {
                **common,
                "threadId": spec.parent_thread_id,
            },
        })
        result = _receive_response(
            transport,
            2,
            timeout=response_timeout,
            expected_fork_parent=spec.parent_thread_id,
        )
        child_thread_id, actual_model = _thread_from_result(
            result, expected_parent=spec.parent_thread_id
        )
        transport.send({
            "id": 3,
            "method": "thread/inject_items",
            "params": {
                "threadId": child_thread_id,
                "items": [{
                    "type": "message",
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": assistant_text,
                    }],
                }],
            },
        })
        _receive_response(transport, 3, timeout=response_timeout)
        emit({
            "type": "thread.started",
            "thread_id": child_thread_id,
            "model": actual_model,
            "model_identity_source": "codex_app_server_history_commit",
        })
        emit({
            "type": "history.committed",
            "thread_id": child_thread_id,
        })
        return child_thread_id
    finally:
        try:
            transport.close()
        except Exception:
            pass


def run_session_turn(
    spec: CodexSessionSpec,
    prompt: str,
    *,
    emit: Callable[[dict], None],
    transport_factory: Callable[..., object] = SubprocessJsonRpcTransport,
    env: dict[str, str] | None = None,
    response_timeout: float = 30,
    turn_timeout: float = 3600,
) -> str:
    """Run one seed/fork candidate turn and return its child thread id.

    The returned thread id is a candidate only. The caller should promote it atomically
    after the surrounding response has been fully persisted.
    """
    if not isinstance(prompt, str):
        raise ValueError("Codex prompt must be text")
    if not prompt and not spec.image_paths:
        raise ValueError("Codex turn requires text or an image")
    system_text = spec.system_text()
    transport = transport_factory(cwd=spec.cwd, env=env)
    try:
        transport.send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {
                    "name": "agent-cli-bridge",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": True},
            },
        })
        _receive_response(transport, 1, timeout=response_timeout)
        transport.send({"method": "initialized", "params": {}})

        common = {
            "cwd": spec.cwd,
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": False,
            "baseInstructions": system_text,
            "developerInstructions": "",
            "config": {
                "web_search": "live" if spec.web_search_enabled else "disabled",
                "features": {
                    "image_generation": bool(
                        spec.paid_image_generation_enabled
                    ),
                },
            },
        }
        if spec.model:
            common["model"] = spec.model
        if spec.mode == "seed":
            seed_params = {
                **common,
                "dynamicTools": [],
                "selectedCapabilityRoots": [],
            }
            transport.send({
                "id": 2,
                "method": "thread/start",
                "params": seed_params,
            })
            result = _receive_response(
                transport, 2, timeout=response_timeout
            )
            child_thread_id, actual_model = _thread_from_result(
                result, expected_parent=None
            )
        else:
            assert spec.parent_thread_id is not None
            transport.send({
                "id": 2,
                "method": "thread/fork",
                "params": {
                    **common,
                    "threadId": spec.parent_thread_id,
                },
            })
            result = _receive_response(
                transport,
                2,
                timeout=response_timeout,
                expected_fork_parent=spec.parent_thread_id,
            )
            child_thread_id, actual_model = _thread_from_result(
                result, expected_parent=spec.parent_thread_id
            )

        emit({
            "type": "thread.started",
            "thread_id": child_thread_id,
            "model": actual_model,
            "model_identity_source": "codex_app_server_thread_start",
        })
        user_input = []
        if prompt:
            user_input.append({"type": "text", "text": prompt})
        user_input.extend(
            {"type": "localImage", "path": path}
            for path in spec.image_paths
        )
        transport.send({
            "id": 3,
            "method": "turn/start",
            "params": {
                "threadId": child_thread_id,
                "input": user_input,
            },
        })
        early_notifications: list[dict] = []
        turn_result = _receive_response(
            transport,
            3,
            timeout=response_timeout,
            buffered_notifications=early_notifications,
        )
        turn = turn_result.get("turn") or {}
        turn_id = _uuid(turn.get("id") or "", field="returned turn id")
        translator = CodexAppServerEventTranslator(
            thread_id=child_thread_id,
            turn_id=turn_id,
        )
        for message in early_notifications:
            for event in translator.translate(message):
                emit(event)
        turn_deadline = time.monotonic() + float(turn_timeout)
        while not translator.completed:
            message = transport.receive(_remaining(
                turn_deadline, message="Codex app-server turn timeout"
            ))
            for event in translator.translate(message):
                emit(event)
        if translator.terminal_status != "completed":
            raise CodexAppServerTurnError(
                translator.terminal_error
                or "Codex app-server turn did not complete successfully"
            )
        if translator.native_tool_attempted:
            raise CodexAppServerTurnError(
                "Codex app-server attempted a blocked native item",
                error_code="native_item_blocked",
                tool_event=(
                    translator.native_tool_events[-1]
                    if translator.native_tool_events else None
                ),
            )
        if not translator.saw_agent_message and not translator.saw_accepted_artifact:
            raise CodexAppServerTurnError(
                "Codex app-server completed without an assistant message"
            )
        return child_thread_id
    finally:
        transport.close()
