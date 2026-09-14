"""Provider-specific adapters for agent-cli-bridge.

The bridge runtime stays provider-neutral. This package contains event parsers
and direct provider transports such as the Codex app-server session adapter.
"""

from .claude import ClaudeTranscriptParser
from .codex import CodexExecParser
from .codex_app_server import (
    APP_SERVER_COMMAND,
    CodexAppServerError,
    CodexAppServerParentUnavailable,
    CodexAppServerProtocolError,
    CodexAppServerTurnError,
    CodexSessionSpec,
    SubprocessJsonRpcTransport,
    commit_assistant_history,
    run_session_turn,
)

__all__ = [
    "ClaudeTranscriptParser",
    "CodexExecParser",
    "APP_SERVER_COMMAND",
    "CodexAppServerError",
    "CodexAppServerParentUnavailable",
    "CodexAppServerProtocolError",
    "CodexAppServerTurnError",
    "CodexSessionSpec",
    "SubprocessJsonRpcTransport",
    "commit_assistant_history",
    "run_session_turn",
]
