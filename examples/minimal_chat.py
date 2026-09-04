from pathlib import Path
import json
import tempfile

from agent_cli_bridge import (
    BridgeRuntime,
    ClaudeStyleTranscriptParser,
    InMemoryTransport,
    SessionRegistry,
    TranscriptTailer,
)


with tempfile.TemporaryDirectory() as tmp:
    transcript = Path(tmp) / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    transport = InMemoryTransport()
    runtime = BridgeRuntime(
        registry=SessionRegistry(),
        transport=transport,
        tailer=TranscriptTailer(
            path=transcript,
            parser=ClaudeStyleTranscriptParser(),
            cold_start="head",
        ),
    )

    turn_id = runtime.send_turn("demo", "Inspect the project.")
    print("sent:", transport.sent[-1])

    rows = [
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "thinking", "thinking": "I should inspect files."}],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "read_file",
                    "input": {"path": "README.md"},
                }],
            }
        },
        {
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "# demo",
                }],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "The project has a README."}],
                "stop_reason": "end_turn",
            }
        },
    ]
    with transcript.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    for event in runtime.poll("demo"):
        print(event.kind, event.data)
