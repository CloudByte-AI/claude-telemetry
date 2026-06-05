"""
Missed-pair recovery module.

process_missed_pairs(session_id, cwd) detects interrupted or missed turns
using JSONL identifiers and recovers all available data into the DB.

Two interruption types detected from JSONL:
  "[Request interrupted by user for tool use]" — user denied a tool call
  "[Request interrupted by user]"              — user hit ESC mid-stream

Two recovery passes:
  Pass 1 (interrupt-based): uses the above markers to find interrupted promptIds.
    Works at raw event level — captures tools and tokens even with no text response.
  Pass 2 (message_id-based): catches any end_turn response the stop hook missed
    for any other reason (crash, etc.) by checking message_id in RESPONSE table.
"""

import json
import re as _re
import uuid as _uuid_mod
from ftfy import fix_text as _fix_text

from src.common.logging import get_logger
from src.common.time_utils import to_ist
from src.db.manager import get_db_connection
from src.db.writers import DatabaseWriter
from src.integrations.claude.prompt_response import (
    convert_pairs_to_db_format,
    extract_prompt_response_pairs,
)
from src.integrations.claude.reader import (
    get_claude_dir,
    normalize_project_name,
    read_jsonl_file,
)
from src.observations.writer import save_observation


logger = get_logger(__name__)

MCP_OBS_TOOL = "mcp__plugin_claude-telemetry_cloudbyte__record_observation"

# Maps interrupt text → interrupt_reason value stored in USER_PROMPT
INTERRUPT_TEXT_TO_REASON = {
    "[Request interrupted by user for tool use]": "tool_use",
    "[Request interrupted by user]": "request",
}
INTERRUPT_TEXTS = frozenset(INTERRUPT_TEXT_TO_REASON)


# ---------------------------------------------------------------------------
# JSONL helpers
# ---------------------------------------------------------------------------

def _find_interrupted_prompt_ids(events: list) -> dict:
    """
    Return {promptId: interrupt_reason} for all interrupted turns.

    interrupt_reason values:
      'tool_use' — user denied a tool call
      'request'  — user hit ESC / cancelled the entire request
    """
    result = {}
    for event in events:
        if event.get("type") != "user":
            continue
        pid = event.get("promptId")
        if not pid:
            continue
        # Detect from synthetic text in message content
        for item in (event.get("message", {}).get("content", []) or []):
            if isinstance(item, dict) and item.get("type") == "text":
                reason = INTERRUPT_TEXT_TO_REASON.get(item.get("text", ""))
                if reason:
                    result[pid] = reason
        # Also detect from top-level toolUseResult (tool_use denial)
        if event.get("toolUseResult") == "User rejected tool use":
            result[pid] = "tool_use"
    return result


def _get_user_prompt_event(events: list, prompt_id: str):
    """
    Return (prompt_text, raw_event) for the original user message of a promptId.
    Skips tool_result records and interrupt synthetic messages.
    Handles both plain-string content and content-array formats.
    """
    for event in events:
        if event.get("type") != "user" or event.get("promptId") != prompt_id:
            continue
        content = event.get("message", {}).get("content")

        # Plain string content (CLI prompts often use this format)
        if isinstance(content, str):
            text = content.strip()
            if text and text not in INTERRUPT_TEXTS:
                return text, event
            continue

        # Array content format
        content = content or []
        if any(isinstance(i, dict) and i.get("type") == "tool_result" for i in content):
            continue
        texts = [
            i.get("text", "")
            for i in content
            if isinstance(i, dict) and i.get("type") == "text"
            and i.get("text", "") not in INTERRUPT_TEXTS
        ]
        if texts:
            return " ".join(texts), event
    return "", {}


