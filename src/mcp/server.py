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
import hashlib
import json
import logging
import re
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

# Set at initialize. Only gates optional response fields, so a client that never
# handshakes simply gets the plain content-only result.
_NEGOTIATED_VERSION: str = ""


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
            "FORMATTING: type, title, subtitle and narrative are single-line strings - no "
            "newline characters, no inner quotes. facts, concepts, files_read and "
            "files_modified are JSON arrays of strings. Forward slashes in every path "
            "(never backslashes). Close every parameter block with </parameter>. "
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
                    # Bounded like title and subtitle. Observed narratives run
                    # 360-950 chars, so this is generous, but an unbounded prose
                    # field is the one the model loses the parameter frame on.
                    "maxLength": 1200,
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
        # Declared so a client on 2025-06-18+ can validate structuredContent.
        # `warnings` is the soft corrective channel: a call can be stored and
        # still tell the model what to do differently next time, without
        # costing a rejection round-trip.
        "outputSchema": {
            "type": "object",
            "required": ["recorded", "title", "warnings", "repairs"],
            "properties": {
                "recorded": {
                    "type": "boolean",
                    "description": "False when the call was refused and must be resent.",
                },
                "title": {"type": "string"},
                "warnings": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Contract problems to correct on the next call.",
                },
                "repairs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Deterministic fixes the server applied before storing.",
                },
            },
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


# ── Payload salvage ────────────────────────────────────────────────────────────
#
# Models deviate from the advertised contract in four observed ways: they omit
# fields, they rename them, the client fails to parse their JSON, and - when the
# tool is delivered as a deferred tool and must be invoked in the XML parameter
# protocol - they close a parameter block with a name-matched tag such as
# </narrative> instead of the generic </parameter>. That last one is silent and
# destructive: the harness scans on to the next real </parameter>, so the field
# that followed is swallowed whole into the prose field and never arrives.
#
# Everything here is deterministic repair, no model round-trip. Anything that
# cannot be repaired is reported back through _obs_hard_errors.

# Non-schema keys observed carrying real content, mapped to where they belong.
# Grow this from the `unknown=[...]` lists in the OBS_INCOMPLETE log lines.
_OBS_ALIASES = {
    "summary": "subtitle",
    "description": "subtitle",
    "details": "narrative",
    "what_happened": "narrative",
    "body": "narrative",
    "key_facts": "facts",
    "observation_type": "type",
    "tags": "concepts",
    "files_touched": "files_read",
}

# Editorial inventions that map to nothing. Dropped, but named in the log.
_OBS_DROP = ("assumptions", "project", "autocancel")

# A prose value that swallowed the parameter block after it. Non-greedy head so
# the FIRST mis-close wins, which is where the real narrative ends.
_XML_LEAK_RE = re.compile(
    r"^(?P<head>.*?)</(?P<tag>[A-Za-z_][A-Za-z0-9_]*)>\s*"
    r'<parameter name="(?P<field>[A-Za-z_][A-Za-z0-9_]*)">\s*(?P<value>.*)$',
    re.S,
)

_SCALAR_RE = r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"'
_ARRAY_RE = r'"%s"\s*:\s*(\[[^\]]*\])'


def _coerce_obs_value(field: str, text: str):
    """Parse a recovered parameter value into the type its schema declares."""
    text = (text or "").strip()
    want_list = field in ("facts", "concepts", "files_read", "files_modified")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return [text] if want_list and text else text
    if want_list and not isinstance(parsed, list):
        return [parsed] if parsed else []
    return parsed


def _extract_fields_from_raw(text: str) -> dict:
    """Best-effort field recovery from tool input JSON the client could not parse.

    Deliberately regex-based rather than a JSON repairer: the payload is broken
    by definition, so the goal is to rescue the well-formed leading fields
    rather than to reconstruct a valid document.
    """
    out: dict = {}
    if not isinstance(text, str):
        return out
    for field in _OBS_FIELDS:
        pattern = _ARRAY_RE if field in ("facts", "concepts", "files_read", "files_modified") \
            else _SCALAR_RE
        match = re.search(pattern % re.escape(field), text)
        if not match:
            continue
        raw = match.group(1)
        try:
            out[field] = json.loads(raw if raw.startswith("[") else f'"{raw}"')
        except (json.JSONDecodeError, ValueError):
            continue
    return out


