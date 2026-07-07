#!/usr/bin/env python3
"""
CloudByte MCP Server — Observation Capture

Exposes a single MCP tool: record_observation

Claude calls this tool as routine background telemetry after every response
where it used tools or made meaningful changes. The tool simply acknowledges
receipt — observations are visible in the JSONL transcript for inspection.

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
            "This is routine background telemetry, like other tool calls you don't narrate — "
            "call it before your final response text, no need to mention it in your reply."
        ),
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
                        "Focus on deliverables and capabilities, not observations. "
                        "Single line only — no newline characters. "
                        "Use forward slashes for paths, never backslashes."
                    ),
                },
                "facts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Concise technical statements. No inner quotes. No log strings. "
                        "Use forward slashes for paths, never backslashes. "
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
                    "description": (
                        "Relative paths of files read. "
                        "Always use forward slashes — never backslashes."
                    ),
                },
                "files_modified": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Relative paths of files created, modified, or deleted. "
                        "Always use forward slashes — never backslashes."
                    ),
                },
            },
        },
    }
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
            "instructions": (
                "Use record_observation after completing any task that involved tool use "
                "(Read, Write, Edit, Bash, Grep, etc.). "
                "This is routine background telemetry — call it before your final response, "
                "no need to narrate it in your reply, same as other routine tool calls. "
                "Tool name: mcp__plugin_claude-telemetry_cloudbyte__record_observation"
            ),
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
                "content": [{"type": "text", "text": f"Observation recorded: {title}."}],
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

    while True:
        try:
            raw_line = sys.stdin.readline()

            if raw_line == "":
                # stdin.readline() returns "" only on real EOF (pipe closed) —
                # the parent process disconnected, so shut down instead of
                # looping forever and leaking an orphaned process.
                _log.info("stdin closed — parent disconnected, shutting down")
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
            _log.info("KeyboardInterrupt — shutting down")
            break
        except EOFError:
            _log.info("EOFError on stdin — parent disconnected, shutting down")
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