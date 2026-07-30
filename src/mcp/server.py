#!/usr/bin/env python3
"""
CloudByte MCP Server - Observation Capture

Exposes a single MCP tool: record_observation

Claude calls this tool as routine background telemetry after every response
where it used tools or made meaningful changes. The tool simply acknowledges
receipt - observations are visible in the JSONL transcript for inspection.

Start command (declared in plugin.json mcpServers):
    uv run --directory "${CLAUDE_PLUGIN_ROOT}" python -m src.mcp.server
"""
import os
import json
import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# ── Client detection (must run before the logger is set up, so the log
#    directory itself can be routed per-client) ────────────────────────────
def _detect_client() -> str:
    """Identify the connecting client.

    Checks CLOUDBYTE_MCP_CLIENT first - an explicit marker we set ourselves
    in mcp.json's "env" block, so it's present on every spawn regardless of
    whether the host app forwards its own CURSOR_*/CLAUDE_* env vars into
    the child process (confirmed unreliable - real Cursor-spawned processes
    sometimes carry no CURSOR_* vars at all). Falls back to the ambient
    env-var guess only when that marker is absent (e.g. manual/direct runs).
    """
    explicit = os.environ.get("CLOUDBYTE_MCP_CLIENT", "").strip().lower()
    if explicit == "cursor":
        return "Cursor"
    if explicit == "claude":
        return "Claude Code"

    has_claude_env = any("CLAUDE" in k.upper() for k in os.environ)
    has_cursor_env = any("CURSOR" in k.upper() for k in os.environ)
    if has_claude_env and not has_cursor_env:
        return "Claude Code"
    if has_cursor_env and not has_claude_env:
        return "Cursor"
    if has_claude_env and has_cursor_env:
        return "ambiguous (both CLAUDE_* and CURSOR_* env vars present)"
    return "unknown (no CLAUDE_* or CURSOR_* env vars found)"


_DETECTED_CLIENT = _detect_client()


def get_logs_dir() -> Path:
    """Return the CloudByte logs directory, routed per-client.

    Both IDEs' own hook handlers already log to their own subfolder
    (src/cursor/utils/paths.py's get_cursor_logs_dir -> logs/cursor/,
    src/common/paths.py's get_claude_logs_dir -> logs/claude/) - mirrored
    here so the MCP server's logs land in the same structure instead of a
    shared top-level logs/ directory. Only a confidently detected client is
    routed to its subfolder; ambiguous/unknown stays on the shared default
    so a misdetection can't misplace real logs from either IDE. Path is
    inlined rather than imported from src.common.paths/src.cursor.utils.paths
    so server.py stays stdlib-only (see start_mcp.py's fallback mode).
    """
    cloudbyte_dir = Path.home() / ".cloudbyte"
    logs_dir = cloudbyte_dir / "logs"
    if _DETECTED_CLIENT == "Cursor":
        logs_dir = logs_dir / "cursor"
    elif _DETECTED_CLIENT == "Claude Code":
        logs_dir = logs_dir / "claude"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


# ── Constants ──────────────────────────────────────────────────────────────────

_SERVER_NAME      = "cloudbyte-obs"
_SERVER_VERSION   = "1.0.0"

# MCP protocol versions this server can speak, newest first. The wire
# format this server actually uses (initialize/tools:list/tools:call/ping)
# has stayed compatible across all of these dated revisions, so claiming
# support for the full set is safe rather than aspirational.
_SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
_LATEST_PROTOCOL_VERSION = _SUPPORTED_PROTOCOL_VERSIONS[0]


def _negotiate_protocol_version(requested: Any) -> str:
    """Choose the protocolVersion to return from an initialize response.

    Per the MCP spec's version negotiation rule: if the client's requested
    version is one this server supports, echo it back exactly so both
    sides agree on the same version; otherwise fall back to this server's
    latest supported version (the spec's recommended fallback), never to
    an arbitrary/unsupported value.

    This replaces a previous hardcoded "2024-11-05" reply that ignored
    whatever the client actually asked for. A real capture (2026-07-13)
    showed Cursor requesting "2025-11-25" while this server kept replying
    "2024-11-05" - a mismatch that coincided with Cursor never proceeding
    past initialize to tools/list in that session.
    """
    if requested in _SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return _LATEST_PROTOCOL_VERSION


# ── MCP Logger setup ───────────────────────────────────────────────────────────

def _setup_mcp_logger() -> logging.Logger:
    """Setup separate MCP log file - mcp-YYYY-MM-DD.log"""
    log_dir = get_logs_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    current_date = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"mcp-{current_date}.log"

    logger = logging.getLogger("mcp.server")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(handler)
    return logger

