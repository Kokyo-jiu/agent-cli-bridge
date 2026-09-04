from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from agent_cli_bridge import (
    BridgeRuntime,
    ClaudeStyleTranscriptParser,
    SessionRegistry,
    SubprocessPromptTransport,
    TranscriptTailer,
)


def main() -> int:
    here = Path(__file__).resolve().parent
    worker = here / "mock_line_agent.py"

    with tempfile.TemporaryDirectory(prefix="agent-cli-bridge-") as tmp:
        transcript = Path(tmp) / "session.jsonl"
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

        runtime.send_turn("demo", "inspect the project")
        deadline = time.monotonic() + 5

        try:
            while time.monotonic() < deadline:
                events = runtime.poll("demo")
                for event in events:
                    print(f"{event.kind:16} {event.data}")
                if any(event.kind == "turn_end" for event in events):
                    return 0
                time.sleep(0.02)
        finally:
            runtime.close_session("demo")

    raise SystemExit("timed out waiting for the mock CLI")


if __name__ == "__main__":
    raise SystemExit(main())
