"""Claude-style transcript adapter.

This module gives the existing content-block parser a provider-specific import
path without breaking the original public import.
"""

from ..core import ClaudeStyleTranscriptParser


class ClaudeTranscriptParser(ClaudeStyleTranscriptParser):
    """Parse Claude-style transcript rows into normalized RuntimeEvents."""


__all__ = ["ClaudeTranscriptParser", "ClaudeStyleTranscriptParser"]
