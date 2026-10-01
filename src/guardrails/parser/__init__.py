"""
Command chain parsing.

`tool_input["command"]` is a shell string, not a command. A naive
`command.startswith("rm")` is defeated by chaining, leading assignments,
substitution, absolute paths and split flags; the evasion corpus in
tests/guardrails/test_command_scanner.py is the specification.

Two dialects share one scanner:

    posix       bash / sh / zsh - Claude Code's Bash tool, and Cursor's Shell
                tool on macOS and Linux
    powershell  Cursor's Shell tool on Windows

The dialect is chosen at the adapter edge and passed in. Nothing here inspects
the host platform, so the same payload parses identically on every machine.
"""

from src.guardrails.parser.command_scanner import (
    Command,
    Dialect,
    POSIX,
    command_names,
    get_dialect,
    parse_chain,
    register_dialect,
)

# Importing the module registers the dialect, which is what makes
# get_dialect("powershell") resolve.
from src.guardrails.parser.powershell_dialect import POWERSHELL, looks_like_powershell


def sniff_dialect(command: str, default: Dialect = POSIX) -> Dialect:
    """
    Guess a dialect from command text when the caller has no better signal.

    A fallback only: returns `default` unless the text carries a
    PowerShell-only shape.
    """
    return POWERSHELL if looks_like_powershell(command) else default


__all__ = [
    "Command",
    "Dialect",
    "POSIX",
    "POWERSHELL",
    "command_names",
    "get_dialect",
    "looks_like_powershell",
    "parse_chain",
    "register_dialect",
    "sniff_dialect",
]
