"""Business logic for observations and session summaries."""

import json
from datetime import datetime, timedelta
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
    
    # Clean Project Path (Robust)
    raw_p = d.get("project_name", "")
    if raw_p:
        cleaned = raw_p.replace('\\', '/').replace('--', '/')
        if '/' in cleaned:
            d["project_display"] = cleaned.split('/')[-1]
        elif '-' in cleaned:
            # Aggressive dash cleaning: CloudByte-plugin-claude-telemetry -> claude-telemetry
            parts = cleaned.split('-')
            if len(parts) > 2:
                d["project_display"] = "-".join(parts[-2:])
            else:
                d["project_display"] = cleaned
        else:
            d["project_display"] = cleaned
    else:
        d["project_display"] = "-"

    d["type_meta"] = TYPE_META.get(d.get("type", ""), {"label": d.get("type", ""), "color": "accent", "icon": "◎"})
    return d


def get_observations_context(
    search: str = "",
    type_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
    per_page: int = 15,
    client: str = None,
) -> dict:
    all_rows    = [_enrich_observation(r) for r in oq.get_observations_list(search, type_filter, date_from, date_to, client)]
    paged, pg   = paginate(all_rows, page, per_page)
    type_counts = {r["type"]: r["count"] for r in oq.get_observation_type_counts(client)}

    # Timeline Data
    end_dt = datetime.now()
    if date_to:
        try: end_dt = datetime.strptime(date_to, '%Y-%m-%d')
        except: pass
    
    start_dt = end_dt - timedelta(days=13)
    if date_from:
        try: start_dt = datetime.strptime(date_from, '%Y-%m-%d')
        except: pass

    timeline_labels = []
    curr = start_dt
    while curr <= end_dt:
        timeline_labels.append(curr.strftime('%Y-%m-%d'))
        curr += timedelta(days=1)
    
    timeline_data = [0] * len(timeline_labels)
    for obs in all_rows:
        if obs.get("created_at"):
            d_str = obs["created_at"][:10]
            if d_str in timeline_labels:
                idx = timeline_labels.index(d_str)
                timeline_data[idx] += 1

    # Donut Data
    donut_labels = [TYPE_META[t]["label"] for t in ALL_TYPES if type_counts.get(t,0) > 0]
    donut_values = [type_counts.get(t,0) for t in ALL_TYPES if type_counts.get(t,0) > 0]

    # Distinct sessions that actually produced observations - the denominator for
    # the "Avg Density" / "Unique Contexts" tiles. This used to be derived from
    # TASK_QUEUE (sessions that enqueued an LLM task), which drifted from reality
    # and counted every Cursor session as zero, since Cursor never enqueued anything.
    obs_sessions = len({r["session_id"] for r in all_rows if r.get("session_id")})

    return {
        "active":          "observations",
        "client_filter":   client or "all",
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
        
        "timeline_labels": timeline_labels,
        "timeline_values": timeline_data,
        "donut_labels":    donut_labels,
        "donut_values":    donut_values,
        "obs_sessions":    obs_sessions,
    }


def get_observation_detail_context(obs_id: str) -> dict | None:
    row = oq.get_observation(obs_id)
    if not row:
        return None

    obs     = _enrich_observation(row)
    nearby  = [_enrich_observation(r) for r in oq.get_nearby_observations(obs["session_id"])]

    return {
        "active":      "observations",
        "obs":         obs,
        "nearby":      nearby,
        "type_meta":   TYPE_META,
    }