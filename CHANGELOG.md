# Changelog

All notable changes to `agent-cli-bridge` are documented here.

## [0.2.1] - 2026-09-14

### Codex near-bare thread assembly

- Moved instruction/context suppression into per-thread app-server config; process-level feature disables alone did not remove every model-visible fragment.
- Suppressed skills, collaboration, environment, apps, permissions, root-team and multi-agent mode hints for `thread/start` and `thread/fork`.
- Applied the same configuration to clean-sibling assistant-history commits.
- Verified against Codex 0.148.0 with a local Responses request capture: the final request omitted the `tools` key and contained no Codex-added skills/team/multi-agent/environment fragments.
- Verified the seed -> clean-sibling history commit -> next-fork lineage without relying on subscription quota.

## [0.2.0] - 2026-09-14

### Codex app-server provider

- Added the production-proven Codex app-server session transport used by the original deployment.
- Added persisted `thread/start` seeds and isolated `thread/fork` candidate turns.
- Added `turn/start` streaming-event translation and compatibility with the existing Codex parser.
- Added clean-sibling assistant-history commits through `thread/inject_items` without another model turn.
- Added caller-owned `baseInstructions`, explicit empty `developerInstructions`, empty dynamic tools/capability roots on seed, strict config, empty MCP config, and disabled optional extension families.
- Kept the v0.1 `codex exec --json` path intact for compatibility and documented migration rather than silently replacing it.
- Documented the important near-bare boundary: fail-closed native-tool handling is not the same as proving an empty model-visible core tool registry.

### Compatibility

- `CodexExecParser` now understands app-server assistant deltas, reasoning `summary` fields, and app-server failure messages while preserving the existing CLI event behavior.

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
