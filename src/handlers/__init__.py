"""
CloudByte Hook Handlers

Each handler corresponds to a Claude Code hook event.

Imports are lazy (a module __getattr__), so touching src.handlers does not
load every handler and its heavy dependencies on the guardrails hot path.
"""

import os

# This plugin's Claude Code hook registration.
HOOKS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "hooks", "hooks.json",
)

__all__ = [
    "handle_session_start",
    "handle_user_prompt",
    "handle_session_end",
    "handle_pre_tool_use",
]

_EXPORTS = {
    "handle_session_start": "src.handlers.session_start",
    "handle_user_prompt": "src.handlers.user_prompt",
    "handle_session_end": "src.handlers.session_end",
    "handle_pre_tool_use": "src.handlers.pre_tool_use",
}


def __getattr__(name: str):
    """PEP 562 lazy attribute access - the submodule is imported on first use."""
    module_path = _EXPORTS.get(name)
    if module_path is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    return getattr(importlib.import_module(module_path), name)


def __dir__():
    return sorted(__all__)
