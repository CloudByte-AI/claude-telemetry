"""
Prompt/Response Pair Extractor

Extracts prompt/response pairs with tools and thinking blocks from Claude Code JSONL files.

Data model per pair:
  prompt_id, message_id, session_id, timestamp, prompt, response, model,
  input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
  tools: [
    {
      tool_use_id, tool_name, tool_input,
      tool_result, is_error,
      thinking,                   # thinking text if present for this tool call
      model,                      # from the assistant record that called this tool
      input_tokens, output_tokens,
      cache_read_tokens, cache_creation_tokens
    },
    ...
  ]

Linking strategy:
  1. prompt   ← promptId on user record (string content)
  2. tool     ← tool_result (type:user, list content) carries promptId + tool_use_id
  3. tool_use ← assistant record whose content has type:tool_use with matching id
  4. thinking ← same assistant record (same message.id, different streaming chunk)
                 that has type:thinking in content
"""

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional

from src.common.logging import get_logger

logger = get_logger(__name__)


def load_records_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert event list to record list for processing.
    Events already come parsed from JSONL.
    """
    return events


def build_uuid_index(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build index by UUID for quick parent lookup."""
    return {r["uuid"]: r for r in records if "uuid" in r}


def find_prompt_id(record: Dict[str, Any], by_uuid: Dict[str, Dict[str, Any]], max_depth: int = 50) -> Optional[str]:
    """Find prompt_id by walking up the parent chain."""
    current = record
    for _ in range(max_depth):
        pid = current.get("promptId")
        if pid:
            return pid
        parent_uuid = current.get("parentUuid")
        if not parent_uuid:
            break
        parent = by_uuid.get(parent_uuid)
        if not parent:
            break
        current = parent
    return None


def get_text_content(content: Any) -> Optional[str]:
    """
    Extract text content from message content (handles string, list, and dict formats).

    Supports:
    - String format: "some text"
    - List format: [{"type":"text","text":"..."}, ...]
    - List with compact summary: [{"type":"text","text":"...","isCompactSummary":true}, ...]

    Note: Content items with isCompactSummary=true are included to ensure
    complete context is captured, not just compact summaries.
    """
    if not content:
        return None

    # Handle string format
    if isinstance(content, str):
        text = content.strip()
        return text if text else None

    # Handle list format
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    text = item.get("text", "").strip()
                    if text:
                        parts.append(text)
        return "\n\n".join(parts) if parts else None

    # Handle dict format (unexpected but possible)
    if isinstance(content, dict):
        if content.get("type") == "text":
            text = content.get("text", "").strip()
            return text if text else None

    return None


def get_thinking_content(content: Any) -> Optional[str]:
    """Extract thinking content from message content array."""
    if not isinstance(content, list):
        return None
    parts = [
        item["thinking"].strip()
        for item in content
        if isinstance(item, dict) and item.get("type") == "thinking" and item.get("thinking", "").strip()
    ]
    return "\n\n".join(parts) if parts else None


