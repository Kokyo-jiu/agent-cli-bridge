"""Minimal Claude-style transcript parsing example.

This example has no provider dependency. It demonstrates the row shape consumed
by ClaudeTranscriptParser and the normalized events produced by the bridge.
"""

from agent_cli_bridge import ClaudeTranscriptParser


parser = ClaudeTranscriptParser()
rows = [
    {
        "message": {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "I should inspect the project."}],
        }
    },
    {
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                }
            ],
        }
    },
    {
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool-1", "content": "# demo"}
            ],
        }
    },
    {
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "The README is present."}],
            "stop_reason": "end_turn",
        }
    },
]

for row in rows:
    for event in parser.parse(row, session_id="demo", turn_id="turn-1"):
        print(event.kind, event.data)
