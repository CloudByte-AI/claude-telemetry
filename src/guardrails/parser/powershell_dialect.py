"""
PowerShell dialect.

An agent on Windows may drive PowerShell (Cursor's Shell tool does). Without
this dialect, `Remove-Item -Recurse -Force dist` would not match an `rm` rule
and the guardrail would fail silently.

Five token rules differ from POSIX (see `Dialect`), and cmdlets and aliases are
normalised to canonical POSIX names so ONE profile rule covers both shells:

    rm -rf dist                      -> command_name "rm", flags {-r, -f}
    Remove-Item -Recurse -Force dist -> command_name "rm", flags {-recurse, -force}

`has_force_flag` and `has_recursive_flag` recognise both flag spellings.
"""

from __future__ import annotations

import re

from src.guardrails.parser.command_scanner import Dialect, register_dialect

# Cmdlets and aliases mapped to their POSIX equivalents. Only mappings whose
# RISK semantics genuinely match are listed: a wrong entry makes a rule fire on
# something it was never written for. `Get-Item` is absent for that reason (it
# reads metadata, not content).
ALIASES: dict[str, str] = {
    # ── delete ────────────────────────────────────────────────────────────
    "remove-item": "rm", "ri": "rm", "del": "rm", "erase": "rm",
    "rd": "rm", "rmdir": "rm", "remove-itemproperty": "rm",
    # ── read / list ───────────────────────────────────────────────────────
    "get-content": "cat", "gc": "cat", "type": "cat",
    "get-childitem": "ls", "gci": "ls", "dir": "ls",
    # ── write / copy / move ───────────────────────────────────────────────
    "set-content": "tee", "out-file": "tee", "add-content": "tee", "ac": "tee",
    "copy-item": "cp", "copy": "cp", "cpi": "cp",
    "move-item": "mv", "move": "mv", "mi": "mv",
    "new-item": "touch", "ni": "touch",
    # ── network ───────────────────────────────────────────────────────────
    "invoke-webrequest": "curl", "iwr": "curl", "wget": "curl",
    "invoke-restmethod": "curl", "irm": "curl",
    "start-bitstransfer": "curl",
    # ── process / service ─────────────────────────────────────────────────
    "stop-process": "kill", "spps": "kill",
    "stop-service": "systemctl", "start-service": "systemctl",
    "restart-service": "systemctl", "set-service": "systemctl",
    # ── search ────────────────────────────────────────────────────────────
    "select-string": "grep", "sls": "grep",
    # ── code execution ────────────────────────────────────────────────────
    "invoke-expression": "eval", "iex": "eval",
    "start-process": "exec", "saps": "exec",
}


POWERSHELL: Dialect = register_dialect(Dialect(
    name="powershell",
    # Backtick is the escape character, so it does NOT substitute as in POSIX.
    escape_char="`",
    backtick_substitutes=False,
    # No inline `FOO=bar cmd` form; PowerShell writes `$env:FOO="bar"; cmd`,
    # which the scanner already splits on `;`.
    allows_env_prefix=False,
    # `&&` and `||` are PowerShell 7+. On 5.1 they are a syntax error, so
    # splitting on them is never wrong.
    operators=("&&", "||", ";", "|", "\n"),
    substitution_prefixes=("$(", "@("),
    # PowerShell flags and paths are case-insensitive; POSIX ones are not.
    case_insensitive_flags=True,
    case_insensitive_names=True,
    executable_suffixes=(".exe", ".cmd", ".bat", ".ps1", ".com"),
    aliases=ALIASES,
))


# Shapes that occur in PowerShell and not in POSIX.
_MARKERS = re.compile(
    r"(?:^|[\s;|(])(?:"
    r"\$env:"                                    # $env:PATH
    r"|\$[A-Za-z_][A-Za-z0-9_]*\s*="             # $var = ...
    r"|(?:Get|Set|New|Remove|Start|Stop|Invoke|Add|Copy|Move|Select|Out|Write|Test|Restart|Clear)-[A-Za-z]+"
    r"|-(?:Recurse|Force|ErrorAction|LiteralPath|Confirm)\b"
    r")",
    re.IGNORECASE,
)


def looks_like_powershell(command: str) -> bool:
    """
    True when the command text carries a PowerShell-only shape.

    Deliberately one-directional: POSIX syntax is also valid PowerShell input,
    so ambiguous text stays POSIX. An adapter that knows the platform's shell
    should pass the dialect explicitly instead.
    """
    return bool(_MARKERS.search(command or ""))
