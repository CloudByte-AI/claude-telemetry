"""Business logic for tool call pages."""

from ..queries import tools as tq
from ..queries import projects as pq
from ..queries import sessions as sq
from .utils import resolve_dates, paginate


def get_tools_page_context(
    dr: str, date_from: str, date_to: str,
    tool_search: str, sess_search: str, proj_search: str,
    tool_page: int, sess_page: int, proj_page: int,
    per_page: int,
) -> dict:
    d_from, d_to = resolve_dates(dr, date_from, date_to)

    # Chart data — date-filtered, top 5 + others
    all_tools   = list(tq.get_tools_stats_by_date(d_from, d_to))
    total_calls = sum(r["call_count"] for r in all_tools) or 1

    tool_rows_full = []
    for row in all_tools:
        d = dict(row)
        d["call_pct"]     = round(d["call_count"] / total_calls * 100)
        d["total_tokens"] = (d["input_tokens"] + d["output_tokens"] +
                             d["cache_read_tokens"] + d["cache_creation_tokens"])
        tool_rows_full.append(d)

    chart_rows = tool_rows_full[:5]
    others     = tool_rows_full[5:]
    if others:
        chart_rows = list(chart_rows) + [{
            "tool_name":             "others",
            "call_count":            sum(r["call_count"]             for r in others),
            "input_tokens":          sum(r["input_tokens"]           for r in others),
            "output_tokens":         sum(r["output_tokens"]          for r in others),
            "cache_read_tokens":     sum(r["cache_read_tokens"]      for r in others),
            "cache_creation_tokens": sum(r["cache_creation_tokens"]  for r in others),
        }]

    # Per-tool table — all time, searchable
    all_tools_full = list(tq.get_all_tools_stats())
    total_all      = sum(r["call_count"] for r in all_tools_full) or 1
    tool_rows_enriched = []
    for row in all_tools_full:
        d = dict(row)
        d["call_pct"] = round(d["call_count"] / total_all * 100)
        tool_rows_enriched.append(d)

    if tool_search.strip():
        filtered_tools = [r for r in tool_rows_enriched
                          if tool_search.lower() in r["tool_name"].lower()]
    else:
        filtered_tools = tool_rows_enriched
    tool_paged, tool_pg = paginate(filtered_tools, tool_page, per_page)

    sess_rows = list(tq.get_sessions_tool_breakdown(sess_search))
    sess_paged, sess_pg = paginate(sess_rows, sess_page, per_page)

    proj_rows = list(tq.get_projects_tool_breakdown(proj_search))
    proj_paged, proj_pg = paginate(proj_rows, proj_page, per_page)

    summary = tq.get_tools_overall_summary()

    return {
        "active":  "tools",
        "summary": dict(summary),
        "dr": dr, "d_from": d_from, "d_to": d_to,
        "chart_labels":     [r["tool_name"]            for r in chart_rows],
        "chart_calls":      [r["call_count"]           for r in chart_rows],
        "chart_inp":        [r["input_tokens"]         for r in chart_rows],
        "chart_out":        [r["output_tokens"]        for r in chart_rows],
        "chart_cache_read": [r["cache_read_tokens"]    for r in chart_rows],
        "chart_cache_cr":   [r["cache_creation_tokens"]for r in chart_rows],
        "tool_rows": tool_paged, "tool_search": tool_search, "tool_pg": tool_pg,
        "sess_rows": sess_paged, "sess_search": sess_search, "sess_pg": sess_pg,
        "proj_rows": proj_paged, "proj_search": proj_search, "proj_pg": proj_pg,
    }


