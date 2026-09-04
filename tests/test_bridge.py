import json

from agent_cli_bridge import (
    BridgeRuntime,
    ClaudeStyleTranscriptParser,
    InMemoryTransport,
    SessionRegistry,
    TranscriptTailer,
    decode_line,
    encode_event,
    encode_hello,
)


def test_protocol_round_trip():
    kind, obj = decode_line(encode_hello(capabilities=["events"]))
    assert kind == "hello"
    assert obj["version"] == 1
    assert obj["capabilities"] == ["events"]
    kind, obj = decode_line(encode_event(kind="tool_start", name="read"))
    assert kind == "event"
    assert obj["kind"] == "tool_start"


def test_session_registry_reuses_logical_session():
    registry = SessionRegistry()
    first, created = registry.ensure("chat")
    assert created is True
    second, created = registry.ensure("chat")
    assert created is False
    assert second.session_id == first.session_id


def test_provider_session_switch_increments_epoch():
    registry = SessionRegistry()
    session, _ = registry.ensure("chat")
    old_epoch = session.epoch
    session = registry.replace_provider_session("chat", "provider-2")
    assert session.epoch == old_epoch + 1
    assert session.provider_session_id == "provider-2"


def test_parser_keeps_tool_turn_open_until_terminal(tmp_path):
    path = tmp_path / "session.jsonl"
    rows = [
        {"message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "inspect"}]}},
        {"message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x"}}]}},
        {"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}},
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    tailer = TranscriptTailer(path=path, parser=ClaudeStyleTranscriptParser(), cold_start="head")
    events = tailer.poll(session_id="s", turn_id="t")
    assert [event.kind for event in events] == ["thinking", "tool_start", "tool_result", "assistant_delta", "turn_end"]


def test_incomplete_row_is_not_committed(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_bytes(b'{"message":{"role":"assistant"')
    tailer = TranscriptTailer(path=path, parser=ClaudeStyleTranscriptParser(), cold_start="head")
    assert tailer.poll(session_id="s", turn_id="t") == []
    assert tailer.offset == 0


def test_runtime_reuses_session_and_finishes_only_on_terminal(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")
    transport = InMemoryTransport()
    runtime = BridgeRuntime(
        registry=SessionRegistry(),
        transport=transport,
        tailer=TranscriptTailer(path=transcript, parser=ClaudeStyleTranscriptParser(), cold_start="head"),
    )
    first_turn = runtime.send_turn("chat", "hello")
    first_session = runtime.registry.get("chat").session_id
    assert [e.kind for e in runtime.poll("chat")] == ["session_started", "turn_start"]
    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "read", "input": {}}]}}) + "\n")
    assert [e.kind for e in runtime.poll("chat")] == ["tool_start"]
    assert runtime.registry.get("chat").active_turn_id == first_turn
    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}) + "\n")
        f.write(json.dumps({"message": {"role": "assistant", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn"}}) + "\n")
    assert [e.kind for e in runtime.poll("chat")] == ["tool_result", "assistant_delta", "turn_end"]
    assert runtime.registry.get("chat").active_turn_id == ""
    runtime.send_turn("chat", "again")
    assert runtime.registry.get("chat").session_id == first_session
