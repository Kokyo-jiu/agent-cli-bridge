# agent-cli-bridge

Turn CLI agents into persistent, streaming conversational backends.

`agent-cli-bridge` extracts a small runtime pattern for applications that want to use a coding/agent CLI as more than a one-shot command:

- keep a logical session across many frontend turns;
- send only the new user turn instead of rebuilding the entire conversation every time;
- normalize reasoning summaries, assistant text, tool calls, tool results, turn boundaries, and errors into one event stream;
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
   └── TranscriptTailer / provider parser
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
5. provider parsers for Claude-style content-block transcripts and Codex `exec --json` events;
6. a runtime coordinator that keeps a turn alive through tool activity;
7. a long-lived subprocess transport and runnable end-to-end demo;
8. runnable provider-parser examples;
9. tests across Python 3.10–3.12.

The application-specific transport used by the original deployment is deliberately not included. Real CLIs can be connected through `PromptTransport`, while transcript/event parsing stays independent.

## Quick start

```python
from agent_cli_bridge import (
    BridgeRuntime,
    InMemoryTransport,
    SessionRegistry,
    TranscriptTailer,
    ClaudeTranscriptParser,
)

registry = SessionRegistry()
transport = InMemoryTransport()
tailer = TranscriptTailer(
    path="/path/to/session.jsonl",
    parser=ClaudeTranscriptParser(),
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

## Provider parsers

The runtime stays provider-neutral; `agent_cli_bridge.providers` contains small adapters for provider-native event shapes.

### Claude-style transcripts

`ClaudeTranscriptParser` parses common content-block transcript rows:

- assistant `thinking` blocks → `thinking`
- assistant `text` blocks → `assistant_delta`
- `tool_use` → `tool_start`
- `tool_result` → `tool_result`
- an explicit stop/terminal marker → `turn_end`

The original `ClaudeStyleTranscriptParser` import remains available for compatibility.

Run the parser example:

```bash
python examples/claude_provider_events.py
```

### Codex `exec --json`

`CodexExecParser` targets the public Codex CLI JSONL event model used by `codex exec --json`:

```text
thread.started
turn.started
item.started / item.updated / item.completed
turn.completed / turn.failed
error
```

Current canonical item types include agent messages, reasoning summaries, command execution, file changes, MCP tool calls, collaboration tool calls, web search, to-do lists, and non-fatal error items.

The parser normalizes the useful lifecycle pieces while preserving unknown/additive events as `raw`:

```text
thread.started                  → session_switched
item.completed: reasoning       → thinking
item.completed: agent_message   → assistant_delta
item.started: command/tool      → tool_start
item.completed: command/tool    → tool_result
turn.completed                  → turn_end
turn.failed / fatal error       → turn_error
```

Codex `reasoning` is treated as provider-exposed reasoning summary/status text. The parser does not attempt to expose hidden chain of thought.

Run the parser example:

```bash
python examples/codex_provider_events.py
```

Provider event schemas can evolve. The Codex adapter intentionally preserves unknown top-level and item events as `raw` rather than silently discarding them.


### Codex app-server sessions (v0.2, recommended for persistent Codex)

`CodexExecParser` remains supported for existing `codex exec --json` integrations. For new persistent Codex integrations, v0.2 adds the app-server session transport that the original deployment has used in production since July 2026.

It talks directly to `codex app-server --stdio --strict-config` and supports:

- persisted `thread/start` seed sessions;
- isolated `thread/fork` child turns;
- `turn/start` streaming notifications;
- caller-owned `baseInstructions` with `developerInstructions` explicitly empty;
- empty dynamic-tool/capability-root lists on new threads;
- MCP/apps/plugins/multi-agent and other extension families disabled at process startup;
- candidate-child semantics: the parent is never mutated by a foreground turn;
- clean-sibling assistant-history commits via `thread/inject_items`, without a second model turn;
- local subscription auth through the installed Codex runtime; no API key is required by this package.

Minimal seed turn:

```python
from agent_cli_bridge import CodexSessionSpec, run_session_turn

spec = CodexSessionSpec(
    mode="seed",
    cwd="/absolute/path/to/clean/workspace",
    system_file="/absolute/path/to/system.txt",
)

candidate = run_session_turn(spec, "hello", emit=print)
```

Continue from an accepted parent with `mode="fork"` and `parent_thread_id=<uuid>`. The returned child is only a **candidate**; applications should persist the assistant result first and then atomically promote that child in their own session ledger.

A runnable command-line example is included:

```bash
python examples/codex_app_server_turn.py \
  --cwd /absolute/clean/workspace \
  --system-file /absolute/system.txt \
  "hello"
```

#### Migrating from the v0.1 Codex CLI path

The v0.1 `CodexExecParser` / `codex exec --json` path is kept for compatibility. Migration does not require deleting it:

```text
v0.1: frontend -> BridgeRuntime -> CLI subprocess -> codex exec --json
v0.2: frontend/session ledger -> Codex app-server provider -> thread/start|fork -> turn/start
```

The two paths can coexist while an application moves conversations to app-server threads. Do not reuse a CLI process id as an app-server thread id; store the provider-native thread id separately.

#### Near-bare boundary

v0.2.1 moves the near-bare suppression into the per-thread `thread/start` / `thread/fork` config, because process-level feature disables alone are not enough. The provider now suppresses Codex-added skills, collaboration, environment, apps, permissions, and multi-agent hint fragments at thread scope while keeping caller-owned `baseInstructions`, an explicit empty `developerInstructions`, empty `dynamicTools` / `selectedCapabilityRoots` for seeds, and empty MCP configuration.

This was verified against Codex 0.148.0 by capturing the actual local Responses request built by app-server. With the v0.2.1 config, the request contained no `tools` key and no Codex-added skills/team/multi-agent/environment instruction fragments; the model-visible conversation contained only the caller base instructions, retained conversation history, and the new user turn. The same property was verified across seed, clean-sibling `thread/inject_items` history commit, and the next fork.

That guarantee is version-pinned, not timeless. Codex prompt/tool assembly can change between releases, so rerun the request-capture probe when upgrading Codex instead of assuming a future version has the same surface.

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
- [x] Claude-style and Codex provider parser examples
- [ ] SSE/WebSocket event fan-out
- [ ] richer session resume policies
- [ ] additional pluggable provider parsers
- [ ] reference frontend activity timeline

## License

MIT.
