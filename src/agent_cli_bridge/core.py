from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class RuntimeEvent:
    kind: str
    session_id: str = ""
    turn_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now)

    @classmethod
    def make(
        cls,
        kind: str,
        *,
        session_id: str = "",
        turn_id: str = "",
        **data: Any,
    ) -> "RuntimeEvent":
        return cls(
            kind=kind,
            session_id=session_id,
            turn_id=turn_id,
            data=data,
        )

from dataclasses import dataclass, field
from datetime import datetime, timezone
import threading
import uuid


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class BridgeSession:
    key: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    epoch: int = 1
    provider_session_id: str = ""
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    active_turn_id: str = ""

    def touch(self) -> None:
        self.updated_at = _utc_now()


class SessionRegistry:
    """Thread-safe logical session registry.

    The registry deliberately stores only bridge continuity metadata.
    Provider-native state remains owned by the provider/CLI.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, BridgeSession] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> BridgeSession | None:
        with self._lock:
            return self._sessions.get(key)

    def ensure(self, key: str) -> tuple[BridgeSession, bool]:
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                session.touch()
                return session, False
            session = BridgeSession(key=key)
            self._sessions[key] = session
            return session, True

    def replace_provider_session(self, key: str, provider_session_id: str) -> BridgeSession:
        with self._lock:
            session, _ = self.ensure(key)
            if session.provider_session_id != provider_session_id:
                session.provider_session_id = provider_session_id
                session.epoch += 1
                session.active_turn_id = ""
                session.touch()
            return session

    def remove(self, key: str) -> BridgeSession | None:
        with self._lock:
            return self._sessions.pop(key, None)

from typing import Protocol



class PromptTransport(Protocol):
    """Provider-specific boundary for delivering a new turn to a CLI session."""

    def open_session(self, session: BridgeSession) -> None:
        ...

    def send(self, session: BridgeSession, message: str, turn_id: str) -> None:
        ...

    def close_session(self, session: BridgeSession) -> None:
        ...


class InMemoryTransport:
    """Tiny reference transport used by examples and tests."""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.sent: list[tuple[str, str, str]] = []
        self.closed: list[str] = []

    def open_session(self, session: BridgeSession) -> None:
        self.opened.append(session.session_id)

    def send(self, session: BridgeSession, message: str, turn_id: str) -> None:
        self.sent.append((session.session_id, turn_id, message))

    def close_session(self, session: BridgeSession) -> None:
        self.closed.append(session.session_id)

import base64
import json
from typing import Any

HELLO_PREFIX = "AGENTBRIDGE/1 hello"
EVENT_PREFIX = "AGENTBRIDGE/1 event"


def _pack(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _unpack(value: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(value.encode("ascii"))
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("protocol payload must be a JSON object")
    return obj


def encode_hello(**data: Any) -> str:
    return f"{HELLO_PREFIX} {_pack({'version': 1, **data})}"


def encode_event(**data: Any) -> str:
    return f"{EVENT_PREFIX} {_pack({'version': 1, **data})}"


def decode_line(line: str) -> tuple[str, dict[str, Any]]:
    line = line.strip()
    for kind, prefix in (("hello", HELLO_PREFIX), ("event", EVENT_PREFIX)):
        marker = prefix + " "
        if line.startswith(marker):
            return kind, _unpack(line[len(marker):])
    raise ValueError("unknown protocol line")

import json
import os
from pathlib import Path
from typing import Any, Protocol



class TranscriptParser(Protocol):
    def parse(
        self,
        row: dict[str, Any],
        *,
        session_id: str,
        turn_id: str,
    ) -> list[RuntimeEvent]:
        ...


class ClaudeStyleTranscriptParser:
    """Parse common Claude-style JSONL transcript content blocks.

    It intentionally operates on the row shape rather than private filesystem or
    deployment conventions. Terminal detection supports explicit stop/terminal
    markers and can be extended by subclassing ``is_terminal``.
    """

    def parse(
        self,
        row: dict[str, Any],
        *,
        session_id: str,
        turn_id: str,
    ) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        msg = row.get("message") if isinstance(row.get("message"), dict) else row
        role = msg.get("role") or row.get("role")
        content = msg.get("content", row.get("content", []))
        if isinstance(content, dict):
            content = [content]

        if role == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "text":
                    text = str(block.get("text") or "")
                    if text:
                        events.append(RuntimeEvent.make(
                            "assistant_delta",
                            session_id=session_id,
                            turn_id=turn_id,
                            text=text,
                        ))
                elif kind == "thinking":
                    text = str(block.get("thinking") or block.get("text") or "")
                    events.append(RuntimeEvent.make(
                        "thinking",
                        session_id=session_id,
                        turn_id=turn_id,
                        text=text,
                        visible=bool(text),
                    ))
                elif kind == "tool_use":
                    events.append(RuntimeEvent.make(
                        "tool_start",
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_name=str(block.get("name") or ""),
                        tool_call_id=str(block.get("id") or ""),
                        input=block.get("input", {}),
                    ))

        if role == "user" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    events.append(RuntimeEvent.make(
                        "tool_result",
                        session_id=session_id,
                        turn_id=turn_id,
                        tool_call_id=str(block.get("tool_use_id") or ""),
                        content=block.get("content", ""),
                        is_error=bool(block.get("is_error", False)),
                    ))

        if self.is_terminal(row, msg):
            events.append(RuntimeEvent.make(
                "turn_end",
                session_id=session_id,
                turn_id=turn_id,
                stop_reason=msg.get("stop_reason") or row.get("stop_reason") or "",
            ))
        return events

    def is_terminal(self, row: dict[str, Any], msg: dict[str, Any]) -> bool:
        if row.get("terminal") is True or row.get("turn_done") is True:
            return True
        typ = row.get("type") or row.get("event")
        if typ in {"turn_end", "turn_done", "result"}:
            return True
        stop_reason = msg.get("stop_reason") or row.get("stop_reason")
        return bool(stop_reason)


class TranscriptTailer:
    """Incrementally tail JSONL with durable byte-offset recovery."""

    def __init__(
        self,
        *,
        path: str | os.PathLike[str],
        parser: TranscriptParser,
        offset_path: str | os.PathLike[str] | None = None,
        cold_start: str = "tail",
    ) -> None:
        self.path = Path(path)
        self.parser = parser
        self.offset_path = Path(offset_path) if offset_path else self.path.with_suffix(
            self.path.suffix + ".offset.json"
        )
        if cold_start not in {"head", "tail"}:
            raise ValueError("cold_start must be 'head' or 'tail'")
        self.cold_start = cold_start
        self.offset = self._load_offset()

    def _load_offset(self) -> int:
        size = self.path.stat().st_size if self.path.exists() else 0
        try:
            data = json.loads(self.offset_path.read_text(encoding="utf-8"))
            if data.get("path") != str(self.path):
                raise ValueError("path changed")
            offset = int(data["offset"])
            if not 0 <= offset <= size:
                raise ValueError("bad offset")
            return offset
        except Exception:
            return 0 if self.cold_start == "head" else size

    def _commit(self) -> None:
        self.offset_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.offset_path.with_suffix(self.offset_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                {"path": str(self.path), "offset": self.offset},
                ensure_ascii=False,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.offset_path)

    def poll(self, *, session_id: str, turn_id: str) -> list[RuntimeEvent]:
        if not self.path.exists():
            return []

        out: list[RuntimeEvent] = []
        with self.path.open("rb") as handle:
            handle.seek(self.offset)
            while True:
                row_start = handle.tell()
                raw = handle.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    # Do not advance past an incomplete trailing row.
                    handle.seek(row_start)
                    break
                try:
                    row = json.loads(raw.decode("utf-8"))
                    if not isinstance(row, dict):
                        raise ValueError("row must be object")
                    parsed = self.parser.parse(
                        row,
                        session_id=session_id,
                        turn_id=turn_id,
                    )
                except Exception as exc:
                    parsed = [RuntimeEvent.make(
                        "raw",
                        session_id=session_id,
                        turn_id=turn_id,
                        error="bad_jsonl",
                        detail=type(exc).__name__,
                    )]
                out.extend(parsed)
                self.offset = handle.tell()
                self._commit()
        return out

import uuid



class BridgeRuntime:
    """Coordinates logical sessions, thin-turn injection, and transcript events."""

    def __init__(
        self,
        *,
        registry: SessionRegistry,
        transport: PromptTransport,
        tailer: TranscriptTailer,
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.tailer = tailer
        self._pending: dict[str, list[RuntimeEvent]] = {}

    def ensure_session(self, key: str) -> BridgeSession:
        session, created = self.registry.ensure(key)
        if created:
            self.transport.open_session(session)
            self._pending.setdefault(key, []).append(
                RuntimeEvent.make("session_started", session_id=session.session_id)
            )
        return session

    def send_turn(self, key: str, message: str) -> str:
        if not message:
            raise ValueError("message must not be empty")
        session = self.ensure_session(key)
        if session.active_turn_id:
            raise RuntimeError("a turn is already active for this session")

        turn_id = str(uuid.uuid4())
        session.active_turn_id = turn_id
        session.touch()
        self._pending.setdefault(key, []).append(
            RuntimeEvent.make(
                "turn_start",
                session_id=session.session_id,
                turn_id=turn_id,
            )
        )
        try:
            self.transport.send(session, message, turn_id)
        except Exception as exc:
            session.active_turn_id = ""
            self._pending[key].append(
                RuntimeEvent.make(
                    "turn_error",
                    session_id=session.session_id,
                    turn_id=turn_id,
                    error=type(exc).__name__,
                )
            )
            raise
        return turn_id

    def poll(self, key: str) -> list[RuntimeEvent]:
        session = self.registry.get(key)
        if session is None:
            return []

        out = self._pending.pop(key, [])
        if session.active_turn_id:
            parsed = self.tailer.poll(
                session_id=session.session_id,
                turn_id=session.active_turn_id,
            )
            out.extend(parsed)
            if any(event.kind in {"turn_end", "turn_error"} for event in parsed):
                session.active_turn_id = ""
                session.touch()
        return out

    def close_session(self, key: str) -> None:
        session = self.registry.remove(key)
        if session is not None:
            seldf.transport.close_session(session)


__all__ = [
    "BridgeRuntime", "BridgeSession", "ClaudeStyleTranscriptParser",
    "InMemoryTransport", "PromptTransport", "RuntimeEvent",
    "SessionRegistry", "TranscriptTailer", "encode_hello", "encode_event", "decode_line",
]
