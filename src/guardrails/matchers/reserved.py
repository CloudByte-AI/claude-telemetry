"""
Reserved taxonomy names.

`OPERATION` names are a public interface (user profiles, taxonomy.json, audit
rows), so they are reserved here before their matchers exist, which keeps
later matchers additive rather than breaking.

A reserved operation never fires. It appears in taxonomy.json with
`implemented: false`, and a rule naming one is valid but inert. The
`default_action` values are what those matchers WILL ship with; most are `allow`.
"""

from __future__ import annotations

from src.guardrails.registry import reserve_operation
from src.guardrails.toolcall import (
    KIND_DELETE,
    KIND_EDIT,
    KIND_MCP,
    KIND_READ,
    KIND_SHELL,
    KIND_WEB,
    KIND_WRITE,
)

_SHELL = (KIND_SHELL,)
_FILES = (KIND_SHELL, KIND_READ, KIND_WRITE, KIND_EDIT, KIND_DELETE)

# (operation, domain, description, applies_to, default_action, default_alert)
_RESERVED: tuple[tuple[str, str, str, tuple[str, ...], str, str], ...] = (
    # ── fs ────────────────────────────────────────────────────────────────
    ("fs.read", "fs", "Reads a file", _FILES, "allow", "info"),
    ("fs.write", "fs", "Writes or overwrites a file", _FILES, "allow", "info"),
    ("fs.create", "fs", "Creates a new file or directory", _FILES, "allow", "info"),
    ("fs.copy", "fs", "Copies a filesystem path", _SHELL, "allow", "info"),
    ("fs.move", "fs", "Moves or renames a filesystem path", _SHELL, "allow", "info"),
    ("fs.symlink", "fs", "Creates a symbolic or hard link", _SHELL, "allow", "warn"),
    # ── exec ──────────────────────────────────────────────────────────────
    ("exec.background", "exec", "Detaches a process to run in the background", _SHELL, "allow", "info"),
    ("exec.shell.eval", "exec", "Evaluates a dynamically constructed shell string", _SHELL, "ask", "warn"),
    # ── vcs ───────────────────────────────────────────────────────────────
    ("vcs.remote.add", "vcs", "Adds or changes a version-control remote", _SHELL, "ask", "warn"),
    ("vcs.tag.delete", "vcs", "Deletes a tag locally or on a remote", _SHELL, "allow", "warn"),
    ("vcs.config.change", "vcs", "Changes version-control configuration", _SHELL, "allow", "info"),
    ("vcs.submodule", "vcs", "Adds, updates or removes a submodule", _SHELL, "allow", "info"),
    # ── mcp ───────────────────────────────────────────────────────────────
    ("mcp.ungoverned", "mcp", "MCP tool outside the configured allowlist", (KIND_MCP,), "allow", "warn"),
    ("mcp.external_send", "mcp", "MCP tool that sends data to an external service", (KIND_MCP,), "allow", "warn"),
    ("mcp.new_server", "mcp", "First use of a previously unseen MCP server", (KIND_MCP,), "allow", "warn"),
    ("mcp.elevated", "mcp", "MCP tool requesting elevated capability", (KIND_MCP,), "ask", "warn"),
    ("mcp.bulk", "mcp", "MCP tool operating on many records at once", (KIND_MCP,), "allow", "warn"),
    # ── infra ─────────────────────────────────────────────────────────────
    ("infra.secrets.access", "infra", "Reads infrastructure secrets", _SHELL, "ask", "warn"),
    ("infra.docker.prune", "infra", "Prunes container images, volumes or networks", _SHELL, "allow", "warn"),
    # ── net ───────────────────────────────────────────────────────────────
    ("net.download", "net", "Downloads content from a remote host", _SHELL, "allow", "info"),
    ("net.egress.unknown", "net", "Connects to a host not seen before", _SHELL, "allow", "warn"),
    ("net.scan", "net", "Scans hosts or ports", _SHELL, "ask", "warn"),
    # ── db ────────────────────────────────────────────────────────────────
    ("db.grant", "db", "Grants or revokes database privileges", _SHELL, "ask", "warn"),
    # ── sec ───────────────────────────────────────────────────────────────
    ("sec.credential.write", "sec", "Writes a credential to disk or a store", _FILES, "ask", "critical"),
    ("sec.key.generate", "sec", "Generates a cryptographic key", _SHELL, "allow", "info"),
    ("sec.cert.modify", "sec", "Installs or modifies a certificate or trust store", _SHELL, "ask", "critical"),
    # ── sys ───────────────────────────────────────────────────────────────
    ("sys.service.control", "sys", "Starts, stops or restarts a system service", _SHELL, "allow", "warn"),
    ("sys.user.modify", "sys", "Creates, modifies or deletes a system user or group", _SHELL, "ask", "critical"),
    ("sys.env.modify", "sys", "Modifies persistent environment configuration", _SHELL, "allow", "warn"),
    ("sys.kernel.module", "sys", "Loads or unloads a kernel module", _SHELL, "ask", "critical"),
    ("sys.shutdown", "sys", "Shuts down or reboots the machine", _SHELL, "ask", "warn"),
    # ── devflow ───────────────────────────────────────────────────────────
    ("devflow.dependency.unpinned", "devflow", "Adds a dependency without a pinned version", _SHELL, "allow", "info"),
    ("devflow.test.skip", "devflow", "Skips or disables tests", (KIND_SHELL, KIND_WRITE, KIND_EDIT), "allow", "warn"),
    ("devflow.lockfile.bypass", "devflow", "Bypasses or discards a dependency lockfile", _SHELL, "allow", "warn"),
)

# `net.web.*` is deliberately absent: both web operations are implemented.
assert not any(operation.startswith("net.web.") for operation, *_ in _RESERVED)


def register_reserved() -> None:
    """Register every reserved name. Idempotent per process (import-time only)."""
    for operation, domain, description, applies_to, action, alert in _RESERVED:
        reserve_operation(
            operation=operation,
            domain=domain,
            description=description,
            applies_to=applies_to,
            default_action=action,
            default_alert=alert,
        )


register_reserved()
