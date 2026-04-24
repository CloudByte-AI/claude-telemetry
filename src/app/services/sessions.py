"""Business logic for session-related pages."""

import json

from ..routers.db import build_tool_list
from ..queries import sessions as sq
from .utils import paginate


def get_sessions_list_context(search: str, page: int, per_page: int) -> dict:
    all_sessions = list(sq.get_sessions_list(search))
    total_records = len(all_sessions)
    total_pages   = max(1, (total_records + per_page - 1) // per_page)
    page          = max(1, min(page, total_pages))
    offset        = (page - 1) * per_page
    paged         = all_sessions[offset: offset + per_page]
    pg_start      = max(1, page - 2)
    pg_end        = min(total_pages, page + 2)
    return {
        "active":        "sessions",
        "sessions":      paged,
        "search":        search,
        "page":          page,
        "per_page":      per_page,
        "total_records": total_records,
        "total_pages":   total_pages,
        "page_nums":     list(range(pg_start, pg_end + 1)),
        "pg_start":      pg_start,
        "pg_end":        pg_end,
    }


def get_session_detail_context(session_id: str) -> dict | None:
    session = sq.get_session(session_id)
    if not session:
        return None

    prompts          = sq.get_session_prompts(session_id)
    turns            = []
    total_tool_count = 0

    for p in prompts:
        pid          = p["prompt_id"]
        tool_list    = build_tool_list(pid)
        response_row = sq.get_prompt_response(pid)
        tokens_row   = sq.get_prompt_io_tokens(pid)
        tool_tok_agg = sq.get_prompt_tool_tokens_agg(pid)

        total_tool_count += len(tool_list)
        turns.append({
            "prompt":          p["prompt"],
            "timestamp":       p["timestamp"],
            "prompt_id":       pid,
            "tool_list":       tool_list,
            "response":        dict(response_row)    if response_row    else None,
            "tokens":          dict(tokens_row)      if tokens_row      else None,
            "tool_tokens_agg": dict(tool_tok_agg)    if tool_tok_agg    else None,
        })

    obs_rows = sq.get_session_observations(session_id)
    observations = []
    obs_by_prompt = {}   # prompt_id → observation dict for per-turn buttons
    for o in obs_rows:
        d = dict(o)
        try:    d["facts_list"]    = json.loads(d.get("facts")    or "[]")
        except: d["facts_list"]    = []
        try:    d["concepts_list"] = json.loads(d.get("concepts") or "[]")
        except: d["concepts_list"] = []
        observations.append(d)
        if d.get("prompt_id"):
            obs_by_prompt[d["prompt_id"]] = d

    return {
        "active":           "sessions",
        "session":          dict(session),
        "turns":            turns,
        "total_tool_count": total_tool_count,
        "observations":     observations,
        "obs_by_prompt":    obs_by_prompt,
    }


def get_conversation_context(prompt_id: str) -> dict | None:
    prompt = sq.get_conversation_prompt(prompt_id)
    if not prompt:
        return None

    prompt           = dict(prompt)
    response_row     = sq.get_prompt_response(prompt_id)
    tokens_row       = sq.get_prompt_io_tokens(prompt_id)
    tool_list        = build_tool_list(prompt_id)
    tool_token_total = sum(
        (t["tokens"]["input_tokens"] or 0) + (t["tokens"]["output_tokens"] or 0)
        for t in tool_list if t["tokens"]
    )

    sibling_ids = sq.get_session_prompt_ids(prompt["session_id"])
    current_idx = sibling_ids.index(prompt_id) if prompt_id in sibling_ids else -1
    prev_id     = sibling_ids[current_idx - 1] if current_idx > 0                    else None
    next_id     = sibling_ids[current_idx + 1] if current_idx < len(sibling_ids) - 1 else None

    # Check if this prompt has an observation
    obs_row = sq.get_prompt_observation(prompt_id)
    observation = dict(obs_row) if obs_row else None

    return {
        "active":           "sessions",
        "prompt":           prompt,
        "response":         dict(response_row) if response_row else None,
        "tokens":           dict(tokens_row)   if tokens_row   else None,
        "tool_list":        tool_list,
        "tool_token_total": tool_token_total,
        "turn_number":      current_idx + 1,
        "total_turns":      len(sibling_ids),
        "prev_id":          prev_id,
        "next_id":          next_id,
        "observation":      observation,
    }