def _salvage_obs_args(args: Any) -> tuple:
    """Repair a deviant payload in place. Returns (payload, repairs_applied)."""
    repairs: list = []
    if not isinstance(args, dict):
        return {}, repairs
    args = dict(args)

    # 1. The client could not parse the model's JSON and wrapped it whole.
    if "__unparsedToolInput" in args:
        wrapper = args.pop("__unparsedToolInput")
        raw = wrapper.get("raw") if isinstance(wrapper, dict) else wrapper
        recovered: dict = {}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                recovered = parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, ValueError):
                recovered = _extract_fields_from_raw(raw)
        if recovered:
            repairs.append(f"unwrapped __unparsedToolInput ({len(recovered)} fields recovered)")
            recovered.update({k: v for k, v in args.items() if v})
            args = recovered

    # 2. A prose parameter closed with a name-matched tag and swallowed the
    #    block after it. Loop, because a chain of mis-closes nests.
    for _ in range(len(_OBS_FIELDS)):
        leaked = None
        for key, value in args.items():
            if isinstance(value, str) and "<parameter name=" in value:
                match = _XML_LEAK_RE.match(value)
                if match:
                    leaked = (key, match)
                    break
        if not leaked:
            break
        key, match = leaked
        field, tail = match.group("field"), match.group("value")
        # The swallowed block ends at the real </parameter>, if one survived.
        tail = tail.split("</parameter>", 1)[0].strip()
        if args.get(field):
            break  # already populated - do not clobber real content
        args[key] = match.group("head")
        args[field] = _coerce_obs_value(field, tail)
        repairs.append(f"recovered {field} from a </{match.group('tag')}> mis-close in {key}")

    # 3. Content sent under a name we recognise but do not use.
    for wrong, right in _OBS_ALIASES.items():
        if wrong in args and not args.get(right):
            args[right] = args.pop(wrong)
            repairs.append(f"mapped {wrong} to {right}")

    # 4. Inventions that map to nothing.
    for junk in [k for k in _OBS_DROP if k in args]:
        args.pop(junk)
        repairs.append(f"dropped {junk}")

    return args, repairs


# ── Validation ─────────────────────────────────────────────────────────────────

_OBS_ENUM = tuple(_TOOLS[0]["inputSchema"]["properties"]["type"]["enum"])

# Rejection is reserved for content that is genuinely absent. Constraint
# violations (short narrative, unknown type) are reported as warnings so a
# terse-but-real observation is never thrown away.
_OBS_HARD_REQUIRED = _OBS_NEVER_EMPTY

# One rejection per distinct observation. The retry carries the same type and
# title, so it maps to the same fingerprint and is always accepted - a second
# failure stores a degraded row rather than losing it. Keyed on content rather
# than on connection identity because MCP 2026-07-28 states that an open stdio
# process is not a session.
_OBS_REJECTED: set = set()
_OBS_REJECT_BUDGET = 50  # process-wide backstop against a pathological loop


def _obs_strict_enabled() -> bool:
    """False when CLOUDBYTE_OBS_STRICT is switched off - salvage and warn only."""
    return os.environ.get("CLOUDBYTE_OBS_STRICT", "1").strip().lower() not in ("0", "false", "no")


