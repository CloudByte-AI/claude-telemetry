"""
Tool Guardrails - policy, audit and approval layer for agent tool calls.

This package is COMMON to every platform. Nothing under src/guardrails/ may
import from src.handlers or src.cursor, and nothing here may branch on which
platform is calling. Platform differences live in the thin adapters
(src/handlers/pre_tool_use.py, src/cursor/handlers/guardrails.py) - for
example, Cursor shows no prompt for `ask`, so its adapter renders ask as an
allow with a warning.

    stdin JSON -> ToolCall (normalise) -> evaluate() -> Decision -> platform JSON
"""
