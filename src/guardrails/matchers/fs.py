"""
Filesystem matchers.

Matchers take a normalised ToolCall, not a command string: on Claude Code a
delete is `Bash` + `rm`, on Cursor a structured `Delete` tool call with no
shell string. One matcher covers both, so one profile rule covers both platforms.
"""

from __future__ import annotations

import posixpath

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import (
    KIND_DELETE,
    KIND_EDIT,
    KIND_READ,
    KIND_SHELL,
    KIND_WRITE,
    ToolCall,
    resolve_path,
)

# Commands that remove things, in either dialect (PowerShell's Remove-Item and
# its aliases already normalise to `rm` in the parser).
_DELETE_COMMANDS = frozenset({"rm", "rmdir", "shred", "srm", "unlink", "wipe"})

# Filename shapes that carry credentials or keys. Matched on the basename or on
# a path segment, never as a bare substring: `environment.ts` must not look like
# a `.env`.
_SENSITIVE_BASENAMES = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".htpasswd",
    "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "known_hosts", "authorized_keys", "secrets.yaml", "secrets.yml",
    "secrets.json", "terraform.tfvars", ".dockercfg", "kubeconfig",
})
_SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".asc", ".ppk",
)
_SENSITIVE_PREFIXES = (".env.", "service-account", "serviceaccount")
# Directory segments whose contents are sensitive regardless of filename.
_SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure"})


def _is_sensitive_path(path: str | None) -> bool:
    if not path:
        return False
    normalised = str(path).replace("\\", "/")
    basename = posixpath.basename(normalised).lower()
    segments = {segment.lower() for segment in normalised.split("/")}

    if basename in _SENSITIVE_BASENAMES:
        return True
    if basename.endswith(_SENSITIVE_SUFFIXES):
        return True
    if basename.startswith(_SENSITIVE_PREFIXES):
        return True
    return bool(segments & _SENSITIVE_DIRS)


def _candidate_paths(call: ToolCall) -> list[tuple[str, str]]:
    """(resolved_path, evidence) for everything this call touches."""
    found: list[tuple[str, str]] = []
    for path in call.file_paths:
        found.append((path, path))
    for command in call.commands:
        for operand in command.subcommand_path:
            if operand.startswith("-"):
                continue
            resolved = resolve_path(operand, call.cwd)
            if resolved:
                found.append((resolved, command.raw))
        for target in command.redirects:
            resolved = resolve_path(target, call.cwd)
            if resolved:
                found.append((resolved, command.raw))
    return found


@register_matcher
class DeleteMatcher(BaseMatcher):
    OPERATION = "fs.delete"
    DOMAIN = "fs"
    DESCRIPTION = "Deletes a filesystem path"
    APPLIES_TO = (KIND_SHELL, KIND_DELETE)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"

    def match(self, call: ToolCall) -> MatchResult | None:
        # Cursor's first-class Delete tool: structured, no shell string.
        if call.kind == KIND_DELETE and call.file_paths:
            return self.hit(target=call.file_paths[0], evidence=call.file_paths[0])

        for command in self.commands_named(call, _DELETE_COMMANDS):
            target = next(
                (resolve_path(o, call.cwd) for o in command.subcommand_path if not o.startswith("-")),
                None,
            )
            return self.hit(target=target, command=command)
        return None


@register_matcher
class RecursiveDeleteMatcher(BaseMatcher):
    OPERATION = "fs.delete.recursive"
    DOMAIN = "fs"
    DESCRIPTION = "Recursive or forced delete of a filesystem path"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("recursive", "forced", "target_count")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, _DELETE_COMMANDS):
            if not (command.has_recursive_flag or command.has_force_flag):
                continue
            targets = [o for o in command.subcommand_path if not o.startswith("-")]
            resolved = resolve_path(targets[0], call.cwd) if targets else None
            return self.hit(
                target=resolved,
                command=command,
                recursive=command.has_recursive_flag,
                forced=command.has_force_flag,
                target_count=len(targets),
            )
        return None


@register_matcher
class SensitiveFileMatcher(BaseMatcher):
    OPERATION = "fs.sensitive"
    DOMAIN = "fs"
    DESCRIPTION = "Touches a credential, key or other sensitive file"
    APPLIES_TO = (KIND_SHELL, KIND_READ, KIND_WRITE, KIND_EDIT, KIND_DELETE)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("sensitive_path",)

    def match(self, call: ToolCall) -> MatchResult | None:
        for path, evidence in _candidate_paths(call):
            if _is_sensitive_path(path):
                # `kind` distinguishes read from write, so ONE matcher serves
                # both and the rule decides which it cares about:
                #   { operation: fs.sensitive, kind: write, decision: ask }
                return self.hit(target=path, evidence=evidence, sensitive_path=path)
        return None


@register_matcher
class PermissionChangeMatcher(BaseMatcher):
    OPERATION = "fs.permission"
    DOMAIN = "fs"
    DESCRIPTION = "Changes file permissions or ownership"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    FACTS = ("world_writable", "recursive")

    _COMMANDS = frozenset({"chmod", "chown", "chgrp", "setfacl", "icacls", "takeown", "attrib"})
    # World-writable or fully-open modes, the cases actually worth surfacing.
    _OPEN_MODES = frozenset({"777", "0777", "666", "0666", "a+w", "a+rwx", "o+w"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._COMMANDS):
            operands = list(command.subcommand_path)
            world_writable = any(o in self._OPEN_MODES for o in operands)
            target = next(
                (resolve_path(o, call.cwd) for o in operands if "/" in o or "\\" in o),
                None,
            )
            return self.hit(
                target=target,
                command=command,
                world_writable=world_writable,
                recursive=command.has_recursive_flag,
            )
        return None
