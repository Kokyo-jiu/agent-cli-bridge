# Changelog

All notable changes to `agent-cli-bridge` are documented here.

## [0.1.0] - 2026-09-05

Initial public release.

### Runtime core

- Persistent logical sessions across frontend turns.
- Thin-turn delivery: each turn sends only the new user message instead of replaying the full conversation.
- Normalized runtime events for session lifecycle, reasoning summaries, assistant output, tool calls, tool results, terminal states, errors, and raw provider events.
- Tool continuation: tool use does not end a turn; the bridge stays active until the provider emits a real terminal signal.
- Durable JSONL transcript tailing with byte-offset recovery.
- Provider-neutral transport and parser boundaries.

### Process adapters

- Long-lived `SubprocessPromptTransport` with one subprocess per logical bridge session.
- JSONL and plain-line input modes.
- Runnable end-to-end subprocess example demonstrating process reuse across multiple turns.

### Provider parsers

- `ClaudeTranscriptParser` for Claude-style content-block transcripts.
- Backward-compatible `ClaudeStyleTranscriptParser` import.
- `CodexExecParser` for the public Codex CLI `exec --json` event stream.
- Codex normalization for agent messages, reasoning summaries, command execution, file changes, MCP tools, collaboration tools, web search, turn completion, and terminal errors.
- Unknown/additive provider events are preserved as `raw` instead of silently discarded.

### Quality

- Runnable Claude and Codex provider examples.
- Automated test coverage on Python 3.10, 3.11, and 3.12.
- MIT license.

### Scope and privacy

The public project contains only the reusable runtime architecture. Application persona, private memory data, private prompts, credentials, deployment endpoints, internal routing, and private infrastructure remain outside the repository.
