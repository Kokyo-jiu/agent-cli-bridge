# agent-cli-bridge

Turn CLI agents into persistent, streaming conversational backends.

`agent-cli-bridge` extracts a small runtime pattern for applications that want to use a coding/agent CLI as more than a one-shot command:

- keep a logical session across many frontend turns;
- send only the new user turn instead of rebuilding the entire conversation every time;
- normalize thinking, assistant text, tool calls, tool results, turn boundaries, and errors into one event stream;
- keep tool-using turns open until the CLI has actually finished the turn;
- isolate provider-specific behavior behind adapters;
- recover transcript reading from a durable byte offset.

This repository is intentionally **runtime-only**. It contains no application persona, private memory data, deployment endpoints, credentials, or application-specific routing.

## Why

A normal request/response wrapper often treats a CLI like this:

```text
frontend message
      ↓
spawn process
      ↓
final string
      ↓
process disappears
```

A persistent agent backend needs a different shape:

```text
frontend
   │  thin turn: only the new message
   ▼
BridgeRuntime
   ├── SessionRegistry
   ├── PromptTransport
   └── TranscriptTailer
          │
          ├── thinking
          ├── assistant_delta
          ├── tool_start
          ├── tool_result
          └── turn_end
   │
   ▼
long-lived / resumable CLI session
```

The important distinction is **thick session, thin turn**: continuity belongs to the CLI session; a frontend turn should not have to replay the whole transcript.

## v0.1 scope

The first public extraction focuses on the reusable core:

1. normalized runtime events;
2. persistent logical session registry;
3. provider/transport adapter boundary;
4. JSONL transcript tailing with durable offsets;
5. a Claude-style transcript parser for text / thinking / tool-use / tool-result blocks;
6. a runtime coordinator that keeps a turn alive through tool activity;
7. a long-lived subprocess transport and runnable end-to-end demo;
8. tests across Python 3.10–3.12.

The application-specific transport used by the original deployment is deliberately not included. Real CLIs can be connected through `PromptTransport`, while transcript parsing stays independent.

## Quick start

```python
from agent_cli_bridge import (
    BridgeRuntime,
    InMemoryTransport,
    SessionRegistry,
    TranscriptTailer,
    ClaudeStyleTranscriptParser,
)

registry = SessionRegistry()
transport = InMemoryTransport()
tailer = TranscriptTailer(
    path="/path/to/session.jsonl",
    parser=ClaudeStyleTranscriptParser(),
)

runtime = BridgeRuntime(
    registry=registry,
    transport=transport,
    tailer=tailer,
)

runtime.send_turn("my-chat", "Inspect the project and explain the failing tests.")

for event in runtime.poll("my-chat"):
    print(event.kind, event.data)
```

## Runnable subprocess example

`SubprocessPromptTransport` keeps one CLI process alive per logical bridge session and writes each new turn to its stdin. By default it uses newline-delimited JSON:

```json
{"type":"turn","session_id":"...","turn_id":"...","message":"inspect the project"}
```

The process can keep its own workspace, tools, native session state, and transcript. The bridge only owns lifecycle metadata and normalized runtime events.

Run the included end-to-end demo:

```bash
python examples/subprocess_chat.py
```

It starts `examples/mock_line_agent.py`, sends a turn through a real long-lived subprocess, tails the generated JSONL transcript, and emits:

```text
session_started
turn_start
thinking
tool_start
tool_result
assistant_delta
turn_end
```

The same subprocess is reused for later turns in that logical session.

A minimal real adapter setup looks like:

```python
import sys

from agent_cli_bridge import SubprocessPromptTransport

transport = SubprocessPromptTransport(
    [sys.executable, "my_agent_cli.py"],
    env={"MY_AGENT_TRANSCRIPT": "/path/to/session.jsonl"},
)
```

For CLIs that accept plain newline-separated prompts instead of JSONL, use `input_mode="line"`.

`command` is always an argv sequence and is launched without a shell. Provider-specific flags and transcript locations belong in the application adapter, not in the bridge core.

## Runtime event model

The public event vocabulary is deliberately small:

- `session_started`
- `session_switched`
- `turn_start`
- `thinking`
- `assistant_delta`
- `tool_start`
- `tool_result`
- `turn_end`
- `turn_error`
- `raw`

Applications can render these differently without parsing provider-native transcript rows themselves.

## Adapter boundary

A transport only needs to know how to deliver the next user turn:

```python
class PromptTransport(Protocol):
    def open_session(self, session: BridgeSession) -> None: ...
    def send(self, session: BridgeSession, message: str, turn_id: str) -> None: ...
    def close_session(self, session: BridgeSession) -> None: ...
```

The CLI can keep its own state, process, workspace, tools, and transcript. The bridge is responsible for continuity metadata and normalized events rather than reimplementing the agent.

## Tool continuation

Tool use is not treated as a terminal response.

A turn may look like:

```text
turn_start
thinking
tool_start
tool_result
thinking
tool_start
tool_result
assistant_delta
turn_end
```

The bridge only marks the turn complete when the transcript parser sees an actual terminal signal.

## Transcript safety and recovery

`TranscriptTailer` stores a byte offset in a small JSON state file. On restart it can resume from the last committed row instead of replaying the whole file. Invalid/incomplete trailing rows are held until the next poll.

## Privacy boundary

The public package intentionally excludes:

- prompts and persona text;
- private conversation or memory data;
- private domains, IPs, filesystem layout, and deployment endpoints;
- credentials, tokens, cookies, and signing secrets;
- application-specific routing, queues, and callbacks;
- private memory/context injection logic.

## Roadmap

- [x] line-oriented subprocess transport + runnable end-to-end demo
- [ ] provider-specific adapter examples
- [ ] SSE/WebSocket event fan-out
- [ ] richer session resume policies
- [ ] pluggable transcript parsers
- [ ] reference frontend activity timeline

## License

MIT.
