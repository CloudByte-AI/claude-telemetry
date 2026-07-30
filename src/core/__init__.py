"""
Core business logic for CloudByte.

Session and prompt record creation for the Claude Code hooks. The LLM-based
observation generation that used to live here is gone — observations come from
the agent via the MCP record_observation tool (see src/observations/).
"""

from src.core.event_processor import (
    EventProcessor,
    process_session_start,
    process_user_prompt,
)

__all__ = [
    "EventProcessor",
    "process_session_start",
    "process_user_prompt",
]
