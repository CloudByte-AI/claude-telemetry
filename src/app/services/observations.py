"""Business logic for observations and session summaries."""

import json
from ..queries import observations as oq
from .utils import paginate


# ── Type metadata ──────────────────────────────────────────────────────────────
TYPE_META = {
    "bugfix":    {"label": "Bug Fix",    "color": "red",    "icon": "✗"},
    "feature":   {"label": "Feature",   "color": "green",  "icon": "★"},
    "refactor":  {"label": "Refactor",  "color": "purple", "icon": "↻"},
    "change":    {"label": "Change",    "color": "accent", "icon": "◎"},
    "discovery": {"label": "Discovery", "color": "amber",  "icon": "◉"},
    "decision":  {"label": "Decision",  "color": "blue",   "icon": "◈"},
}

ALL_TYPES = list(TYPE_META.keys())


def _parse_json_field(val, default=None):
    if default is None:
        default = []
    try:
        return json.loads(val or "[]")
    except (json.JSONDecodeError, TypeError):
        return default


def _enrich_observation(row) -> dict:
    d = dict(row)
    d["facts"]          = _parse_json_field(d.get("facts"))
    d["concepts"]       = _parse_json_field(d.get("concepts"))
    d["files_read"]     = _parse_json_field(d.get("files_read"))
    d["files_modified"] = _parse_json_field(d.get("files_modified"))
    d["type_meta"]      = TYPE_META.get(d.get("type", ""), {"label": d.get("type", ""), "color": "accent", "icon": "◎"})
    return d


def get_observations_context(
    search: str = "",
    type_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = 15,
) -> dict:
    all_rows    = [_enrich_observation(r) for r in oq.get_observations_list(search, type_filter, date_from, date_to)]
    paged, pg   = paginate(all_rows, page, per_page)
    type_counts = {r["type"]: r["count"] for r in oq.get_observation_type_counts()}

    # Get session task counts (pending/failed/completed)
    session_task_counts = {}
    for row in oq.get_session_task_counts():
        session_id = row["session_id"]
        session_task_counts[session_id] = {
            "pending": row["pending"] or 0,
            "running": row["running"] or 0,
            "failed": row["failed"] or 0,
            "completed": row["completed"] or 0,
        }

    # Bubble chart data — session × type matrix
    bubble_raw  = list(oq.get_bubble_chart_data(date_from, date_to))
    # Build unique session labels (short ids)
    sessions_seen = []
    session_labels = {}
    for row in bubble_raw:
        sid = row["session_id"]
        if sid not in session_labels:
            session_labels[sid] = len(sessions_seen)
            sessions_seen.append(sid[:8] + "…")

    # One dataset per type
    type_colors = {
        "bugfix":    "rgba(220,80,80,0.75)",
        "feature":   "rgba(0,229,160,0.75)",
        "refactor":  "rgba(167,139,250,0.75)",
        "change":    "rgba(0,212,255,0.75)",
        "discovery": "rgba(255,179,71,0.75)",
        "decision":  "rgba(0,180,255,0.75)",
    }
    bubble_datasets = []
    for t in ALL_TYPES:
        points = []
        for row in bubble_raw:
            if row["type"] == t:
                points.append({
                    "x": session_labels[row["session_id"]],
                    "y": ALL_TYPES.index(t),
                    "r": min(6 + row["count"] * 4, 32),
                    "count": row["count"],
                    "session": row["session_id"][:8] + "…",
                    "project": row["project_name"] or "—",
                })
        if points:
            bubble_datasets.append({
                "label":           TYPE_META[t]["label"],
                "data":            points,
                "backgroundColor": type_colors.get(t, "rgba(122,154,184,0.75)"),
            })

    total = sum(len(r) for r in [all_rows])

    # Add task counts to each observation
    for obs in paged:
        session_id = obs.get("session_id")
        if session_id in session_task_counts:
            obs["task_counts"] = session_task_counts[session_id]
        else:
            obs["task_counts"] = {"pending": 0, "running": 0, "failed": 0, "completed": 0}

    return {
        "active":          "observations",
        "observations":    paged,
        "pg":              pg,
        "search":          search,
        "type_filter":     type_filter,
        "date_from":       date_from,
        "date_to":         date_to,
        "type_counts":     type_counts,
        "type_meta":       TYPE_META,
        "all_types":       ALL_TYPES,
        "total":           len(all_rows),
        "bubble_datasets": bubble_datasets,
        "bubble_x_labels": sessions_seen,
        "bubble_y_labels": [TYPE_META[t]["label"] for t in ALL_TYPES],
        "session_task_counts": session_task_counts,
    }


def get_observation_detail_context(obs_id: str) -> dict | None:
    row = oq.get_observation(obs_id)
    if not row:
        return None

    obs     = _enrich_observation(row)
    nearby  = [dict(r) for r in oq.get_nearby_observations(obs_id, obs["session_id"])]
    for n in nearby:
        n["type_meta"] = TYPE_META.get(n.get("type", ""), {"label": "", "color": "accent", "icon": "◎"})

    return {
        "active":      "observations",
        "obs":         obs,
        "nearby":      nearby,
        "type_meta":   TYPE_META,
    }