_log = _setup_mcp_logger()


# ── Tool schema ────────────────────────────────────────────────────────────────

_OBS_EXAMPLE: dict = {
    "type": "bugfix",
    "title": "Fixed null pointer crash in auth middleware",
    "subtitle": (
        "Requests without an Authorization header now fall through to the anonymous path "
        "instead of crashing."
    ),
    "narrative": (
        "Added a None guard before token parsing in src/auth/middleware.py so a request "
        "carrying no Authorization header no longer raises. The guard routes those requests "
        "down the existing anonymous path, leaving authenticated flows untouched. This "
        "removes a 500 that any unauthenticated client could trigger on every endpoint."
    ),
    "facts": [
        "Guarded the token lookup in src/auth/middleware.py against a missing header",
        "Unauthenticated requests now route to the existing anonymous path",
        "Added a regression test covering a request with no Authorization header",
        "Left the authenticated code path and token validation logic unchanged",
    ],
    "concepts": ["null-guard", "auth-middleware", "request-validation", "regression-test"],
    "files_read": ["src/auth/middleware.py", "tests/test_auth.py"],
    "files_modified": ["src/auth/middleware.py", "tests/test_auth.py"],
}

_OBS_EXAMPLE_NO_WRITES: dict = {
    "type": "discovery",
    "title": "Mapped session token refresh flow across auth and api layers",
    "subtitle": (
        "Traced where refresh tokens are minted, cached and rotated, and found no single "
        "owner of expiry."
    ),
    "narrative": (
        "Read the auth and api layers to establish how a refresh token travels from mint to "
        "rotation, following it through src/auth/tokens.py and src/api/session.py. Expiry is "
        "currently set in two places that can disagree, and no module owns the value. "
        "Recorded so the upcoming rotation change starts from the real flow, not a guess."
    ),
    "facts": [
        "Refresh tokens are minted in src/auth/tokens.py and cached in src/api/session.py",
        "Expiry is set independently in both modules and the two values can diverge",
        "No module currently owns the canonical expiry value",
    ],
    "concepts": ["token-refresh", "session-lifecycle", "ownership-gap"],
    "files_read": [
        "src/auth/tokens.py",
        "src/api/session.py",
        "src/api/routes/login.py",
    ],
    "files_modified": [],
}

