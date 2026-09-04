"""Provider-specific transcript/event parsers.

The bridge runtime stays provider-neutral. These parsers only translate
provider-native rows into the small public RuntimeEvent vocabulary.
"""

from .claude import ClaudeTranscriptParser
from .codex import CodexExecParser

__all__ = ["ClaudeTranscriptParser", "CodexExecParser"]
