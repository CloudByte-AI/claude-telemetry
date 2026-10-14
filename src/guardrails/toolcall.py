"""
The normalised tool call - the seam that makes this engine platform-neutral.

Every adapter converts its platform's payload into exactly this shape, and
nothing downstream (parser aside) ever sees a platform's own field names.

    shell surface   one opaque string -> parsed into `commands`
    everything else already structured -> `file_paths`, `mcp_tool`, `urls`

Rule matching is PER COMMAND for the shell surface (see rules.py): a rule
matches when at least one parsed command satisfies all of its command-scoped
constraints.
"""

from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from src.guardrails.parser import Command, Dialect, POSIX, parse_chain

# ── Operation kinds ───────────────────────────────────────────────────────────
#
# The coarse vocabulary adapters map tool names onto. The taxonomy
# (fs.delete.recursive, ...) says what specifically.

KIND_SHELL = "shell"
KIND_READ = "read"
KIND_WRITE = "write"
KIND_EDIT = "edit"
KIND_DELETE = "delete"
KIND_SEARCH = "search"
KIND_WEB = "web"
KIND_MCP = "mcp"
KIND_AGENT = "agent"
KIND_OTHER = "other"

ALL_KINDS = frozenset({
    KIND_SHELL, KIND_READ, KIND_WRITE, KIND_EDIT, KIND_DELETE,
    KIND_SEARCH, KIND_WEB, KIND_MCP, KIND_AGENT, KIND_OTHER,
})

# Kinds that write to the filesystem, shared by matchers and the profile.
WRITE_KINDS = frozenset({KIND_WRITE, KIND_EDIT, KIND_DELETE})

_URL = re.compile(r"\b(?:https?|ftp|ssh|git|s3|gs)://([^\s/'\"<>|]+)", re.IGNORECASE)
_SCP_TARGET = re.compile(r"\b(?:[\w.\-]+@)([A-Za-z0-9.\-]+\.[A-Za-z]{2,}|\d{1,3}(?:\.\d{1,3}){3}):")
# A drive letter followed by a separator or nothing: `C:/...`, `C:\...`, `C:`.
# `C:foo` (relative to drive C's current directory) is deliberately not matched
# and stays relative - a lexical resolver cannot know that directory.
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:(?=[\\/]|$)")


def _split_drive(text: str) -> tuple[str, str]:
    """
    (`C:`, `/rest`) for a drive path, (``, text) otherwise.

    The rest always starts with `/`, so normpath treats a bare `C:` as a root.
    """
    if not _WINDOWS_DRIVE.match(text):
        return "", text
    rest = text[2:]
    return text[:2], rest if rest.startswith("/") else "/" + rest


def resolve_path(path: str | None, cwd: str | None) -> str | None:
    """
    Resolve a path for comparison, without touching the filesystem.

    Purely lexical on purpose: the path often does not exist yet (a `Write`
    creates it), and a stat on a network drive can stall the hot path.
    Separators are normalised to `/` so one profile rule works on every
    platform. A drive root resolves to `C:/`, the way the POSIX root resolves
    to `/`, so `C:/..` cannot climb above it.
    """
    if not path:
        return None
    text = str(path).strip().strip('"').strip("'")
    if not text:
        return None

    text = os.path.expanduser(text) if text.startswith("~") else text
    text = text.replace("\\", "/")

    drive, rest = _split_drive(text)
    if not drive and not rest.startswith("/") and cwd:
        base = str(cwd).replace("\\", "/").rstrip("/") or "/"
        drive, rest = _split_drive(posixpath.join(base, rest))

    # With the drive split off the rest is rooted, so `..` cannot climb above
    # the drive root.
    return drive + posixpath.normpath(rest)


def extract_hosts(text: str) -> tuple[str, ...]:
    """Network destinations named in a string - URLs and scp/ssh style targets."""
    if not text:
        return ()
    found: dict[str, None] = {}
    for match in _URL.finditer(text):
        host = match.group(1).split("@")[-1].split(":")[0].lower()
        if host:
            found.setdefault(host, None)
    for match in _SCP_TARGET.finditer(text):
        found.setdefault(match.group(1).lower(), None)
    return tuple(found)