_TOOLS: list = [
    {
        "name": "record_observation",
        "title": "Record Observation",
        "description": (
            "Record a structured technical observation about work just completed, so it is "
            "kept as durable project memory - the session work log / telemetry history. "
            "Call it once per DISTINCT unit of work finished in this response: one call for a "
            "bug fixed, a separate call for a feature added, a separate call for an analysis "
            "completed. Multiple calls per response are expected for complex tasks. "
            "Call it before writing your final response text. "
            "WHEN NOT TO CALL: pure conversation, greetings, or a trivial single read that "
            "produced no outcome.\n"
            "\n"
            "EVERY call MUST carry all eight fields - type, title, subtitle, narrative, "
            "facts, concepts, files_read, files_modified. A call carrying only type and "
            "title is stored as an empty husk of an observation: the subtitle, narrative, "
            "facts and concepts are the entire reason the record is worth keeping, so never "
            "omit them and never send them as empty strings or empty arrays. Populate every "
            "field from the work you actually just did. The one exception is files_modified, "
            "which is correctly [] for a discovery or a decision that changed no files.\n"
            "\n"
            "EXAMPLE of a well-formed call that changed files:\n"
            + json.dumps(_OBS_EXAMPLE) + "\n"
            "\n"
            "EXAMPLE of a well-formed call that changed no files:\n"
            + json.dumps(_OBS_EXAMPLE_NO_WRITES) + "\n"
            "\n"
            "Match the depth of those examples: specific file paths, concrete facts, and a "
            "narrative that says what was done, how it works and why it matters. Do not "
            "reply with a one-line stub.\n"
            "\n"
            "FORMATTING: every value is a plain single-line string - no newline characters, "
            "no inner quotes, forward slashes in every path (never backslashes). "
            "The tool replies with a short confirmation and never changes your work; it is "
            "routine background telemetry, so there is no need to mention the call in your "
            "reply."
        ),
        "annotations": {
            "title": "Record Observation",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "inputSchema": {
            "type": "object",
            "required": [
                "type", "title", "subtitle", "narrative",
                "facts", "concepts", "files_read", "files_modified",
            ],
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "bugfix", "feature", "refactor",
                        "change", "discovery", "decision",
                    ],
                    "description": (
                        "REQUIRED. Category of the work YOU performed - not a word borrowed "
                        "from the user's request. bugfix: broken behaviour identified and "
                        "corrected. feature: something added that did not exist. refactor: "
                        "existing code restructured with no behaviour change. change: an "
                        "existing value, setting or data modified. discovery: something read "
                        "and understood, no writes. decision: multiple valid alternatives "
                        "genuinely weighed and one chosen."
                    ),
                },
                "title": {
                    "type": "string",
                    "minLength": 10,
                    "maxLength": 100,
                    "description": (
                        "REQUIRED. Action-oriented verb + technical subject, max 10 words "
                        "and 100 characters. Record what was BUILT/FIXED/DEPLOYED, not what "
                        "you looked at. GOOD: 'Fixed null pointer in auth middleware'. "
                        "BAD: 'Analyzed the authentication code'."
                    ),
                },
                "subtitle": {
                    "type": "string",
                    "minLength": 20,
                    "maxLength": 200,
                    "description": (
                        "REQUIRED - never send this empty. One sentence, max 24 words, "
                        "describing what the system now does differently. "
                        "GOOD: 'Requests with no Authorization header no longer crash the "
                        "middleware.'"
                    ),
                },
                "narrative": {
                    "type": "string",
                    "minLength": 60,
                    "description": (
                        "REQUIRED - never send this empty; it is the most valuable field in "
                        "the record. 2-4 sentences, structured as what was done -> how it "
                        "works -> why it matters. Focus on deliverables and capabilities. "
                        "Single line only, no newline characters, forward slashes for paths."
                    ),
                },
                "facts": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 8},
                    "minItems": 1,
                    "maxItems": 12,
                    "description": (
                        "REQUIRED - at least one entry, never an empty array. Concise "
                        "technical statements about what changed. No inner quotes, no log "
                        "strings, forward slashes for paths. "
                        "GOOD: ['Modified src/auth.py to add OAuth2 support']. "
                        "BAD: [\"File now contains 'oauth_enabled=true'\"]."
                    ),
                },
                "concepts": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 3},
                    "minItems": 1,
                    "maxItems": 8,
                    "description": (
                        "REQUIRED - at least one entry. Short abstract technical patterns as "
                        "kebab-case tags, NOT descriptions. "
                        "GOOD: ['oauth2', 'pkce-flow', 'token-refresh']. "
                        "BAD: ['login button', 'user screen', 'oauth setup']."
                    ),
                },
                "files_read": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 50,
                    "description": (
                        "REQUIRED. Repo-relative paths of the files you read this turn, "
                        "forward slashes only. Send [] only when you genuinely read no "
                        "files."
                    ),
                },
                "files_modified": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 50,
                    "description": (
                        "REQUIRED. Repo-relative paths of the files you created, modified or "
                        "deleted this turn, forward slashes only. Send [] only when you "
                        "genuinely changed no files (for example a discovery or decision)."
                    ),
                },
            },
            "examples": [_OBS_EXAMPLE, _OBS_EXAMPLE_NO_WRITES],
        },
    }
]


# ── Payload audit ──────────────────────────────────────────────────────────────

_OBS_FIELDS = tuple(_TOOLS[0]["inputSchema"]["required"])

# files_read, files_modified and concepts are legitimately empty on some
# observations (a discovery that wrote nothing), so only these are treated as
# never-acceptably-empty when judging whether a call arrived usable.
_OBS_NEVER_EMPTY = ("type", "title", "subtitle", "narrative", "facts")


def _audit_obs_args(args: dict) -> dict:
    """Describe what a record_observation payload is missing or renamed."""
    args = args if isinstance(args, dict) else {}
    unknown = sorted(set(args) - set(_OBS_FIELDS))
    return {
        "missing": [f for f in _OBS_NEVER_EMPTY if not args.get(f)],
        "absent": [f for f in _OBS_FIELDS if f not in args],
        "unknown": unknown,
        "dropped_chars": sum(len(json.dumps(args[k], default=str)) for k in unknown),
    }


# ── JSON-RPC transport ─────────────────────────────────────────────────────────

def _send(obj: dict) -> None:
    """Write a JSON-RPC message to stdout and flush immediately."""
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        _log.warning("BrokenPipe - client disconnected")
        sys.exit(0)


def _reply_ok(id_: Any, result: dict) -> None:
    """Send a successful JSON-RPC response."""
    _send({"jsonrpc": "2.0", "id": id_, "result": result})


