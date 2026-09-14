"""Run one Codex app-server seed/fork turn using the logged-in local subscription."""
from __future__ import annotations

import argparse
import json

from agent_cli_bridge import CodexSessionSpec, run_session_turn


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt")
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--system-file", required=True)
    parser.add_argument("--parent-thread-id")
    parser.add_argument("--model")
    args = parser.parse_args()

    mode = "fork" if args.parent_thread_id else "seed"
    spec = CodexSessionSpec(
        mode=mode,
        cwd=args.cwd,
        system_file=args.system_file,
        parent_thread_id=args.parent_thread_id,
        model=args.model,
    )
    child = run_session_turn(
        spec,
        args.prompt,
        emit=lambda event: print(json.dumps(event, ensure_ascii=False), flush=True),
    )
    print(json.dumps({"candidate_thread_id": child}), flush=True)


if __name__ == "__main__":
    main()
