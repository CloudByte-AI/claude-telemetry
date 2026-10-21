"""
Self-protection - the agent may not change its own guardrails, nor the
security scanning profiles (~/.cloudbyte/security/), which it could otherwise
edit to stop its prompts being scanned.

Checked before any profile, whenever a guardrails policy is active, and not
something a profile can turn off: a rule that let the agent rewrite the policy
files would let it lift every other rule. Reading them stays allowed.

What counts as a change to ~/.cloudbyte/guardrails/ or ~/.cloudbyte/security/:
    - a Write / Edit / Delete (or Cursor's equivalents) on a path inside it
    - a shell command that redirects output into it (`> file`, `>> file`)
    - a shell command that changes files (rm, mv, cp, tee, sed, Set-Content,
      ...) or runs an interpreter (python, node, powershell, ...) and names a
      path inside it - by any spelling, including `$HOME/.cloudbyte/...` or
      `%USERPROFILE%\\.cloudbyte\\...`, which a lexical resolver cannot expand

Best effort by nature: a command can build the path at run time. A file the
organisation manages is put back when it is found changed, and the change is
reported, so a managed policy cannot be lifted quietly either way.
"""

from __future__ import annotations

import re

from src.common.paths import get_guardrails_dir, get_security_dir
from src.guardrails.decision import DENY, SCOPE_BUILTIN, SOURCE_POLICY, WARN, Decision
from src.guardrails.toolcall import KIND_SHELL, WRITE_KINDS, ToolCall, resolve_path

RULE_ID = "cb.self.guardrails-files"

REASON = (
    "Guardrails and security scanning policy files can only be changed by you or your "
    "organisation, not by the agent. Edit the file yourself, or ask your admin to change "
    "your organisation's policy."
)

# Commands that change files, and interpreters that can (`python -c "open(...)"`).
# Lowercase; command names are compared lowercased.
_CHANGING_COMMANDS = frozenset({
    "rm", "rmdir", "unlink", "del", "erase", "rd", "mv", "move", "ren", "rename",
    "cp", "copy", "xcopy", "robocopy", "install", "ln", "mkdir", "md", "touch",
    "truncate", "tee", "sed", "dd", "chmod", "chown", "icacls", "attrib", "takeown",
    "remove-item", "move-item", "copy-item", "rename-item", "new-item", "set-content",
    "add-content", "out-file", "clear-content", "set-itemproperty",
    "ri", "mi", "cpi", "rni", "ni", "sc", "ac", "clc",
})
_INTERPRETERS = frozenset({
    "python", "python3", "py", "pythonw", "node", "deno", "bun", "ruby", "perl", "php",
    "bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh",
})

# `.cloudbyte/guardrails` or `.cloudbyte\security` (and so on), however the
# home part is spelled.
_GUARDRAILS_MENTION = re.compile(
    r"\.cloudbyte[\\/]+(?:guardrails|security)(?:[\\/]|$|[\s\"'])", re.IGNORECASE)


def _protected_roots() -> list[str]:
    from src.guardrails.workspaces import normalise_root
    roots = (normalise_root(str(get_guardrails_dir())), normalise_root(str(get_security_dir())))
    return [root for root in roots if root]


def _inside(path: str | None, root: str) -> bool:
    from src.guardrails.workspaces import normalise_root
    normalised = normalise_root(path)
    return bool(normalised) and (normalised == root or normalised.startswith(root.rstrip("/") + "/"))


def _deny(target: str | None) -> Decision:
    return Decision(
        action=DENY,
        reason=REASON,
        alert_level=WARN,
        rule_id=RULE_ID,
        source=SOURCE_POLICY,
        policy_scope=SCOPE_BUILTIN,
        target=target,
    )


def check(call: ToolCall) -> Decision | None:
    """A deny when this call would change the protected policy files, else None. Never raises."""
    try:
        roots = _protected_roots()
        if not roots:
            return None

        def inside_any(path: str | None) -> bool:
            return any(_inside(path, root) for root in roots)

        if call.kind in WRITE_KINDS:
            for path in call.file_paths:
                if inside_any(path):
                    return _deny(path)
            return None

        if call.kind != KIND_SHELL:
            return None

        for command in call.commands:
            for target in command.redirects:
                resolved = resolve_path(target, call.cwd)
                if inside_any(resolved) or _GUARDRAILS_MENTION.search(target or ""):
                    return _deny(resolved or target)

            name = (command.command_name or "").lower()
            if name.endswith(".exe"):
                name = name[:-4]
            if name not in _CHANGING_COMMANDS and name not in _INTERPRETERS:
                continue
            for operand in command.subcommand_path:
                resolved = resolve_path(operand, call.cwd)
                if inside_any(resolved):
                    return _deny(resolved)
            if _GUARDRAILS_MENTION.search(command.raw or ""):
                return _deny(None)
        return None
    except Exception:
        return None
