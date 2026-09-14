"""Closed, bounded error taxonomy for the Codex app-server provider.

This module deliberately carries no provider payloads, prompts, local paths,
credentials, or artifact bytes.  It is safe to project into application history or across a bridge.
"""
from __future__ import annotations

import re

ERROR_RETRYABLE = {
    "remote_transport_failed": True,
    "app_server_turn_failed": True,
    "native_item_blocked": False,
    "artifact_transfer_failed": True,
    "sandbox_denied": False,
    "worker_protocol_error": False,
    "parent_session_unavailable": False,
}
ERROR_CODES = frozenset(ERROR_RETRYABLE)
LOW_RISK_NATIVE_ITEM_TYPES = frozenset({
    "image_view",
    "web_search",
    "sleep",
})
_SAFE_LABEL_RE = re.compile(r"[^A-Za-z0-9._:-]+")


def error_retryable(code: str) -> bool:
    return bool(ERROR_RETRYABLE.get(str(code or ""), False))


def normalize_error_code(code: str, *, default: str = "worker_protocol_error") -> str:
    value = str(code or "").strip()
    if value in ERROR_CODES:
        return value
    return default if default in ERROR_CODES else "worker_protocol_error"


def bounded_label(value, *, fallback: str = "unknown", limit: int = 64) -> str:
    """Normalize enum-like labels only; this is not a free-form text redactor."""
    text = _SAFE_LABEL_RE.sub("_", str(value or "").strip()).strip("_")
    if not text:
        text = fallback
    return text[: max(1, int(limit))]


def native_item_tool_event(
    item_type, *, item_id=None, phase: str = "codex", status: str = "blocked"
) -> dict:
    status = str(status or "blocked")
    if status not in {"blocked", "completed", "artifact_transfer_failed"}:
        status = "blocked"
    event = {
        "type": "native_item",
        "status": status,
        "item_type": bounded_label(item_type, fallback="native_tool"),
        "phase": bounded_label(phase, fallback="codex", limit=32),
    }
    if status == "blocked":
        event.update(error_code="native_item_blocked", retryable=False)
    elif status == "artifact_transfer_failed":
        event.update(error_code="artifact_transfer_failed", retryable=True)
    if item_id:
        event["item_id"] = bounded_label(item_id, fallback="item", limit=80)
    return event


def bounded_tool_event(value) -> dict | None:
    """Return the small allow-listed native diagnostic shape or ``None``."""
    if not isinstance(value, dict):
        return None
    if value.get("type") != "native_item":
        return None
    return native_item_tool_event(
        value.get("item_type") or "native_tool",
        item_id=value.get("item_id"),
        phase=value.get("phase") or "codex",
        status=value.get("status") or "blocked",
    )


def error_details(code: str, *, tool_event=None) -> dict:
    normalized = normalize_error_code(code)
    details = {
        "error_code": normalized,
        "retryable": error_retryable(normalized),
    }
    event = bounded_tool_event(tool_event)
    if event is not None:
        details["tool_event"] = event
    return details
