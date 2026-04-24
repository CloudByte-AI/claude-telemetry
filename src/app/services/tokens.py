"""Business logic for token usage pages."""

from ..queries import tokens as tq
from ..queries import projects as pq
from .utils import resolve_dates, paginate, sum_token_row


def get_token_usage_context(
    dr: str, date_from: str, date_to: str,
    sess_search: str, proj_search: str,
    sess_page: int, proj_page: int, per_page: int,
) -> dict:
    d_from, d_to = resolve_dates(dr, date_from, date_to)

    # Chart data — date-filtered
    io_t   = tq.get_io_totals_by_date(d_from, d_to)
    tool_t = tq.get_tool_totals_by_date(d_from, d_to)

    chart_labels = ["IO Input", "IO Output", "IO Cache Rd",
                    "Tool Input", "Tool Output", "Tool Cache Rd"]
    chart_values = [
        io_t["inp"] or 0, io_t["out"] or 0, io_t["cr"] or 0,
        tool_t["inp"] or 0, tool_t["out"] or 0, tool_t["cr"] or 0,
    ]

    chart_sessions = tq.get_chart_sessions_by_date(d_from, d_to, limit=10)
    bar_labels, bar_io, bar_tool, bar_sess_ids = [], [], [], []
    for row in chart_sessions:
        sid  = row["session_id"]
        tt   = tq.get_session_tool_total(sid)
        bar_labels.append(sid[:8] + "…")
        bar_io.append(row["io_total"])
        bar_tool.append(tt["total"] if tt else 0)
        bar_sess_ids.append(sid)

    # Session breakdown — all sessions, IO only
    sess_rows       = list(tq.get_sessions_io_breakdown(sess_search))
    sess_paged, sess_pg = paginate(sess_rows, sess_page, per_page)

    # Project breakdown — all projects, IO only
    proj_rows       = list(tq.get_projects_io_breakdown(proj_search))
    proj_paged, proj_pg = paginate(proj_rows, proj_page, per_page)

    return {
        "active": "tokens",
        "dr": dr, "date_from": date_from, "date_to": date_to,
        "d_from": d_from, "d_to": d_to,
        "chart_labels": chart_labels, "chart_values": chart_values,
        "bar_labels": bar_labels, "bar_io": bar_io,
        "bar_tool": bar_tool, "bar_sess_ids": bar_sess_ids,
        "sess_rows": sess_paged, "sess_search": sess_search, "sess_pg": sess_pg,
        "proj_rows": proj_paged, "proj_search": proj_search, "proj_pg": proj_pg,
    }


def get_session_token_context(session_id: str) -> dict | None:
    from ..queries import sessions as sq
    from ..queries import tools as toolq

    session = sq.get_session(session_id)
    if not session:
        return None

    io_t       = tq.get_session_io_totals(session_id)
    tool_t     = tq.get_session_tool_totals(session_id)
    io_total   = sum_token_row(io_t)
    tool_total = sum_token_row(tool_t)

    tool_stats = toolq.get_session_tool_stats(session_id)
    turn_rows  = tq.get_session_turn_io_breakdown(session_id)
    order_map  = tq.get_session_prompt_order(session_id)

    turn_data = []
    for row in turn_rows:
        d = dict(row)
        d["turn_num"] = order_map.get(d["prompt_id"], "?")
        d["total"]    = (d["input_tokens"] or 0) + (d["output_tokens"] or 0) + \
                        (d["cache_read_tokens"] or 0) + (d["cache_creation_tokens"] or 0)
        turn_data.append(d)

    donut_labels = ["IO Input", "IO Output", "IO Cache Rd",
                    "Tool Input", "Tool Output", "Tool Cache Rd"]
    donut_values = [
        io_t["inp"] or 0, io_t["out"] or 0, io_t["cr"] or 0,
        tool_t["inp"] or 0, tool_t["out"] or 0, tool_t["cr"] or 0,
    ]

    return {
        "active":           "tokens",
        "session":          dict(session),
        "io_total":         io_total,
        "tool_total":       tool_total,
        "grand_total":      io_total + tool_total,
        "io_t":             dict(io_t),
        "tool_t":           dict(tool_t),
        "turns_with_tools": tool_stats["turns_with_tools"] or 0,
        "unique_tools":     tool_stats["unique_tools"] or 0,
        "turn_data":        turn_data,
        "donut_labels":     donut_labels,
        "donut_values":     donut_values,
        "chart_turn_labels": [f"T{r['turn_num']}" for r in turn_data],
        "chart_inp": [r["input_tokens"] or 0            for r in turn_data],
        "chart_out": [r["output_tokens"] or 0           for r in turn_data],
        "chart_cr":  [r["cache_read_tokens"] or 0       for r in turn_data],
        "chart_cc":  [r["cache_creation_tokens"] or 0   for r in turn_data],
    }


def get_project_token_context(project_id: str) -> dict | None:
    from ..queries import tools as toolq

    project = pq.get_project(project_id)
    if not project:
        return None

    io_t       = tq.get_project_io_totals(project_id)
    tool_t     = tq.get_project_tool_totals(project_id)
    io_total   = sum_token_row(io_t)
    tool_total = sum_token_row(tool_t)

    proj_tool_stats = toolq.get_project_tool_stats(project_id)
    session_rows    = tq.get_project_sessions_token_breakdown(project_id)

    sess_list = []
    for row in session_rows:
        d = dict(row)
        d["io_total"]    = d["io_input"] + d["io_output"] + d["io_cr"] + d["io_cc"]
        d["tool_total"]  = d["tool_input"] + d["tool_output"] + d["tool_cr"] + d["tool_cc"]
        d["grand_total"] = d["io_total"] + d["tool_total"]
        sess_list.append(d)

    donut_labels = ["IO Input", "IO Output", "IO Cache Rd",
                    "Tool Input", "Tool Output", "Tool Cache Rd"]
    donut_values = [
        io_t["inp"] or 0, io_t["out"] or 0, io_t["cr"] or 0,
        tool_t["inp"] or 0, tool_t["out"] or 0, tool_t["cr"] or 0,
    ]

    return {
        "active":           "tokens",
        "project":          dict(project),
        "io_total":         io_total,
        "tool_total":       tool_total,
        "grand_total":      io_total + tool_total,
        "io_t":             dict(io_t),
        "tool_t":           dict(tool_t),
        "turns_with_tools": proj_tool_stats["turns_with_tools"] or 0,
        "unique_tools":     proj_tool_stats["unique_tools"] or 0,
        "session_rows":     sess_list,
        "donut_labels":     donut_labels,
        "donut_values":     donut_values,
        "bar_labels":       [r["session_id"][:8] + "…" for r in sess_list],
        "bar_io":           [r["io_total"]              for r in sess_list],
        "bar_tool":         [r["tool_total"]             for r in sess_list],
        "bar_sess_ids":     [r["session_id"]             for r in sess_list],
    }