def _reply_err(id_: Any, code: int, message: str) -> None:
    """Send a JSON-RPC error response."""
    _send({"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}})


# ── Request dispatcher ─────────────────────────────────────────────────────────

def _dispatch(req: dict) -> None:
    """Route a single JSON-RPC request to the appropriate handler."""
    method = req.get("method", "")
    id_    = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        requested_version = params.get("protocolVersion")
        negotiated_version = _negotiate_protocol_version(requested_version)
        _log.info("Client connected - initialize received")
        _log.info(f"Client requested protocolVersion: {requested_version!r}")
        _log.info(f"Client capabilities: {json.dumps(params.get('capabilities', {}))}")
        _log.info(f"Client info: {json.dumps(params.get('clientInfo', {}))}")
        _log.info(f"Negotiated protocolVersion: {negotiated_version!r}")
        _reply_ok(id_, {
            "protocolVersion": negotiated_version,
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            "instructions": (
                "Use record_observation after completing any task that involved tool use "
                "(Read, Write, Edit, Bash, Grep, etc.). "
                "This is routine background telemetry - call it before your final response, "
                "no need to narrate it in your reply, same as other routine tool calls. "
            ),
        })

    elif method == "tools/list":
        _log.debug("tools/list requested")
        _reply_ok(id_, {"tools": _TOOLS})

    elif method == "ping":
        _log.debug("ping received - responding")
        if id_ is not None:
            _reply_ok(id_, {})

    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})

        if name == "record_observation":
            title = args.get("title", "observation")
            audit = _audit_obs_args(args)

            if "__unparsedToolInput" in audit["unknown"]:
                _log.warning(
                    f"OBS_UNPARSED record_observation: client could not parse the model's "
                    f"tool input JSON - payload arrived wrapped in __unparsedToolInput and "
                    f"this observation will be dropped downstream. title={title!r}"
                )
            elif audit["missing"] or audit["unknown"]:
                _log.warning(
                    f"OBS_INCOMPLETE record_observation: title={title!r} "
                    f"missing={audit['missing']} absent={audit['absent']} "
                    f"unknown={audit['unknown']} dropped_chars={audit['dropped_chars']}"
                )
            else:
                _log.info(
                    f"record_observation ok: title={title!r} "
                    f"fields={len(_OBS_FIELDS) - len(audit['absent'])}/{len(_OBS_FIELDS)}"
                )

            _reply_ok(id_, {
                "content": [{"type": "text", "text": f"Observation recorded: {title}."}],
                "isError": False,
            })
        else:
            _log.warning(f"Unknown tool called: {name}")
            _reply_err(id_, -32601, f"Unknown tool: {name}")

    elif method.startswith("notifications/"):
        _log.debug(f"notification received: {method}")
        pass  # Fire-and-forget - no response needed.

    elif id_ is not None:
        _log.warning(f"Method not found: {method}")
        _reply_err(id_, -32601, f"Method not found: {method}")


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Run the MCP stdio server.
    Reads newline-delimited JSON-RPC messages from stdin,
    writes responses to stdout.
    """
    _log.info(f"=== {_SERVER_NAME} v{_SERVER_VERSION} started ===")
    _log.info(f"Platform: {sys.platform}")
    _log.info(f"Python: {sys.version}")
    _log.info(f"Detected client: {_DETECTED_CLIENT}")
    _log.info(f"Log directory: {get_logs_dir()}")

    _log.info("Environment variables from host process:")
    for key, val in os.environ.items():
        if "CLAUDE" in key.upper() or "CURSOR" in key.upper() or "SESSION" in key.upper():
            _log.info(f"  {key}={val}")

    while True:
        try:
            raw_line = sys.stdin.readline()

            if raw_line == "":
                # stdin.readline() returns "" only on real EOF (pipe closed) -
                # the parent process disconnected, so shut down instead of
                # looping forever and leaking an orphaned process.
                _log.info("stdin closed - parent disconnected, shutting down")
                break

            line = raw_line.strip()
            if not line:
                continue

            try:
                _dispatch(json.loads(line))
            except json.JSONDecodeError:
                _log.warning(f"Malformed JSON: {line[:100]}")
                pass
            except Exception as exc:
                _log.error(f"Dispatch error: {exc}", exc_info=True)
                sys.stderr.write(f"[{_SERVER_NAME}] unhandled error: {exc}\n")
                sys.stderr.flush()

        except KeyboardInterrupt:
            _log.info("KeyboardInterrupt - shutting down")
            break
        except EOFError:
            _log.info("EOFError on stdin - parent disconnected, shutting down")
            break
        except Exception as exc:
            _log.error(f"Main loop error: {exc}", exc_info=True)
            sys.stderr.write(f"[{_SERVER_NAME}] error: {exc}\n")
            sys.stderr.flush()
            time.sleep(1)
            continue

    _log.info(f"=== {_SERVER_NAME} stopped ===")


if __name__ == "__main__":
    main()