@dataclass(frozen=True)
class ToolCall:
    """
    One tool call, normalised. Matchers only ever see this.

    Construct with `build()` rather than directly - it does the parsing and
    path resolution that every adapter would otherwise repeat.
    """

    platform: str                       # "claude_code" | "cursor"
    tool_name: str                      # "Bash", "Write", "mcp__github__create_issue"
    kind: str                           # one of ALL_KINDS
    hook_event: str                     # the hook that produced this call
    raw_input: dict = field(default_factory=dict)
    cwd: str | None = None
    # The folder the session was opened in - which workspace policy applies
    # (src/guardrails/workspaces.py). Stays put while `cwd` follows the agent's
    # `cd`. None when the platform did not say; the workspace lookup then
    # starts from `cwd`.
    workspace_root: str | None = None
    session_id: str | None = None
    prompt_id: str | None = None        # the user prompt this call belongs to
    tool_use_id: str | None = None
    permission_mode: str = "default"
    agent_id: str | None = None         # set when the call came from a subagent
    agent_type: str | None = None

    # shell surface
    command: str = ""                   # the raw string, exactly as written
    commands: tuple[Command, ...] = ()  # the parsed chain
    dialect: str = POSIX.name

    # file surface
    file_paths: tuple[str, ...] = ()    # resolved, normalised
    content: str | None = None          # Write payload - scanned, never stored

    # mcp surface
    mcp_server: str | None = None       # "mcp__github__"
    mcp_tool: str | None = None         # "create_issue"

    # web surface
    urls: tuple[str, ...] = ()

    # ── derived ───────────────────────────────────────────────────────────

    @property
    def command_names(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for command in self.commands:
            seen.setdefault(command.command_name, None)
        return tuple(seen)

    @property
    def target_path(self) -> str | None:
        """
        The single path this call is most about - what rules scope on.

        For file tools that is the file. For a shell command it is the first
        operand that looks like a path, which is a heuristic: a matcher that
        needs precision reports its own `target` in the MatchResult instead.
        """
        if self.file_paths:
            return self.file_paths[0]
        for command in self.commands:
            for operand in command.subcommand_path:
                if operand and not operand.startswith("-"):
                    return resolve_path(operand, self.cwd)
        return None

    @property
    def extension(self) -> str | None:
        target = self.target_path
        if not target:
            return None
        _, ext = posixpath.splitext(target)
        return ext.lower() or None

    @property
    def hosts(self) -> tuple[str, ...]:
        found: dict[str, None] = {}
        for url in self.urls:
            for host in extract_hosts(url):
                found.setdefault(host, None)
        for host in extract_hosts(self.command):
            found.setdefault(host, None)
        return tuple(found)

    @property
    def has_force_flag(self) -> bool:
        return any(c.has_force_flag for c in self.commands)

    @property
    def has_recursive_flag(self) -> bool:
        return any(c.has_recursive_flag for c in self.commands)

    @property
    def is_destructive(self) -> bool:
        return any(c.is_destructive for c in self.commands)

    @property
    def is_network_call(self) -> bool:
        return any(c.is_network_call for c in self.commands)

    @property
    def is_subagent(self) -> bool:
        return bool(self.agent_id)

    @property
    def subject(self) -> dict:
        """
        What this call acts on, independent of how the platform packaged it.

        Not `raw_input`: Cursor's beforeShellExecution and beforeReadFile send
        no tool_input. Built from normalised fields, the same call hashes the
        same on both platforms (db_writer relies on that to link a Cursor event
        to its tool call). A Write's content and a Read's offset are left out.
        """
        if self.kind == KIND_SHELL:
            return {"command": self.command}
        if self.kind == KIND_MCP:
            return {"mcp_tool": self.mcp_tool, "input": self.raw_input}
        if self.urls:
            return {"urls": list(self.urls)}
        if self.file_paths:
            return {"paths": list(self.file_paths)}
        return {"input": self.raw_input}

    @property
    def subject_hash(self) -> str | None:
        """SHA256 of `subject` - stored as tool_input_hash; the input itself never is."""
        try:
            import hashlib
            import json
            payload = json.dumps(self.subject, sort_keys=True, default=str)
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()
        except Exception:
            return None

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        *,
        platform: str,
        tool_name: str,
        kind: str,
        hook_event: str,
        raw_input: dict[str, Any] | None = None,
        cwd: str | None = None,
        workspace_root: str | None = None,
        session_id: str | None = None,
        prompt_id: str | None = None,
        tool_use_id: str | None = None,
        permission_mode: str = "default",
        agent_id: str | None = None,
        agent_type: str | None = None,
        command: str | None = None,
        dialect: Dialect | str | None = None,
        file_paths: tuple[str, ...] | list[str] | None = None,
        content: str | None = None,
        mcp_server: str | None = None,
        mcp_tool: str | None = None,
        urls: tuple[str, ...] | list[str] | None = None,
    ) -> "ToolCall":
        """
        Normalise one platform payload. Never raises.

        A malformed field yields a ToolCall with that field empty rather than an
        exception, because this runs in front of every governed tool call.
        """
        resolved_dialect = dialect if isinstance(dialect, Dialect) else None
        if resolved_dialect is None:
            from src.guardrails.parser import get_dialect
            resolved_dialect = get_dialect(dialect if isinstance(dialect, str) else None)

        command_text = command or ""
        try:
            commands = parse_chain(command_text, resolved_dialect) if command_text else ()
        except Exception:
            commands = ()

        resolved_paths: list[str] = []
        for path in (file_paths or ()):
            resolved = resolve_path(path, cwd)
            if resolved and resolved not in resolved_paths:
                resolved_paths.append(resolved)

        return cls(
            platform=platform,
            tool_name=tool_name or "",
            kind=kind if kind in ALL_KINDS else KIND_OTHER,
            hook_event=hook_event,
            raw_input=raw_input or {},
            cwd=cwd,
            workspace_root=workspace_root or None,
            session_id=session_id,
            prompt_id=prompt_id,
            tool_use_id=tool_use_id,
            permission_mode=permission_mode or "default",
            agent_id=agent_id,
            agent_type=agent_type,
            command=command_text,
            commands=commands,
            dialect=resolved_dialect.name,
            file_paths=tuple(resolved_paths),
            content=content,
            mcp_server=mcp_server,
            mcp_tool=mcp_tool,
            urls=tuple(urls or ()),
        )


def split_mcp_tool_name(tool_name: str) -> tuple[str | None, str | None]:
    """
    Split `mcp__github__create_issue` into ("mcp__github__", "create_issue").

    Plugin-bundled servers (`mcp__plugin_<plugin>_<server>__<tool>`) split the
    same way. Returns (None, None) for a non-MCP name.
    """
    if not tool_name or not tool_name.startswith("mcp__"):
        return None, None
    remainder = tool_name[len("mcp__"):]
    server, separator, tool = remainder.partition("__")
    if not separator:
        # `mcp__something` with no tool segment - treat the whole thing as the server.
        return f"mcp__{remainder}__", None
    return f"mcp__{server}__", tool or None