def extract_prompt_response_pairs(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Extract prompt/response pairs from Claude Code events.

    Args:
        events: List of event dictionaries from JSONL

    Returns:
        List of prompt/response pair dictionaries
    """
    records = load_records_from_events(events)
    by_uuid = build_uuid_index(records)

    # ── Step 1: collect user prompts ──────────────────────────────────────────
    prompts: Dict[str, Dict[str, Any]] = {}  # promptId -> record
    for r in records:
        if r.get("type") != "user":
            continue
        pid = r.get("promptId") or r.get("uuid")
        if not pid:
            continue
        message = r.get("message", {})
        content = message.get("content")

        # Use helper to extract text from both string and list format content
        prompt_text = get_text_content(content)

        if prompt_text and pid not in prompts:
            prompts[pid] = r

    # ── Step 2: collect tool_result records ───────────────────────────────────
    tool_results: Dict[str, Dict[str, Any]] = {}  # tool_use_id -> dict
    for r in records:
        if r.get("type") != "user":
            continue
        pid = r.get("promptId")
        if not pid:
            continue
        message = r.get("message", {})
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "tool_result":
                continue
            tuid = item.get("tool_use_id", "")
            if not tuid:
                continue
            # result content can be a string or a list
            raw = item.get("content", "")
            if isinstance(raw, list):
                result_text = " ".join(
                    x.get("text", "") for x in raw if isinstance(x, dict)
                ).strip()
            else:
                result_text = str(raw).strip()
            tool_results[tuid] = {
                "prompt_id": pid,
                "result_text": result_text,
                "is_error": item.get("is_error", False),
            }

    # ── Step 3: collect assistant records grouped by message.id ──────────────
    msg_id_to_records: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r.get("type") != "assistant":
            continue
        msg = r.get("message", {})
        if msg.get("role") != "assistant":
            continue
        mid = msg.get("id")
        if mid:
            msg_id_to_records[mid].append(r)

    # ── Step 4: merge streaming chunks for each message.id ────────────────────
    merged_msgs: Dict[str, Dict[str, Any]] = {}
    for mid, recs in msg_id_to_records.items():
        recs_sorted = sorted(recs, key=lambda x: x.get("timestamp", ""))
        # Get uuid and parentUuid from the first record
        first_rec = recs_sorted[0]
        merged = {
            "uuid": first_rec.get("uuid"),  # Store original event uuid
            "parent_uuid": first_rec.get("parentUuid"),  # Store original parent uuid
            "thinking_used": False,
            "thinking_input_tokens": 0,
            "thinking_output_tokens": 0,
            "thinking_cache_read_tokens": 0,
            "thinking_cache_creation_tokens": 0,
            "tool_uses": [],
            "tool_uuids": {},  # Map tool_use_id to event uuid
            "text": None,
            "stop_reason": None,
            "usage": {},
            "model": "",
            "timestamp": "",
            "message_id": mid,
        }
        seen_tool_ids = set()

        for r in recs_sorted:
            msg = r.get("message", {})
            merged["model"] = msg.get("model", "") or merged["model"]
            merged["timestamp"] = r.get("timestamp", "") or merged["timestamp"]
            content = msg.get("content", [])
            usage = msg.get("usage", {})
            # Store this chunk's uuid for tool_uses in this chunk
            chunk_uuid = r.get("uuid")
            chunk_parent_uuid = r.get("parentUuid")

            # Identify content types present in this chunk
            types_in_chunk = {
                item.get("type")
                for item in content
                if isinstance(item, dict)
            }

            # Thinking-only chunk → capture all 4 token fields from this chunk
            if types_in_chunk == {"thinking"} and usage:
                merged["thinking_used"] = True
                merged["thinking_input_tokens"] = usage.get("input_tokens", 0) or 0
                merged["thinking_output_tokens"] = usage.get("output_tokens", 0) or 0
                merged["thinking_cache_read_tokens"] = usage.get("cache_read_input_tokens", 0) or 0
                merged["thinking_cache_creation_tokens"] = usage.get("cache_creation_input_tokens", 0) or 0
                # Do NOT update merged["usage"] here — this is partial usage
                continue

            # All other chunks → last one's usage is the final/complete usage
            if usage:
                merged["usage"] = usage
            sr = msg.get("stop_reason")
            if sr:
                merged["stop_reason"] = sr

            for item in content:
                if not isinstance(item, dict):
                    continue
                t = item.get("type")
                if t == "tool_use":
                    tuid = item.get("id", "")
                    if tuid and tuid not in seen_tool_ids:
                        seen_tool_ids.add(tuid)
                        merged["tool_uses"].append({
                            "id": tuid,
                            "name": item.get("name", ""),
                            "input": item.get("input", {}),
                        })
                        # Store this chunk's uuid for this tool_use
                        if chunk_uuid:
                            merged["tool_uuids"][tuid] = {
                                "uuid": chunk_uuid,
                                "parent_uuid": chunk_parent_uuid,
                            }
                elif t == "text":
                    tx = item.get("text", "").strip()
                    if tx:
                        merged["text"] = tx

        merged_msgs[mid] = merged

    # ── Step 5: build tool_use_id -> merged message mapping ───────────────────
    tool_use_id_to_msg: Dict[str, tuple] = {}  # tool_use_id -> (message_id, merged_msg, tool_use)
    for mid, merged in merged_msgs.items():
        for tu in merged["tool_uses"]:
            tool_use_id_to_msg[tu["id"]] = (mid, merged, tu)

    # ── Step 6: find final text response per prompt ───────────────────────────
    prompt_to_response: Dict[str, Dict[str, Any]] = {}
    for mid, merged in merged_msgs.items():
        if not merged["text"]:
            continue
        if merged.get("stop_reason") != "end_turn":
            continue
        sample_rec = msg_id_to_records[mid][0]
        pid = find_prompt_id(sample_rec, by_uuid)
        if not pid:
            continue
        existing = prompt_to_response.get(pid)
        if existing is None or merged["timestamp"] > existing["timestamp"]:
            prompt_to_response[pid] = merged

    # ── Step 7: assemble tools list per prompt ────────────────────────────────
    prompt_to_tools: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_tool_per_prompt: Dict[str, set] = defaultdict(set)

    for tuid, tr in tool_results.items():
        pid = tr["prompt_id"]
        if tuid in seen_tool_per_prompt[pid]:
            continue
        seen_tool_per_prompt[pid].add(tuid)

        if tuid not in tool_use_id_to_msg:
            continue
        mid, merged, tu = tool_use_id_to_msg[tuid]
        usage = merged["usage"]

        # Get the uuid/parent_uuid for this specific tool_use
        tool_uuid_info = merged.get("tool_uuids", {}).get(tuid, {})
        tool_uuid = tool_uuid_info.get("uuid") or merged.get("uuid")
        tool_parent_uuid = tool_uuid_info.get("parent_uuid") or merged.get("parent_uuid")

        prompt_to_tools[pid].append({
            "tool_use_id": tuid,
            "tool_name": tu["name"],
            "tool_input": tu["input"],
            "tool_result": tr["result_text"],
            "is_error": tr["is_error"],
            "uuid": tool_uuid,  # Use specific uuid for this tool_use
            "parent_uuid": tool_parent_uuid,  # Use specific parent_uuid for this tool_use
            "thinking_used": merged["thinking_used"],
            "thinking_input_tokens": merged["thinking_input_tokens"],
            "thinking_output_tokens": merged["thinking_output_tokens"],
            "thinking_cache_read_tokens": merged["thinking_cache_read_tokens"],
            "thinking_cache_creation_tokens": merged["thinking_cache_creation_tokens"],
            "model": merged["model"],
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        })

    # ── Step 8: assemble final pairs ──────────────────────────────────────────
    pairs = []
    for pid, prompt_rec in prompts.items():
        response_merged = prompt_to_response.get(pid)
        if not response_merged:
            continue

        usage = response_merged["usage"]
        response_mid = response_merged.get("message_id", "")

        # Extract prompt text from content using helper function
        prompt_text = get_text_content(prompt_rec["message"]["content"]) or ""

        pairs.append({
            "prompt_id": pid,
            "message_id": response_mid,
            "session_id": prompt_rec.get("sessionId", ""),
            "timestamp": prompt_rec.get("timestamp", ""),
            "prompt": prompt_text,
            "response": response_merged["text"] or "",
            "model": response_merged["model"],
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
            "tools": prompt_to_tools.get(pid, []),
            # Include prompt record for UUID access
            "prompt_rec": prompt_rec,
            # Include response record for UUID access
            "response_rec": response_merged,
        })

    pairs.sort(key=lambda p: p["timestamp"])
    logger.info(f"Extracted {len(pairs)} prompt/response pairs")
    return pairs


def convert_pairs_to_db_format(pairs: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Convert prompt/response pairs to database format.

    Only includes pairs that have BOTH prompt text AND response text.
    Skips pairs where either is empty or missing.

    Returns dict with keys: user_prompts, responses, tools, thinking, io_tokens, tool_tokens
    """
    result = {
        "user_prompts": [],
        "responses": [],
        "tools": [],
        "thinking": [],
        "io_tokens": [],
        "tool_tokens": [],
    }

    for pair in pairs:
        pid = pair["prompt_id"]
        mid = pair["message_id"]

        # Extract prompt text from the prompt record using helper function
        prompt_rec = pair.get("prompt_rec", {})
        raw_content = prompt_rec.get("message", {}).get("content", "")

        prompt_text = get_text_content(raw_content) or ""
        response_text = pair.get("response", "").strip()

        if not prompt_text or not response_text:
            logger.debug(f"Skipping pair {pid}: missing prompt or response text")
            continue

        # User prompt - use the original event uuid and parent_uuid from the prompt record
        result["user_prompts"].append({
            "prompt_id": pid,
            "session_id": pair["session_id"],
            "uuid": prompt_rec.get("uuid", pid),  # Use original event uuid
            "parent_uuid": prompt_rec.get("parentUuid"),  # Use original parent uuid
            "prompt": prompt_text,
            "timestamp": pair["timestamp"],
        })

        # Response - use the original event uuid and parent_uuid from the response record
        response_rec = pair.get("response_rec", {})
        result["responses"].append({
            "message_id": mid,
            "prompt_id": pid,
            "uuid": response_rec.get("uuid", mid),  # Use original event uuid
            "parent_uuid": response_rec.get("parent_uuid"),  # Use original parent uuid
            "response_text": response_text,
            "model": pair["model"],
            "timestamp": pair["timestamp"],
        })

        # IO tokens
        result["io_tokens"].append({
            "id": f"{pid}_io_tokens",
            "prompt_id": pid,
            "message_id": mid,
            "token_type": "io",
            "input_tokens": pair["input_tokens"],
            "cache_creation_tokens": pair["cache_creation_tokens"],
            "cache_read_tokens": pair["cache_read_tokens"],
            "output_tokens": pair["output_tokens"],
        })

        # Tools and tool tokens
        for tool in pair["tools"]:
            tool_id = tool["tool_use_id"]

            result["tools"].append({
                "tool_id": tool_id,
                "prompt_id": pid,
                "uuid": tool.get("uuid", tool_id),  # Use assistant event uuid
                "parent_uuid": tool.get("parent_uuid"),  # Use assistant event parent uuid
                "tool_name": tool["tool_name"],
                "model": tool["model"],
                "input_json": json.dumps(tool["tool_input"]),
                "output_json": json.dumps({"result": tool["tool_result"], "is_error": tool["is_error"]}),
                "timestamp": pair["timestamp"],
            })

            result["tool_tokens"].append({
                "id": f"{tool_id}_tokens",
                "tool_id": tool_id,
                "input_tokens": tool["input_tokens"],
                "cache_creation_tokens": tool["cache_creation_tokens"],
                "cache_read_tokens": tool["cache_read_tokens"],
                "output_tokens": tool["output_tokens"],
            })

            # Thinking (if used)
            if tool["thinking_used"]:
                result["thinking"].append({
                    "thinking_id": f"{tool_id}_thinking",
                    "prompt_id": pid,
                    "uuid": tool.get("uuid", f"{tool_id}_thinking_uuid"),  # Use assistant event uuid
                    "parent_uuid": tool.get("parent_uuid"),  # Use assistant event parent uuid
                    "content": f"Thinking used for {tool['tool_name']}",
                    "signature": "",
                    "timestamp": pair["timestamp"],
                })

    logger.info(f"Converted {len(result['user_prompts'])} valid pairs (from {len(pairs)} total pairs)")
    return result
