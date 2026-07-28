"""
db.py — Database helpers shared across all routes.

Ordering strategy (confirmed from real DB analysis):
─────────────────────────────────────────────────────
1. USER_PROMPT order within a session  → ORDER BY timestamp ASC
   Timestamps are unique per prompt — safe to use.

2. TOOL order within a prompt          → ORDER BY rowid ASC
   All tools in one prompt share the same timestamp (useless for ordering).
   The parent_uuid chain is broken — most parents reference assistant wrapper
   blocks not stored in any table.
   rowid = SQLite insertion order = JSONL parse order = true execution order.

3. THINKING paired to TOOL             → matched by shared uuid
   Every tool call has exactly one thinking block with the same uuid.
   rowid of thinking always matches rowid of its paired tool.
"""

import json
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".cloudbyte" / "data" / "cloudbyte-cursor-test.db"

# This repo ships both a Claude Code plugin manifest and a Cursor plugin manifest
# side by side, since both plugins share one codebase - a full clone always has
# both files regardless of which one the user actually installed. File presence
# is therefore not a valid "which plugin is installed" signal; see
# get_active_plugin_versions() below for the real one.
_REPO_ROOT = Path(__file__).parents[3]
_PLUGIN_MANIFESTS = {
    "claude_code": _REPO_ROOT / ".claude-plugin" / "plugin.json",
    "cursor":      _REPO_ROOT / ".cursor-plugin" / "plugin.json",
}


def q(sql: str, params: tuple = (), one: bool = False):
    """Run a query and return Row(s) as sqlite3.Row or a single Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cur = conn.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()
    finally:
        conn.close()


def cmd(sql: str, params: tuple = ()):
    """Run a command (commit) and return rowcount."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def get_active_plugin_versions() -> list[dict]:
    """Which plugin(s) have actually produced data in this DB, and each one's
    own (claude-telemetry / cursor-telemetry) package version from its manifest.

    Detection is usage-based, not file-presence-based: both plugin.json files
    always exist in this repo regardless of which one the user installed, so
    the only reliable signal that a given plugin is actually in use is whether
    any SESSION row with that client value exists - that can only happen if a
    real hook from that plugin fired at least once.
    """
    try:
        rows = q("SELECT DISTINCT client FROM SESSION WHERE client IS NOT NULL")
        active_clients = {row["client"] for row in rows}
    except Exception:
        active_clients = set()

    result = []
    for client, manifest_path in _PLUGIN_MANIFESTS.items():
        if client not in active_clients:
            continue
        try:
            version = json.loads(manifest_path.read_text()).get("version")
        except Exception:
            version = None
        if version:
            result.append({"client": client, "version": version})
    return result


def client_where(client: str | None, alias: str = "s") -> tuple[str, tuple]:
    """SQL fragment + params for the global nav client filter (SESSION.client).

    Returns ("", ()) when client is falsy or 'all' - safe to always splice the
    fragment directly after an existing "WHERE 1=1"/"WHERE <cond>" clause in the
    caller's query. `alias` is whatever table alias SESSION is joined under in
    that query (defaults to 's', the convention used everywhere else). When a
    query has more than one independent subquery needing the filter, repeat the
    returned params tuple once per usage (e.g. `params * 3`) to match the
    positional '?' placeholders.
    """
    if not client or client == "all":
        return "", ()
    return f" AND {alias}.client = ? ", (client,)


def build_tool_list(prompt_id: str) -> list[dict]:
    """
    Return tools for a prompt in execution order (rowid ASC).

    Each item:
      {
        "tool":     dict,        # TOOL row
        "thinking": dict | None, # THINKING row sharing same uuid, or None
        "tokens":   dict | None, # TOOL_TOKENS row
      }

    Thinking is matched to its tool by shared uuid — every tool call has
    exactly one paired thinking block recorded with the same uuid.
    """
    tools = q(
        "SELECT * FROM TOOL WHERE prompt_id = ? ORDER BY rowid ASC",
        (prompt_id,)
    )

    thinking_rows = q(
        "SELECT * FROM THINKING WHERE prompt_id = ? ORDER BY rowid ASC",
        (prompt_id,)
    )

    # Claude Code populates uuid on both tables, so pairing by uuid is exact.
    # Cursor leaves uuid NULL on every row (no hook equivalent) - fall back to
    # positional rowid-adjacency, the true execution-order signal for that case.
    thinking_by_uuid = {row["uuid"]: dict(row) for row in thinking_rows if row["uuid"] is not None}
    thinking_by_position = [dict(row) for row in thinking_rows if row["uuid"] is None]
    position_index = 0

    result = []
    for tool in tools:
        tool_dict = dict(tool)
        ttok = q(
            "SELECT * FROM TOOL_TOKENS WHERE tool_id = ? LIMIT 1",
            (tool["tool_id"],), one=True
        )
        if tool["uuid"] is not None:
            thinking = thinking_by_uuid.get(tool["uuid"])
        else:
            thinking = thinking_by_position[position_index] if position_index < len(thinking_by_position) else None
            position_index += 1
        result.append({
            "tool":     tool_dict,
            "thinking": thinking,
            "tokens":   dict(ttok) if ttok else None,
        })

    return result