def _obs_fingerprint(args: dict) -> str:
    basis = json.dumps(
        {"type": args.get("type"), "title": (args.get("title") or "").strip().lower()},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _obs_hard_errors(args: dict) -> list:
    """Fields that are absent or empty and cannot be recovered."""
    return [f for f in _OBS_HARD_REQUIRED if not args.get(f)]


def _obs_soft_warnings(args: dict) -> list:
    """Contract violations worth telling the model about, but not worth a retry."""
    warnings: list = []
    obs_type = args.get("type")
    if obs_type and obs_type not in _OBS_ENUM:
        warnings.append(f"type {obs_type!r} is not one of {', '.join(_OBS_ENUM)}")
    props = _TOOLS[0]["inputSchema"]["properties"]
    for field in ("title", "subtitle", "narrative"):
        value = args.get(field)
        if not isinstance(value, str):
            continue
        low = props[field].get("minLength")
        high = props[field].get("maxLength")
        if low and len(value) < low:
            warnings.append(f"{field} is {len(value)} chars, below the {low} minimum")
        if high and len(value) > high:
            warnings.append(f"{field} is {len(value)} chars, above the {high} maximum")
    for field in ("facts", "concepts"):
        value = args.get(field)
        high = props[field].get("maxItems")
        if isinstance(value, list) and high and len(value) > high:
            warnings.append(f"{field} has {len(value)} entries, above the {high} maximum")
    return warnings


def _obs_rejection_text(missing: list, repairs: list) -> str:
    """The corrective message the model reads before retrying.

    Names the mechanism when we can see it. A model told which token to change
    can correct; a model told only that a field is missing re-sends the same
    broken framing.
    """
    lines = [
        "Observation NOT recorded. Required field(s) arrived empty or absent: "
        + ", ".join(missing) + "."
    ]
    if any("mis-close" in r for r in repairs):
        lines.append(
            "Cause: a parameter block was closed with a name-matched tag (for example "
            "</narrative>) instead of the generic </parameter>, so the block after it was "
            "swallowed. Close EVERY parameter with </parameter>."
        )
    lines.append("Resend the call once with those fields populated. All 8 fields are required.")
    return " ".join(lines)


def _supports_structured_output() -> bool:
    """True when the negotiated revision understands structuredContent.

    Introduced in 2025-06-18. Older clients would receive a field they do not
    know, so it is withheld rather than sent speculatively.
    """
    return str(_NEGOTIATED_VERSION or "") >= "2025-06-18"


def _obs_result(text: str, is_error: bool, recorded: bool,
                title: str, warnings: list, repairs: list) -> dict:
    """Build the tools/call result.

    `content` carries the human/model-readable message on every revision.
    `structuredContent` mirrors it as typed data for clients that support it,
    matching the tool's declared outputSchema.
    """
    result: dict = {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }
    if _supports_structured_output():
        result["structuredContent"] = {
            "recorded": recorded,
            "title": title,
            "warnings": warnings,
            "repairs": repairs,
        }
    return result


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
        global _NEGOTIATED_VERSION
        requested_version = params.get("protocolVersion")
        negotiated_version = _negotiate_protocol_version(requested_version)
        _NEGOTIATED_VERSION = negotiated_version
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
            audit = _audit_obs_args(args)
            if "__unparsedToolInput" in audit["unknown"]:
                _log.warning(
                    "OBS_UNPARSED record_observation: client could not parse the model's "
                    "tool input JSON - attempting salvage from the raw payload."
                )
            elif audit["missing"] or audit["unknown"]:
                _log.warning(
                    f"OBS_INCOMPLETE record_observation: "
                    f"title={str(args.get('title', ''))[:60]!r} "
                    f"missing={audit['missing']} absent={audit['absent']} "
                    f"unknown={audit['unknown']} dropped_chars={audit['dropped_chars']}"
                )

            repaired, repairs = _salvage_obs_args(args)
            title = repaired.get("title") or "observation"
            missing = _obs_hard_errors(repaired)
            warnings = _obs_soft_warnings(repaired)

            if repairs:
                _log.warning(f"OBS_SALVAGED record_observation: title={title[:60]!r} {repairs}")

            fingerprint = _obs_fingerprint(repaired)
            may_reject = (
                bool(missing)
                and _obs_strict_enabled()
                and fingerprint not in _OBS_REJECTED
                and len(_OBS_REJECTED) < _OBS_REJECT_BUDGET
            )

            if may_reject:
                # Reject once per observation. The retry carries the same title,
                # so it maps to this fingerprint and is accepted unconditionally -
                # a second failure stores a degraded row rather than losing it.
                _OBS_REJECTED.add(fingerprint)
                _log.warning(
                    f"OBS_REJECTED record_observation: title={title[:60]!r} "
                    f"missing={missing} - asked the model to resend"
                )
                _reply_ok(id_, _obs_result(
                    text=_obs_rejection_text(missing, repairs),
                    is_error=True,
                    recorded=False,
                    title=title,
                    warnings=[f"missing: {f}" for f in missing] + warnings,
                    repairs=repairs,
                ))
            else:
                notes = list(warnings)
                if repairs:
                    notes.append(
                        "Your call was repaired before storing: " + "; ".join(repairs)
                        + ". Close EVERY parameter with </parameter>, not a name-matched tag."
                        if any("mis-close" in r for r in repairs)
                        else "Your call was repaired before storing: " + "; ".join(repairs) + "."
                    )
                if missing:
                    notes.append(
                        "Stored with empty field(s): " + ", ".join(missing)
                        + ". Populate all 8 fields on the next call."
                    )
                if not (missing or repairs):
                    _log.info(
                        f"record_observation ok: title={title[:60]!r} "
                        f"fields={len(_OBS_FIELDS) - len(_audit_obs_args(repaired)['absent'])}"
                        f"/{len(_OBS_FIELDS)}"
                    )
                text = f"Observation recorded: {title}."
                if notes:
                    text += " " + " ".join(notes)
                _reply_ok(id_, _obs_result(
                    text=text,
                    is_error=False,
                    recorded=True,
                    title=title,
                    warnings=notes,
                    repairs=repairs,
                ))
        else:
            # The spec's tools page uses -32602 for an unknown tool; -32601
            # stays correct for an unknown *method*, handled further down.
            _log.warning(f"Unknown tool called: {name}")
            _reply_err(id_, -32602, f"Unknown tool: {name}")

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