def get_session_tool_context(session_id: str) -> dict | None:
    session = sq.get_session(session_id)
    if not session:
        return None

    # Use dict() so we can safely derive values — sqlite3.Row is read-only
    stats        = dict(tq.get_session_tool_counts(session_id))
    token_totals = dict(tq.get_session_tool_token_totals(session_id) or
                        {"input_tokens":0,"output_tokens":0,
                         "cache_read_tokens":0,"cache_creation_tokens":0})
    total_tool_tokens = sum(token_totals.values())

    tool_rows = list(tq.get_session_tools_breakdown(session_id))
    raw_count = stats["total_calls"] or 1

    tool_rows_enriched = []
    for row in tool_rows:
        d = dict(row)
        d["call_pct"] = round(d["call_count"] / raw_count * 100)
        tool_rows_enriched.append(d)

    turn_rows  = list(tq.get_session_turns_tool_breakdown(session_id))
    order_map  = tq.get_session_prompt_order(session_id)

    turn_rows_enriched = []
    for row in turn_rows:
        d = dict(row)
        d["turn_num"] = order_map.get(d["prompt_id"], "?")
        turn_rows_enriched.append(d)

    return {
        "active":            "tools",
        "session":           dict(session),
        "stats":             stats,
        "token_totals":      token_totals,
        "total_tool_tokens": total_tool_tokens,
        "tool_rows":         tool_rows_enriched,
        "turn_rows":         turn_rows_enriched,
        "chart_labels":     [r["tool_name"]           for r in tool_rows_enriched],
        "chart_calls":      [r["call_count"]          for r in tool_rows_enriched],
        "chart_inp":        [r["input_tokens"]        for r in tool_rows_enriched],
        "chart_out":        [r["output_tokens"]       for r in tool_rows_enriched],
        "chart_cache_read": [r["cache_read_tokens"]   for r in tool_rows_enriched],
        "chart_cache_cr":   [r["cache_creation_tokens"]for r in tool_rows_enriched],
        "bar_labels":       [f"Turn {r['turn_num']}"  for r in turn_rows_enriched],
        "bar_tool_count":   [r["tool_count"]           for r in turn_rows_enriched],
        "bar_tokens":       [r["turn_tokens"]          for r in turn_rows_enriched],
    }


def get_project_tool_context(project_id: str) -> dict | None:
    project = pq.get_project(project_id)
    if not project:
        return None

    stats        = dict(tq.get_project_tool_stats(project_id))
    token_totals = dict(tq.get_project_tool_token_totals(project_id) or
                        {"input_tokens":0,"output_tokens":0,
                         "cache_read_tokens":0,"cache_creation_tokens":0})

    tool_rows    = list(tq.get_project_tools_breakdown(project_id))
    session_rows = list(tq.get_project_sessions_tool_breakdown(project_id))

    raw_count = sum(r["call_count"] for r in tool_rows) or 1
    tool_rows_enriched = []
    for row in tool_rows:
        d = dict(row)
        d["call_pct"]     = round(d["call_count"] / raw_count * 100)
        d["total_tokens"] = (d["input_tokens"] + d["output_tokens"] +
                             d["cache_read_tokens"] + d["cache_creation_tokens"])
        tool_rows_enriched.append(d)

    proj_stats = {
        "total_calls":    raw_count,
        "unique_tools":   stats["unique_tools"] or 0,
        "total_sessions": len(session_rows),
        "active_turns":   stats["turns_with_tools"] or 0,
    }

    return {
        "active":            "tools",
        "project":           dict(project),
        "stats":             proj_stats,
        "token_totals":      token_totals,
        "total_tool_tokens": sum(token_totals.values()),
        "tool_rows":         tool_rows_enriched,
        "session_rows":      session_rows,
        "chart_labels":     [r["tool_name"]            for r in tool_rows_enriched],
        "chart_calls":      [r["call_count"]           for r in tool_rows_enriched],
        "chart_inp":        [r["input_tokens"]         for r in tool_rows_enriched],
        "chart_out":        [r["output_tokens"]        for r in tool_rows_enriched],
        "chart_cache_read": [r["cache_read_tokens"]    for r in tool_rows_enriched],
        "chart_cache_cr":   [r["cache_creation_tokens"]for r in tool_rows_enriched],
        "bar_labels":       [r["session_id"][:8]+"…"  for r in session_rows],
        "bar_tool_calls":   [r["tool_calls"]           for r in session_rows],
        "bar_tokens":       [
            (r["input_tokens"] or 0) + (r["output_tokens"] or 0) +
            (r["cache_read_tokens"] or 0) + (r["cache_creation_tokens"] or 0)
            for r in session_rows
        ],
        "bar_session_ids":  [r["session_id"]           for r in session_rows],
    }