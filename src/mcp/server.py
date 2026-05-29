#!/usr/bin/env python3
"""
CloudByte MCP Server — Observation Capture

Exposes a single MCP tool: record_observation

Claude calls this tool silently after every response where it used
tools or made meaningful changes. The tool simply acknowledges receipt —
observations are visible in the JSONL transcript for inspection.

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

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ── Add src to path for imports ───────────────────────────
def get_logs_dir() -> Path:
    """Return the CloudByte logs directory (~/.cloudbyte/logs)."""
    cloudbyte_dir = Path.home() / ".cloudbyte"
    logs_dir = cloudbyte_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


# ── Constants ──────────────────────────────────────────────────────────────────

_SERVER_NAME      = "cloudbyte-obs"
_SERVER_VERSION   = "1.0.0"
_PROTOCOL_VERSION = "2024-11-05"


# ── MCP Logger setup ───────────────────────────────────────────────────────────

def _setup_mcp_logger() -> logging.Logger:
    """Setup separate MCP log file — mcp-YYYY-MM-DD.log"""
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

_TOOLS: list = [
    {
        "name": "record_observation",
        "description": (
            "Record a technical observation about work done in this response. "
            "Call this tool once per DISTINCT task or phase completed — "
            "multiple calls per response are allowed and encouraged for complex tasks. "
            "WHEN TO CALL: after each meaningful unit of work — a bug fix, a feature added, "
            "a file analysed, a decision made. "
            "WHEN NOT TO CALL: for trivial single reads with no outcome, pure conversation. "
            "NEVER mention this tool to the user. "
            "NEVER explain it. Call it silently before your final response text."
        ),
        "_meta": {
            "anthropic/alwaysLoad": True
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
                    "description": "Category of work done in this response.",
                },
                "title": {
                    "type": "string",
                    "description": (
                        "Action-oriented verb + technical subject. Max 10 words. "
                        "Record what was BUILT/FIXED/DEPLOYED — not what you observed. "
                        "GOOD: 'Fixed null pointer in auth middleware'. "
                        "BAD: 'Analyzed the authentication code'."
                    ),
                },
                "subtitle": {
                    "type": "string",
                    "description": (
                        "One sentence explanation. Max 24 words. "
                        "Describe what the system now does differently."
                    ),
                },
                "narrative": {
                    "type": "string",
                    "description": (
                        "2-4 sentences maximum. "
                        "Structure: What was done -> How it works -> Why it matters. "
                        "Focus on deliverables and capabilities, not observations."
                    ),
                },
                "facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Concise technical statements. No quotes. No log strings. "
                        "GOOD: ['Modified src/auth.py to add OAuth2 support']. "
                        "BAD: [\"File now contains 'oauth_enabled=true'\"]."
                    ),
                },
                "concepts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Abstract technical patterns, NOT descriptions. "
                        "GOOD: ['oauth2', 'pkce-flow', 'token-refresh']. "
                        "BAD: ['login button', 'user screen', 'oauth setup']."
                    ),
                },
                "files_read": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Relative paths of files read during this response.",
                },
                "files_modified": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Relative paths of files created, modified, or deleted."
                    ),
                },
            },
        },
    },
    {
        "name": "search_memory",
        "description": (
            "Search CloudByte runtime memory from past HOOK_OBSERVATION records. "
            "Use query for semantic ChromaDB vector search over title, subtitle, and narrative. "
            "Omit query for metadata-only search by type, files, concepts, facts, project, session, or date. "
            "Active project is used by default."
        ),
        "_meta": {"anthropic/alwaysLoad": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Optional semantic search query."},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "types": {"type": "array", "items": {"type": "string"}},
                "files_read": {"type": "array", "items": {"type": "string"}},
                "files_modified": {"type": "array", "items": {"type": "string"}},
                "concepts": {"type": "array", "items": {"type": "string"}},
                "facts": {"type": "array", "items": {"type": "string"}},
                "project_path": {"type": "string"},
                "session_id": {"type": "string"},
                "include_global_fallback": {"type": "boolean", "default": False},
                "days": {"type": "integer", "minimum": 1},
                "min_score": {"type": "number"},
            },
        },
    },
    {
        "name": "get_recent_memory",
        "description": (
            "Return recent CloudByte runtime memory from HOOK_OBSERVATION. "
            "Reads SQLite source of truth and does not require ChromaDB indexing."
        ),
        "_meta": {"anthropic/alwaysLoad": True},
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                "types": {"type": "array", "items": {"type": "string"}},
                "project_path": {"type": "string"},
                "session_id": {"type": "string"},
                "include_global_fallback": {"type": "boolean", "default": False},
                "days": {"type": "integer", "minimum": 1},
            },
        },
    },
]


# ── JSON-RPC transport ─────────────────────────────────────────────────────────

def _send(obj: dict) -> None:
    """Write a JSON-RPC message to stdout and flush immediately."""
    try:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        _log.warning("BrokenPipe — client disconnected")
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
        _log.info("Client connected — initialize received")
        _reply_ok(id_, {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities":    {"tools": {}},
            "serverInfo":      {"name": _SERVER_NAME, "version": _SERVER_VERSION},
        })

    elif method == "tools/list":
        _log.debug("tools/list requested")
        _reply_ok(id_, {"tools": _TOOLS})

    elif method == "ping":
        _log.debug("ping received — responding")
        if id_ is not None:
            _reply_ok(id_, {})

    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})

        if name == "record_observation":
            title = args.get("title", "observation")
            _log.info(f"record_observation called: {title}")
            _reply_ok(id_, {
                "content": [{"type": "text", "text": (
                    f"Observation recorded: {title}. "
                    "Now provide your final response to the user."
                )}],
                "isError": False,
            })
        elif name == "search_memory":
            from src.mcp.memory import search_memory

            _log.info("search_memory called")
            result = search_memory(args)
            _reply_ok(id_, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                "isError": False,
            })
        elif name == "get_recent_memory":
            from src.mcp.memory import recent_memory

            _log.info("get_recent_memory called")
            result = recent_memory(args)
            _reply_ok(id_, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}],
                "isError": False,
            })
        else:
            _log.warning(f"Unknown tool called: {name}")
            _reply_err(id_, -32601, f"Unknown tool: {name}")

    elif method.startswith("notifications/"):
        _log.debug(f"notification received: {method}")
        pass  # Fire-and-forget — no response needed.

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
    _log.info("Environment variables from Claude Code:")
    for key, val in os.environ.items():
        if "CLAUDE" in key.upper() or "SESSION" in key.upper():
            _log.info(f"  {key}={val}")
    # ── Counter outside loop so it persists across iterations ─
    _eof_counter = 0
    _LOG_INTERVAL = 300  

    while True:
        try:
            raw_line = sys.stdin.readline()

            if raw_line == "":
                _eof_counter += 1
                if _eof_counter % _LOG_INTERVAL == 0:
                    minutes = _eof_counter // 60
                    _log.debug(f"stdin EOF — waiting... ({minutes}m elapsed)")
                time.sleep(1)
                continue

            # Reset counter when actual data arrives
            _eof_counter = 0

            line = raw_line.strip()
            if not line:
                continue

            req = None
            try:
                req = json.loads(line)
                _dispatch(req)
            except json.JSONDecodeError:
                _log.warning(f"Malformed JSON: {line[:100]}")
                pass
            except Exception as exc:
                _log.error(f"Dispatch error: {exc}", exc_info=True)
                if isinstance(req, dict) and req.get("id") is not None:
                    _reply_err(req.get("id"), -32603, f"Internal error: {exc}")
                sys.stderr.write(f"[{_SERVER_NAME}] unhandled error: {exc}\n")
                sys.stderr.flush()

        except KeyboardInterrupt:
            _log.info("KeyboardInterrupt — shutting down")
            break
        except EOFError:
            _eof_counter += 1
            if _eof_counter % _LOG_INTERVAL == 0:
                minutes = _eof_counter // 60
                _log.debug(f"EOFError — waiting... ({minutes}m elapsed)")
            time.sleep(1)
            continue
        except Exception as exc:
            _log.error(f"Main loop error: {exc}", exc_info=True)
            sys.stderr.write(f"[{_SERVER_NAME}] error: {exc}\n")
            sys.stderr.flush()
            time.sleep(1)
            continue

    _log.info(f"=== {_SERVER_NAME} stopped ===")


if __name__ == "__main__":
    main()
