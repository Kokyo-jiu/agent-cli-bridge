from __future__ import annotations

import sys
import time
from pathlib import Path

from agent_cli_bridge import (
    BridgeRuntime,
    ClaudeStyleTranscriptParser,
    SessionRegistry,
    SubprocessPromptTransport,
    TranscriptTailer,
)


def wait_for_turn(runtime: BridgeRuntime, key: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    collected = []
    while time.monotonic() < deadline:
        events = runtime.poll(key)
        collected.extend(events)
        if any(event.kind == "turn_end" for event in events):
            return collected
        time.sleep(0.01)
    raise AssertionError("timed out waiting for turn_end")


def test_subprocess_transport_reuses_process_and_continues_through_tools(tmp_path):
    worker = Path(__file__).resolve().parents[1] / "examples" / "mock_line_agent.py"
    transcript = tmp_path / "session.jsonl"
    transport = SubprocessPromptTransport(
        [sys.executable, str(worker)],
        env={"AGENT_BRIDGE_TRANSCRIPT": str(transcript)},
    )
    runtime = BridgeRuntime(
        registry=SessionRegistry(),
        transport=transport,
        tailer=TranscriptTailer(
            path=transcript,
            parser=ClaudeStyleTranscriptParser(),
            cold_start="head",
        ),
    )

    runtime.send_turn("chat", "first")
    first_session = runtime.registry.get("chat")
    assert first_session is not None
    first_pid = transport.pid(first_session)
    assert first_pid is not None

    first_events = wait_for_turn(runtime, "chat")
    assert [event.kind for event in first_events] == [
        "session_started",
        "turn_start",
        "thinking",
        "tool_start",
        "tool_result",
        "assistant_delta",
        "turn_end",
    ]
    assert transport.is_running(first_session)

    runtime.send_turn("chat", "second")
    second_events = wait_for_turn(runtime, "chat")
    assert [event.kind for event in second_events] == [
        "turn_start",
        "thinking",
        "tool_start",
        "tool_result",
        "assistant_delta",
        "turn_end",
    ]
    assert transport.pid(first_session) == first_pid

    runtime.close_session("chat")
    assert runtime.registry.get("chat") is None
    assert not transport.is_running(first_session)
