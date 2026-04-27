"""Business logic for the dashboard page."""

from ..queries import dashboard as dq

ALL_OBS_TYPES = ["bugfix", "feature", "refactor", "change", "discovery", "decision"]
OBS_LABELS    = ["Bug Fix", "Feature", "Refactor", "Change", "Discovery", "Decision"]

# Intercom Monochromatic Orange Ramp
PROJ_COLORS = [
    ("rgba(254, 76, 2, 0.08)",  "rgba(254, 76, 2, 0.6)",  "#fe4c02"), # Fin Orange (Base)
    ("rgba(200, 50, 0, 0.08)",  "rgba(200, 50, 0, 0.6)",  "#c83200"), # Darker Orange
    ("rgba(255, 120, 40, 0.08)", "rgba(255, 120, 40, 0.6)", "#ff7828"), # Lighter Orange
    ("rgba(150, 30, 0, 0.08)",  "rgba(150, 30, 0, 0.6)",  "#961e00"), # Deep Burnt Orange
    ("rgba(255, 160, 80, 0.08)", "rgba(255, 160, 80, 0.6)", "#ffa050"), # Soft Orange
]


def _tok(v):
    v = v or 0
    if v >= 1_000_000: return f"{v/1_000_000:.1f}M"
    if v >= 1_000:     return f"{v/1_000:.1f}K"
    return str(v)


def _norm(values: list) -> list:
    """Normalise a list to 0-100 for radar chart display."""
    mx = max(values) if values else 1
    if mx == 0:
        return [0] * len(values)
    return [round(v / mx * 100) for v in values]


def get_dashboard_context() -> dict:
    stats = dict(dq.get_dashboard_stats())

    # ── Heatmap — raw day → prompt count dict passed to JS ───────────────────
    raw_heat   = list(dq.get_activity_heatmap())
    heat_data  = {r["day"]: r["prompts"] for r in raw_heat}
    heat_max   = max(heat_data.values()) if heat_data else 1
    # Pass all active months so JS can build the month navigator
    heat_months = sorted(set(d[:7] for d in heat_data.keys()))

    # ── Project Profile Radar: edges = Sessions, Prompts, Observations ────────
    proj_rows      = list(dq.get_projects_radar())
    proj_radar_datasets = []
    proj_labels    = ["Sessions", "Prompts", "Observations"]

    # Get max per axis for normalisation
    max_sessions = max((p["sessions"] for p in proj_rows), default=1) or 1
    max_prompts  = max((p["prompts"]  for p in proj_rows), default=1) or 1
    max_obs      = max((p["observations"] for p in proj_rows), default=1) or 1

    for i, p in enumerate(proj_rows):
        d   = dict(p)
        col = PROJ_COLORS[i % len(PROJ_COLORS)]
        proj_radar_datasets.append({
            "label":              d["project_name"],
            "data":               [
                round(d["sessions"]     / max_sessions * 100),
                round(d["prompts"]      / max_prompts  * 100),
                round(d["observations"] / max_obs      * 100),
            ],
            "raw":                [d["sessions"], d["prompts"], d["observations"]],
            "backgroundColor":    col[0],
            "borderColor":        col[1],
            "pointBackgroundColor": col[2],
            "pointRadius":        3,
            "borderWidth":        1.5,
        })

    # ── Observation Types Radar: edges = obs types, one dataset per project ───
    obs_rows = list(dq.get_obs_types_per_project())
    # Build {project: {type: count}}
    obs_by_proj: dict = {}
    for r in obs_rows:
        pn = r["project_name"] or "Unknown"
        obs_by_proj.setdefault(pn, {})
        obs_by_proj[pn][r["obs_type"]] = r["count"]

    obs_radar_datasets = []
    for i, (pname, type_counts) in enumerate(obs_by_proj.items()):
        raw    = [type_counts.get(t, 0) for t in ALL_OBS_TYPES]
        col    = PROJ_COLORS[i % len(PROJ_COLORS)]
        obs_radar_datasets.append({
            "label":              pname,
            "data":               _norm(raw),
            "raw":                raw,
            "backgroundColor":    col[0],
            "borderColor":        col[1],
            "pointBackgroundColor": col[2],
            "pointRadius":        3,
            "borderWidth":        1.5,
        })

    has_obs_data = any(sum(d["raw"]) > 0 for d in obs_radar_datasets) if obs_radar_datasets else False

    return {
        "active":               "dashboard",
        "stats":                stats,
        "total_tokens_fmt":     _tok(stats.get("total_tokens", 0)),
        # Heatmap
        "heat_data_json":       heat_data,
        "heat_max":             heat_max,
        "heat_months":          heat_months,
        # Project radar
        "proj_labels":          proj_labels,
        "proj_radar_datasets":  proj_radar_datasets,
        # Observation radar
        "obs_radar_datasets":   obs_radar_datasets,
        "obs_radar_labels":     OBS_LABELS,
        "has_obs_data":         has_obs_data,
        # Lists
        "recent_sessions":      list(dq.get_recent_sessions(5)),
        "latest_obs":           list(dq.get_latest_observations(5)),
    }