def _get_assistant_msg_ids_for_prompt(events: list, prompt_id: str) -> set:
    """
    Trace parentUuid links to find assistant message IDs belonging to a promptId.

    Assistant records don't carry promptId directly. User records (tool results,
    interrupt messages) within the same turn DO carry promptId and their
    parentUuid points to the last assistant chunk that triggered them.
    """
    parent_uuids = set()
    for event in events:
        if event.get("type") == "user" and event.get("promptId") == prompt_id:
            pu = event.get("parentUuid")
            if pu:
                parent_uuids.add(pu)

    msg_ids = set()
    for event in events:
        if event.get("type") == "assistant" and event.get("uuid") in parent_uuids:
            mid = event.get("message", {}).get("id")
            if mid:
                msg_ids.add(mid)
    return msg_ids


def _merge_assistant_chunks(events: list, msg_id: str) -> dict:
    """Merge all streaming chunks for an assistant message into one record."""
    merged = {
        "id": msg_id,
        "text": "",
        "tool_uses": [],
        "thinking": [],
        "usage": {},
        "model": "",
        "uuid": "",
        "parent_uuid": "",
        "timestamp": "",
    }
    chunks = [
        e for e in events
        if e.get("type") == "assistant" and e.get("message", {}).get("id") == msg_id
    ]
    for chunk in chunks:
        msg = chunk.get("message", {})
        if not merged["uuid"]:
            merged["uuid"] = chunk.get("uuid", "")
        if not merged["parent_uuid"]:
            merged["parent_uuid"] = chunk.get("parentUuid", "")
        if not merged["timestamp"]:
            merged["timestamp"] = chunk.get("timestamp", "")
        if not merged["model"] and msg.get("model"):
            merged["model"] = msg["model"]
        if msg.get("usage"):
            merged["usage"] = msg["usage"]
        for item in (msg.get("content") or []):
            if not isinstance(item, dict):
                continue
            t = item.get("type")
            if t == "text":
                merged["text"] = (merged["text"] + item.get("text", "")).strip()
            elif t == "tool_use":
                # Avoid duplicates
                existing_ids = {tu["id"] for tu in merged["tool_uses"]}
                if item.get("id") not in existing_ids:
                    merged["tool_uses"].append(item)
            elif t == "thinking":
                merged["thinking"].append(item)
    return merged


def _find_tool_output(events: list, tool_use_id: str):
    """Return the tool result content string for a given tool_use_id, or None."""
    for event in events:
        if event.get("type") != "user":
            continue
        for item in (event.get("message", {}).get("content", []) or []):
            if isinstance(item, dict) and item.get("type") == "tool_result":
                if item.get("tool_use_id") == tool_use_id:
                    content = item.get("content", "")
                    if isinstance(content, list):
                        return json.dumps(content)
                    return str(content) if content else None
    return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return _re.sub(r"\s+", " ", (text or "").strip().lower())


