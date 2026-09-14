from pathlib import Path

from agent_cli_bridge import (
    APP_SERVER_COMMAND,
    CodexExecParser,
    CodexSessionSpec,
    commit_assistant_history,
    run_session_turn,
)

PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
TURN = "33333333-3333-4333-8333-333333333333"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.closed = False

    def send(self, message):
        self.sent.append(message)

    def receive(self, timeout):
        assert timeout > 0
        if not self.responses:
            raise AssertionError("fake transport exhausted")
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def _spec(tmp_path: Path, *, mode="seed", parent=None):
    system_file = tmp_path / "system.txt"
    system_file.write_text("caller supplied SP", encoding="utf-8")
    return CodexSessionSpec(
        mode=mode,
        cwd=str(tmp_path),
        system_file=str(system_file),
        parent_thread_id=parent,
    )


def _assert_near_bare_thread_params(params, *, web_search: str, image_generation: bool):
    assert params["developerInstructions"] == ""
    config = params["config"]
    assert config["web_search"] == web_search
    assert config["include_collaboration_mode_instructions"] is False
    assert config["include_environment_context"] is False
    assert config["include_apps_instructions"] is False
    assert config["include_permissions_instructions"] is False
    assert config["skills"] == {"include_instructions": False}
    assert config["features"]["image_generation"] is image_generation
    assert config["features"]["multi_agent_v2"] == {
        "enabled": False,
        "root_agent_usage_hint_text": "",
        "multi_agent_mode_hint_text": "",
    }


def test_app_server_command_is_strict_and_disables_external_extension_sources():
    command = list(APP_SERVER_COMMAND)
    assert command[:3] == ["codex", "app-server", "--stdio"]
    assert "--strict-config" in command
    assert "mcp_servers={}" in command
    for feature in ("plugins", "apps", "multi_agent", "shell_tool", "workspace_dependencies"):
        idx = command.index(feature)
        assert command[idx - 1] == "--disable"


def test_seed_turn_uses_near_bare_thread_start_and_streams_events(tmp_path):
    transport = FakeTransport([
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": CHILD, "ephemeral": False}, "model": "gpt-5.6-codex"}},
        {"id": 3, "result": {"turn": {"id": TURN}}},
        {"method": "item/agentMessage/delta", "params": {"threadId": CHILD, "turnId": TURN, "itemId": "msg-1", "delta": "hello"}},
        {"method": "item/completed", "params": {"threadId": CHILD, "turnId": TURN, "item": {"id": "msg-1", "type": "agentMessage", "text": "hello"}}},
        {"method": "turn/completed", "params": {"threadId": CHILD, "turn": {"id": TURN, "status": "completed"}}},
    ])
    emitted = []
    child = run_session_turn(_spec(tmp_path), "hi", emit=emitted.append, transport_factory=lambda **_: transport, response_timeout=1, turn_timeout=1)
    assert child == CHILD
    assert transport.closed is True
    start = next(msg for msg in transport.sent if msg.get("method") == "thread/start")
    params = start["params"]
    assert params["baseInstructions"] == "caller supplied SP"
    _assert_near_bare_thread_params(
        params, web_search="live", image_generation=False
    )
    assert params["dynamicTools"] == []
    assert params["selectedCapabilityRoots"] == []
    assert params["sandbox"] == "read-only"
    assert params["approvalPolicy"] == "never"
    assert params["ephemeral"] is False
    turn = next(msg for msg in transport.sent if msg.get("method") == "turn/start")
    assert turn["params"] == {"threadId": CHILD, "input": [{"type": "text", "text": "hi"}]}
    assert emitted[0]["type"] == "thread.started"
    assert any(event["type"] == "item.agent_message.delta" for event in emitted)
    assert emitted[-1]["type"] == "turn.completed"


def test_fork_turn_never_mutates_parent_and_returns_candidate_child(tmp_path):
    transport = FakeTransport([
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": CHILD, "ephemeral": False, "forkedFromId": PARENT}, "model": "gpt-5.6-codex"}},
        {"id": 3, "result": {"turn": {"id": TURN}}},
        {"method": "item/completed", "params": {"threadId": CHILD, "turnId": TURN, "item": {"id": "msg-1", "type": "agentMessage", "text": "done"}}},
        {"method": "turn/completed", "params": {"threadId": CHILD, "turn": {"id": TURN, "status": "completed"}}},
    ])
    child = run_session_turn(_spec(tmp_path, mode="fork", parent=PARENT), "next", emit=lambda _: None, transport_factory=lambda **_: transport, response_timeout=1, turn_timeout=1)
    assert child == CHILD
    fork = next(msg for msg in transport.sent if msg.get("method") == "thread/fork")
    assert fork["params"]["threadId"] == PARENT
    _assert_near_bare_thread_params(
        fork["params"], web_search="live", image_generation=False
    )
    turn = next(msg for msg in transport.sent if msg.get("method") == "turn/start")
    assert turn["params"]["threadId"] == CHILD


def test_clean_sibling_history_commit_injects_assistant_without_model_turn(tmp_path):
    transport = FakeTransport([
        {"id": 1, "result": {}},
        {"id": 2, "result": {"thread": {"id": CHILD, "ephemeral": False, "forkedFromId": PARENT}, "model": "gpt-5.6-codex"}},
        {"id": 3, "result": {}},
    ])
    emitted = []
    child = commit_assistant_history(_spec(tmp_path, mode="fork", parent=PARENT), "visible assistant reply", emit=emitted.append, transport_factory=lambda **_: transport, response_timeout=1)
    assert child == CHILD
    methods = [msg.get("method") for msg in transport.sent]
    assert "turn/start" not in methods
    history_fork = next(msg for msg in transport.sent if msg.get("method") == "thread/fork")
    _assert_near_bare_thread_params(
        history_fork["params"], web_search="disabled", image_generation=False
    )
    injected = next(msg for msg in transport.sent if msg.get("method") == "thread/inject_items")
    assert injected["params"]["threadId"] == CHILD
    item = injected["params"]["items"][0]
    assert item["role"] == "assistant"
    assert item["content"][0]["text"] == "visible assistant reply"
    assert emitted[-1] == {"type": "history.committed", "thread_id": CHILD}


def test_codex_parser_accepts_app_server_stream_reasoning_and_failure_shapes():
    parser = CodexExecParser()
    delta = parser.parse({"type": "item.agent_message.delta", "item_id": "m1", "delta": "hel"}, session_id="s", turn_id="t")
    assert [(e.kind, e.data.get("text")) for e in delta] == [("assistant_delta", "hel")]
    assert parser.parse({"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "hello"}}, session_id="s", turn_id="t") == []
    reasoning = parser.parse({"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "summary": "checked"}}, session_id="s", turn_id="t")
    assert reasoning[0].kind == "thinking"
    assert reasoning[0].data["text"] == "checked"
    failed = parser.parse({"type": "turn.failed", "message": "Codex app-server turn status: failed"}, session_id="s", turn_id="t")
    assert failed[0].kind == "turn_error"
    assert failed[0].data["message"] == "Codex app-server turn status: failed"
