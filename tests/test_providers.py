from agent_cli_bridge import ClaudeTranscriptParser, CodexExecParser


def _kinds(events):
    return [event.kind for event in events]


def test_claude_provider_import_keeps_existing_behavior():
    parser = ClaudeTranscriptParser()
    events = parser.parse(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "inspect"},
                    {"type": "text", "text": "done"},
                ],
                "stop_reason": "end_turn",
            }
        },
        session_id="bridge-session",
        turn_id="turn-1",
    )
    assert _kinds(events) == ["thinking", "assistant_delta", "turn_end"]


def test_codex_exec_successful_tool_turn_maps_to_normalized_events():
    parser = CodexExecParser()
    rows = [
        {"type": "thread.started", "thread_id": "thread-123"},
        {"type": "turn.started"},
        {
            "type": "item.completed",
            "item": {"id": "reason-1", "type": "reasoning", "text": "Inspect the repository."},
        },
        {
            "type": "item.started",
            "item": {
                "id": "cmd-1",
                "type": "command_execution",
                "command": "python -m pytest -q",
                "aggregated_output": "",
                "exit_code": None,
                "status": "in_progress",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "cmd-1",
                "type": "command_execution",
                "command": "python -m pytest -q",
                "aggregated_output": "8 passed",
                "exit_code": 0,
                "status": "completed",
            },
        },
        {
            "type": "item.started",
            "item": {
                "id": "mcp-1",
                "type": "mcp_tool_call",
                "server": "docs",
                "tool": "read",
                "arguments": {"path": "README.md"},
                "result": None,
                "error": None,
                "status": "in_progress",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "mcp-1",
                "type": "mcp_tool_call",
                "server": "docs",
                "tool": "read",
                "arguments": {"path": "README.md"},
                "result": {"content": [{"type": "text", "text": "# demo"}]},
                "error": None,
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {"id": "message-1", "type": "agent_message", "text": "Everything passes."},
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "cached_input_tokens": 20,
                "cache_write_input_tokens": 0,
                "output_tokens": 40,
                "reasoning_output_tokens": 12,
            },
        },
    ]

    events = []
    for row in rows:
        events.extend(parser.parse(row, session_id="bridge-session", turn_id="turn-1"))

    assert _kinds(events) == [
        "session_switched",
        "thinking",
        "tool_start",
        "tool_result",
        "tool_start",
        "tool_result",
        "assistant_delta",
        "turn_end",
    ]
    assert events[0].data["provider_session_id"] == "thread-123"
    assert events[1].data["summary"] is True
    assert events[2].data["tool_name"] == "command_execution"
    assert events[3].data["content"] == "8 passed"
    assert events[4].data["tool_name"] == "docs.read"
    assert events[5].data["is_error"] is False
    assert events[6].data["text"] == "Everything passes."
    assert events[7].data["usage"]["output_tokens"] == 40


def test_codex_file_change_gets_synthetic_start_and_result():
    parser = CodexExecParser()
    events = parser.parse(
        {
            "type": "item.completed",
            "item": {
                "id": "patch-1",
                "type": "file_change",
                "changes": [{"path": "README.md", "kind": "update"}],
                "status": "completed",
            },
        },
        session_id="bridge-session",
        turn_id="turn-1",
    )
    assert _kinds(events) == ["tool_start", "tool_result"]
    assert events[0].data["synthetic"] is True
    assert events[1].data["content"] == [{"path": "README.md", "kind": "update"}]


def test_codex_nonfatal_item_error_does_not_end_turn():
    parser = CodexExecParser()
    events = parser.parse(
        {
            "type": "item.completed",
            "item": {"id": "err-1", "type": "error", "message": "temporary provider warning"},
        },
        session_id="bridge-session",
        turn_id="turn-1",
    )
    assert _kinds(events) == ["raw"]
    assert events[0].data["item_type"] == "error"


def test_codex_turn_failure_is_terminal_error():
    parser = CodexExecParser()
    events = parser.parse(
        {"type": "turn.failed", "error": {"message": "model unavailable"}},
        session_id="bridge-session",
        turn_id="turn-1",
    )
    assert _kinds(events) == ["turn_error"]
    assert events[0].data["message"] == "model unavailable"


def test_codex_unknown_event_is_preserved_as_raw():
    parser = CodexExecParser()
    events = parser.parse(
        {"type": "future.event", "payload": {"x": 1}},
        session_id="bridge-session",
        turn_id="turn-1",
    )
    assert _kinds(events) == ["raw"]
    assert events[0].data["provider_event"] == "future.event"
