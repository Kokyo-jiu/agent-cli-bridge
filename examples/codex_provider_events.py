"""Minimal Codex ``exec --json`` parsing example.

The rows below follow Codex's public JSONL event model. In a real integration,
feed each JSON object emitted by the CLI to CodexExecParser.
"""

from agent_cli_bridge import CodexExecParser


parser = CodexExecParser()
rows = [
    {"type": "thread.started", "thread_id": "thread-demo"},
    {"type": "turn.started"},
    {
        "type": "item.completed",
        "item": {
            "id": "reason-1",
            "type": "reasoning",
            "text": "Inspect the project before answering.",
        },
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
        "type": "item.completed",
        "item": {
            "id": "message-1",
            "type": "agent_message",
            "text": "All tests pass.",
        },
    },
    {
        "type": "turn.completed",
        "usage": {
            "input_tokens": 100,
            "cached_input_tokens": 20,
            "cache_write_input_tokens": 0,
            "output_tokens": 30,
            "reasoning_output_tokens": 8,
        },
    },
]

for row in rows:
    for event in parser.parse(row, session_id="demo", turn_id="turn-1"):
        print(event.kind, event.data)
