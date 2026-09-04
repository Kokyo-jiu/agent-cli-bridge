"""Codex CLI ``exec --json`` event adapter.

The parser targets Codex's public JSONL event stream and translates it into
agent-cli-bridge RuntimeEvents. It treats provider-exposed ``reasoning`` items
as reasoning summaries/status text; it does not attempt to expose hidden chain
of thought.
"""

from __future__ import annotations

from typing import Any

from ..core import RuntimeEvent


class CodexExecParser:
    """Parse Codex ``exec --json`` rows into normalized RuntimeEvents.

    Current canonical Codex events include ``thread.started``, ``turn.started``,
    ``item.started``, ``item.updated``, ``item.completed``, ``turn.completed``,
    ``turn.failed`` and ``error``. Unknown rows are preserved as ``raw`` events
    so callers can survive additive schema changes without silently losing data.
    """

    _TOOL_TYPES = {
        "command_execution",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
        "file_change",
    }

    def parse(
        self,
        row: dict[str, Any],
        *,
        session_id: str,
        turn_id: str,
    ) -> list[RuntimeEvent]:
        event_type = str(row.get("type") or row.get("event") or "")

        if event_type == "thread.started":
            provider_session_id = str(row.get("thread_id") or "")
            return [
                RuntimeEvent.make(
                    "session_switched",
                    session_id=session_id,
                    turn_id=turn_id,
                    provider="codex",
                    provider_session_id=provider_session_id,
                )
            ]

        # BridgeRuntime already emits turn_start when the frontend sends a turn.
        if event_type == "turn.started":
            return []

        if event_type in {"item.started", "item.updated", "item.completed"}:
            item = row.get("item")
            if not isinstance(item, dict):
                return [self._raw(row, session_id=session_id, turn_id=turn_id)]
            return self._parse_item(
                event_type,
                item,
                session_id=session_id,
                turn_id=turn_id,
            )

        if event_type == "turn.completed":
            return [
                RuntimeEvent.make(
                    "turn_end",
                    session_id=session_id,
                    turn_id=turn_id,
                    provider="codex",
                    usage=row.get("usage", {}),
                )
            ]

        if event_type == "turn.failed":
            error = row.get("error")
            message = error.get("message", "") if isinstance(error, dict) else str(error or "")
            return [
                RuntimeEvent.make(
                    "turn_error",
                    session_id=session_id,
                    turn_id=turn_id,
                    provider="codex",
                    error="turn_failed",
                    message=message,
                )
            ]

        if event_type == "error":
            return [
                RuntimeEvent.make(
                    "turn_error",
                    session_id=session_id,
                    turn_id=turn_id,
                    provider="codex",
                    error="stream_error",
                    message=str(row.get("message") or ""),
                )
            ]

        return [self._raw(row, session_id=session_id, turn_id=turn_id)]

    def _parse_item(
        self,
        event_type: str,
        item: dict[str, Any],
        *,
        session_id: str,
        turn_id: str,
    ) -> list[RuntimeEvent]:
        item_type = str(item.get("type") or item.get("item_type") or "")
        item_id = str(item.get("id") or "")

        # Updates are intentionally quiet for now. Codex may emit several
        # snapshots for one item; final normalized output comes from completed.
        if event_type == "item.updated":
            return []

        if item_type in {"agent_message", "assistant_message"}:
            if event_type != "item.completed":
                return []
            text = str(item.get("text") or "")
            return [
                RuntimeEvent.make(
                    "assistant_delta",
                    session_id=session_id,
                    turn_id=turn_id,
                    provider="codex",
                    text=text,
                )
            ] if text else []

        if item_type == "reasoning":
            if event_type != "item.completed":
                return []
            text = str(item.get("text") or "")
            return [
                RuntimeEvent.make(
                    "thinking",
                    session_id=session_id,
                    turn_id=turn_id,
                    provider="codex",
                    text=text,
                    visible=bool(text),
                    summary=True,
                )
            ]

        if item_type in self._TOOL_TYPES:
            if event_type == "item.started":
                return [self._tool_start(item_type, item_id, item, session_id, turn_id)]
            if event_type == "item.completed":
                # Codex currently emits file_change only as a completed item.
                # Synthesize a matching start so consumers can keep one tool
                # lifecycle model across providers.
                events: list[RuntimeEvent] = []
                if item_type == "file_change":
                    events.append(
                        self._tool_start(
                            item_type,
                            item_id,
                            item,
                            session_id,
                            turn_id,
                            synthetic=True,
                        )
                    )
                events.append(self._tool_result(item_type, item_id, item, session_id, turn_id))
                return events

        # Codex error items are documented as non-fatal, so they must not close
        # the bridge turn. Preserve them as raw provider events instead.
        return [
            RuntimeEvent.make(
                "raw",
                session_id=session_id,
                turn_id=turn_id,
                provider="codex",
                provider_event=event_type,
                item_type=item_type,
                item=item,
            )
        ]

    def _tool_start(
        self,
        item_type: str,
        item_id: str,
        item: dict[str, Any],
        session_id: str,
        turn_id: str,
        *,
        synthetic: bool = False,
    ) -> RuntimeEvent:
        name, payload = self._tool_identity(item_type, item)
        return RuntimeEvent.make(
            "tool_start",
            session_id=session_id,
            turn_id=turn_id,
            provider="codex",
            tool_name=name,
            tool_call_id=item_id,
            input=payload,
            synthetic=synthetic,
        )

    def _tool_result(
        self,
        item_type: str,
        item_id: str,
        item: dict[str, Any],
        session_id: str,
        turn_id: str,
    ) -> RuntimeEvent:
        status = str(item.get("status") or "")
        is_error = status in {"failed", "declined"}
        content: Any = ""
        extra: dict[str, Any] = {"status": status}

        if item_type == "command_execution":
            content = item.get("aggregated_output", "")
            extra["exit_code"] = item.get("exit_code")
            if item.get("exit_code") not in {None, 0}:
                is_error = True
        elif item_type == "mcp_tool_call":
            error = item.get("error")
            if isinstance(error, dict) and error.get("message"):
                content = error.get("message")
                is_error = True
            else:
                content = item.get("result")
        elif item_type == "collab_tool_call":
            content = item.get("agents_states", {})
        elif item_type == "web_search":
            content = {"query": item.get("query"), "action": item.get("action")}
        elif item_type == "file_change":
            content = item.get("changes", [])

        return RuntimeEvent.make(
            "tool_result",
            session_id=session_id,
            turn_id=turn_id,
            provider="codex",
            tool_call_id=item_id,
            content=content,
            is_error=is_error,
            **extra,
        )

    def _tool_identity(self, item_type: str, item: dict[str, Any]) -> tuple[str, Any]:
        if item_type == "command_execution":
            return "command_execution", {"command": item.get("command", "")}
        if item_type == "mcp_tool_call":
            server = str(item.get("server") or "")
            tool = str(item.get("tool") or "")
            name = ".".join(part for part in (server, tool) if part) or "mcp_tool_call"
            return name, item.get("arguments", {})
        if item_type == "collab_tool_call":
            tool = str(item.get("tool") or "")
            return (
                f"collab.{tool}" if tool else "collab_tool_call",
                {
                    "sender_thread_id": item.get("sender_thread_id"),
                    "receiver_thread_ids": item.get("receiver_thread_ids", []),
                    "prompt": item.get("prompt"),
                },
            )
        if item_type == "web_search":
            return "web_search", {"query": item.get("query"), "action": item.get("action")}
        if item_type == "file_change":
            return "file_change", {"changes": item.get("changes", [])}
        return item_type or "tool", item

    def _raw(self, row: dict[str, Any], *, session_id: str, turn_id: str) -> RuntimeEvent:
        return RuntimeEvent.make(
            "raw",
            session_id=session_id,
            turn_id=turn_id,
            provider="codex",
            provider_event=str(row.get("type") or row.get("event") or "unknown"),
            row=row,
        )


__all__ = ["CodexExecParser"]
