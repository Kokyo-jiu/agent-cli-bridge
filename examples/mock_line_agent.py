from __future__ import annotations

import json
import os
import sys
from pathlib import Path


TRANSCRIPT_ENV = "AGENT_BRIDGE_TRANSCRIPT"


def append_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def main() -> int:
    transcript_value = os.environ.get(TRANSCRIPT_ENV)
    if not transcript_value:
        raise SystemExit(f"{TRANSCRIPT_ENV} is required")
    transcript = Path(transcript_value)

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        request = json.loads(raw)
        if request.get("type") != "turn":
            continue

        turn_id = str(request.get("turn_id") or "turn")
        message = str(request.get("message") or "")
        tool_id = f"echo-{turn_id[:8]}"

        append_row(
            transcript,
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "I will demonstrate a tool-using turn.",
                        }
                    ],
                }
            },
        )
        append_row(
            transcript,
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": "echo_tool",
                            "input": {"message": message},
                        }
                    ],
                }
            },
        )
        append_row(
            transcript,
            {
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": f"echo:{message}",
                        }
                    ],
                }
            },
        )
        append_row(
            transcript,
            {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "text",
                            "text": f"mock reply: {message}",
                        }
                    ],
                    "stop_reason": "end_turn",
                }
            },
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