def _find_or_create_db_prompt(
    session_id: str,
    jsonl_prompt_id: str,
    prompt_text: str,
    prompt_event: dict,
    conn,
    interrupt_reason: str = None,
) -> str:
    """
    Find an existing USER_PROMPT record by text match (3 passes), or create one.
    Returns the db_prompt_id to use for all child records.
    """
    cursor = conn.cursor()
    prompt_text_norm = _norm(prompt_text)

    cursor.execute(
        """
        SELECT p.prompt_id, p.prompt FROM USER_PROMPT p
        LEFT JOIN RESPONSE r ON r.prompt_id = p.prompt_id
        WHERE p.session_id = ? AND r.message_id IS NULL
        ORDER BY p.timestamp DESC LIMIT 30
        """,
        (session_id,),
    )
    candidates = cursor.fetchall()

    db_prompt_id = None

    # Pass 1 — normalized exact
    for cid, ctxt in candidates:
        if _norm(ctxt) == prompt_text_norm:
            db_prompt_id = cid
            break

    # Pass 2 — fuzzy substring
    if not db_prompt_id:
        for cid, ctxt in candidates:
            cn = _norm(ctxt)
            if prompt_text_norm in cn or cn in prompt_text_norm:
                db_prompt_id = cid
                break

    # Pass 3 — create new record
    if not db_prompt_id:
        new_id = jsonl_prompt_id
        try:
            conn2 = get_db_connection()
            conn2.cursor().execute(
                """
                INSERT INTO USER_PROMPT (
                    prompt_id, session_id, uuid, parent_uuid, prompt, timestamp,
                    entrypoint, claude_version, git_branch, permission_mode,
                    jsonl_prompt_id, interrupt_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id, session_id,
                    prompt_event.get("uuid", str(_uuid_mod.uuid4())),
                    prompt_event.get("parentUuid"),
                    prompt_text,
                    to_ist(prompt_event.get("timestamp")),
                    prompt_event.get("entrypoint"),
                    prompt_event.get("version"),
                    prompt_event.get("gitBranch"),
                    prompt_event.get("permissionMode"),
                    new_id,
                    interrupt_reason,
                ),
            )
            conn2.commit()
            db_prompt_id = new_id
            logger.info(f"recovery: created prompt record {db_prompt_id}")
        except Exception as exc:
            logger.error(f"recovery: failed to insert prompt: {exc}")
            db_prompt_id = jsonl_prompt_id  # best effort
    else:
        # Update jsonl_prompt_id + interrupt_reason + metadata on matched record
        try:
            cursor.execute(
                """
                UPDATE USER_PROMPT
                SET jsonl_prompt_id  = ?,
                    interrupt_reason = COALESCE(interrupt_reason, ?),
                    parent_uuid      = COALESCE(parent_uuid, ?),
                    entrypoint       = COALESCE(entrypoint, ?),
                    claude_version   = COALESCE(claude_version, ?),
                    git_branch       = COALESCE(git_branch, ?),
                    permission_mode  = COALESCE(permission_mode, ?)
                WHERE prompt_id = ?
                """,
                (
                    jsonl_prompt_id,
                    interrupt_reason,
                    prompt_event.get("parentUuid"),
                    prompt_event.get("entrypoint"),
                    prompt_event.get("version"),
                    prompt_event.get("gitBranch"),
                    prompt_event.get("permissionMode"),
                    db_prompt_id,
                ),
            )
            conn.commit()
        except Exception as exc:
            logger.warning(f"recovery: could not update jsonl_prompt_id: {exc}")

    return db_prompt_id


# ---------------------------------------------------------------------------
# Recovery passes
# ---------------------------------------------------------------------------

def _recover_interrupted_prompt(
    session_id: str,
    prompt_id: str,
    interrupt_reason: str,
    events: list,
    conn,
    writer: DatabaseWriter,
) -> bool:
    """
    Pass 1: recover all available data for an interrupted promptId.
    Works at raw event level — handles tool-only turns with no text response.
    """
    prompt_text_raw, prompt_event = _get_user_prompt_event(events, prompt_id)
    if not prompt_text_raw:
        logger.debug(f"recovery: no prompt text found for interrupted promptId={prompt_id}")
        return False

    prompt_text = _fix_text(prompt_text_raw)
    db_prompt_id = _find_or_create_db_prompt(
        session_id, prompt_id, prompt_text, prompt_event, conn,
        interrupt_reason=interrupt_reason,
    )

    msg_ids = _get_assistant_msg_ids_for_prompt(events, prompt_id)
    if not msg_ids:
        logger.debug(f"recovery: no assistant messages for interrupted promptId={prompt_id}")
        return True  # Prompt recovered even if no response (Type 2 interrupt)

    cursor = conn.cursor()

    for msg_id in msg_ids:
        merged = _merge_assistant_chunks(events, msg_id)

        # ── Write text response if present ────────────────────────────────────
        if merged["text"]:
            cursor.execute(
                "SELECT 1 FROM RESPONSE WHERE message_id = ? LIMIT 1", (msg_id,)
            )
            if not cursor.fetchone():
                writer.write_response({
                    "message_id": msg_id,
                    "prompt_id": db_prompt_id,
                    "uuid": merged["uuid"],
                    "parent_uuid": merged["parent_uuid"],
                    "response_text": merged["text"],
                    "model": merged["model"],
                    "timestamp": to_ist(merged["timestamp"]),
                })

        # ── Write tool calls + tool tokens ───────────────────────────────────
        usage = merged.get("usage", {})
        for tool_use in merged["tool_uses"]:
            tool_id = tool_use.get("id")
            if not tool_id:
                continue
            cursor.execute(
                "SELECT 1 FROM TOOL WHERE tool_id = ? LIMIT 1", (tool_id,)
            )
            already_exists = cursor.fetchone()
            if not already_exists:
                tool_output = _find_tool_output(events, tool_id)
                tool_input = tool_use.get("input", {})
                writer.write_tool({
                    "tool_id": tool_id,
                    "prompt_id": db_prompt_id,
                    "uuid": merged["uuid"],
                    "parent_uuid": merged["parent_uuid"],
                    "tool_name": tool_use.get("name", ""),
                    "model": merged["model"],
                    "input_json": json.dumps(tool_input) if isinstance(tool_input, dict) else str(tool_input),
                    "output_json": tool_output,
                    "timestamp": to_ist(merged["timestamp"]),
                })

            # Write tool tokens (use the assistant message's usage for each tool)
            if usage:
                cursor.execute(
                    "SELECT 1 FROM TOOL_TOKENS WHERE tool_id = ? LIMIT 1", (tool_id,)
                )
                if not cursor.fetchone():
                    writer.write_tool_tokens({
                        "id": str(_uuid_mod.uuid4()),
                        "tool_id": tool_id,
                        "input_tokens": usage.get("input_tokens", 0),
                        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
                        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                    })

        # ── Write IO token usage ──────────────────────────────────────────────
        if usage:
            cursor.execute(
                """
                SELECT 1 FROM IO_TOKENS
                WHERE prompt_id = ? AND (message_id = ? OR message_id IS NULL)
                LIMIT 1
                """,
                (db_prompt_id, msg_id),
            )
            if not cursor.fetchone():
                writer.write_io_tokens({
                    "id": str(_uuid_mod.uuid4()),
                    "prompt_id": db_prompt_id,
                    "message_id": msg_id,   # writer sets to None if no RESPONSE row
                    "token_type": "io",
                    "input_tokens": usage.get("input_tokens", 0),
                    "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
                    "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                })

        # ── Save any MCP record_observation calls to HOOK_OBSERVATION ─────────
        for tool_use in merged["tool_uses"]:
            if tool_use.get("name") != MCP_OBS_TOOL:
                continue
            try:
                obs_data = tool_use.get("input", {})
                if isinstance(obs_data, str):
                    obs_data = json.loads(obs_data)
                if obs_data.get("title"):
                    save_observation(session_id=session_id, prompt_id=db_prompt_id, obs_data=obs_data)
                    logger.info(f"recovery pass1: saved MCP observation: {obs_data.get('title')}")
            except Exception as obs_err:
                logger.warning(f"recovery pass1: MCP obs save failed: {obs_err}")

    logger.info(f"recovery pass1: recovered interrupted promptId={prompt_id} db_prompt_id={db_prompt_id}")
    return True


def _is_hook_output(text: str) -> bool:
    t = (text or "").strip()
    return (t.startswith("● Ran") and "hooks" in t) or t.startswith("⎿")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_missed_pairs(session_id: str, cwd: str) -> dict:
    """
    Recover response data for any JSONL turns the stop hook missed.

    Pass 1 — interrupt-based:
      Detect promptIds with [Request interrupted by user ...] markers.
      Recover prompt + tools + tokens directly from raw JSONL events.

    Pass 2 — message_id-based:
      For end_turn pairs not in the RESPONSE table (stop hook missed for
      any other reason), apply the same stop-hook matching + write logic.

    Returns dict with keys 'pass1' and 'pass2' (counts processed).
    """
    counts = {"pass1": 0, "pass2": 0}

    try:
        project_name = normalize_project_name(cwd)
        claude_dir = get_claude_dir()
        jsonl_path = claude_dir / "projects" / project_name / f"{session_id}.jsonl"

        if not jsonl_path.exists():
            logger.debug(f"process_missed_pairs: JSONL not found: {jsonl_path}")
            return counts

        events = list(read_jsonl_file(jsonl_path))
        if not events:
            return counts

        # Run migrations before any writes — ensures interrupt_reason column exists
        # even when called from UserPromptSubmit before stop() has had a chance to migrate.
        try:
            from src.db.schema import migrate_schema
            from src.db.manager import get_db_manager
            migrate_schema(get_db_manager().get_connection())
        except Exception as _me:
            logger.warning(f"process_missed_pairs: migration check failed: {_me}")

        conn = get_db_connection()
        writer = DatabaseWriter()

        # ── Pass 1: interrupt-based recovery ──────────────────────────────────
        interrupted_ids = _find_interrupted_prompt_ids(events)  # {prompt_id: reason}
        for prompt_id, reason in interrupted_ids.items():
            if _recover_interrupted_prompt(session_id, prompt_id, reason, events, conn, writer):
                counts["pass1"] += 1

        # ── Pass 2: message_id-based recovery (end_turn missed by stop hook) ──
        pairs = extract_prompt_response_pairs(events)
        filtered = [p for p in pairs if not _is_hook_output(p.get("prompt", ""))]

        cursor = conn.cursor()
        for pair in filtered:
            message_id = pair.get("message_id")
            if not message_id:
                continue

            # Already in DB
            cursor.execute(
                "SELECT 1 FROM RESPONSE WHERE message_id = ? LIMIT 1", (message_id,)
            )
            if cursor.fetchone():
                continue

            # Already handled by Pass 1
            if pair.get("prompt_id") in interrupted_ids:
                continue

            # Not in DB — apply stop-hook matching logic
            jsonl_prompt_id = pair["prompt_id"]
            prompt_text = _fix_text(pair.get("prompt", ""))
            prompt_rec = pair.get("prompt_rec", {})
            db_prompt_id = _find_or_create_db_prompt(
                session_id, jsonl_prompt_id, prompt_text, prompt_rec, conn
            )

            db_data = convert_pairs_to_db_format([pair])

            def remap(item: dict) -> dict:
                r = item.copy()
                r["prompt_id"] = db_prompt_id
                return r

            for response in db_data.get("responses", []):
                if response["prompt_id"] == jsonl_prompt_id:
                    writer.write_response(remap(response))
            for tool in db_data.get("tools", []):
                if tool["prompt_id"] == jsonl_prompt_id:
                    writer.write_tool(remap(tool))
            for thinking in db_data.get("thinking", []):
                if thinking["prompt_id"] == jsonl_prompt_id:
                    writer.write_thinking(remap(thinking))
            for io_tokens in db_data.get("io_tokens", []):
                if io_tokens["prompt_id"] == jsonl_prompt_id:
                    writer.write_io_tokens(remap(io_tokens))
            for tt in db_data.get("tool_tokens", []):
                for tool in db_data.get("tools", []):
                    if tool["tool_id"] == tt["tool_id"] and tool["prompt_id"] == jsonl_prompt_id:
                        writer.write_tool_tokens(tt)
                        break

            counts["pass2"] += 1
            logger.info(f"recovery pass2: recovered message_id={message_id}")

            # Save any MCP record_observation calls to HOOK_OBSERVATION
            for tool in db_data.get("tools", []):
                if tool["prompt_id"] != jsonl_prompt_id:
                    continue
                if tool.get("tool_name") != MCP_OBS_TOOL:
                    continue
                try:
                    raw_input = tool.get("input_json", "{}")
                    obs_data = json.loads(raw_input) if isinstance(raw_input, str) else raw_input
                    if obs_data.get("title"):
                        save_observation(session_id=session_id, prompt_id=db_prompt_id, obs_data=obs_data)
                        logger.info(f"recovery pass2: saved MCP observation: {obs_data.get('title')}")
                except Exception as obs_err:
                    logger.warning(f"recovery pass2: MCP obs save failed: {obs_err}")

    except Exception as exc:
        logger.error(f"process_missed_pairs failed: {exc}", exc_info=True)

    if counts["pass1"] or counts["pass2"]:
        logger.info(f"process_missed_pairs: pass1={counts['pass1']} pass2={counts['pass2']}")

    return counts
