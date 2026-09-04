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
7. an in-memory demo transport and tests.

The application-specific transport used by the original deployment is deliberately not included. A real CLI can be connected by implementing `PromptTransport`, while transcript parsing stays independent.

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

session = runtime.ensure_session("my-chat")
runtime.send_turn("my-chat", "Inspect the project and explain the failing tests.")

for event in runtime.poll("my-chat"):
    print(event.kind, event.data)
```

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

- [ ] production CLI adapter example
- [ ] subprocess lifecycle helpers
- [ ] SSE/WebSocket event fan-out
- [ ] richer session resume policies
- [ ] pluggable transcript parsers
- [ ] reference frontend activity timeline

## License

MIT.
