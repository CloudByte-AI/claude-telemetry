"""
Observation handling module.

Observations are authored by the agent itself and delivered through the MCP
`record_observation` tool, then persisted to HOOK_OBSERVATION:

    Claude Code  — src/main.py's stop hook reads the tool calls out of the JSONL
    Cursor       — src/cursor/handlers/after_mcp_execution.py parses tool_input

Both paths land on save_observation() below. The legacy inline `<obs>` block
parser (extractor.py / hook_handler.py) and the LLM task queue that predated the
MCP tool have been removed.
"""

from src.observations.writer import (
    save_observation,
    get_session_observations,
    cleanup_duplicate_observations,
)

__all__ = [
    "save_observation",
    "get_session_observations",
    "cleanup_duplicate_observations",
]
