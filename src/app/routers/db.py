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

import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".cloudbyte" / "data" / "cloudbyte.db"


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
    thinking_by_uuid = {row["uuid"]: dict(row) for row in thinking_rows}

    result = []
    for tool in tools:
        tool_dict = dict(tool)
        ttok = q(
            "SELECT * FROM TOOL_TOKENS WHERE tool_id = ? LIMIT 1",
            (tool["tool_id"],), one=True
        )
        result.append({
            "tool":     tool_dict,
            "thinking": thinking_by_uuid.get(tool["uuid"]),
            "tokens":   dict(ttok) if ttok else None,
        })

    return result