"""
External system integrations for CloudByte.

This module contains code for integrating with external systems — currently
Claude Code's JSONL transcripts. The `llm` sub-package (litellm-based
observation/summary generation) was removed: observations are recorded by the
agent itself via the MCP record_observation tool, so nothing here calls an LLM.
"""

# Import sub-modules for convenience
from src.integrations import claude

__all__ = ["claude"]
