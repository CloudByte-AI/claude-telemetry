"""Business logic for the security scanning UI."""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from ..queries.security import get_scan_events, get_scan_stats, get_recent_events

SECURITY_CONFIG_PATH = Path.home() / ".cloudbyte" / "security_profile.yaml"
PROFILES_DIR = Path(__file__).parent.parent.parent / "security" / "profiles"


# ── Config I/O ────────────────────────────────────────────────────────────────

def load_security_yaml() -> dict:
    """Load raw YAML as dict. Returns {} if not set up yet."""
    if not SECURITY_CONFIG_PATH.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(SECURITY_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_security_yaml(data: dict) -> tuple[bool, str]:
    """Write config dict to YAML. Returns (success, message)."""
    try:
        import yaml
        SECURITY_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        SECURITY_CONFIG_PATH.write_text(
            yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return True, "Security settings saved."
    except Exception as e:
        return False, f"Failed to save: {e}"


def load_preset(name: str) -> dict:
    """Load a preset YAML file by name (minimal / standard / strict)."""
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# ── Context builders ──────────────────────────────────────────────────────────

def get_security_context() -> dict:
    """Full context for the /security settings page."""
    from src.security.detector_registry import DETECTORS, get_detector_info_by_category

    cfg = load_security_yaml()
    enabled       = bool(cfg.get("enabled", False))
    plan          = cfg.get("plan", "standard")
    scope         = cfg.get("scope", "both")
    # New format: flat {CATEGORY: bool} dict
    categories_cfg: dict = cfg.get("categories", {})
    custom_patterns: list  = cfg.get("custom_patterns", [])
    keyword_blocklist: list = cfg.get("keyword_blocklist", [])
    allowlist: list         = cfg.get("allowlist", [])

    # Legacy compat: old configs stored entropy under entropy.enabled
    if "categories" not in cfg and "entropy" in cfg:
        categories_cfg["Entropy Secret"] = bool(
            (cfg.get("entropy") or {}).get("enabled", False)
        )

    entropy_enabled = bool(categories_cfg.get("Entropy Secret", False))

    # Per-detector info: types covered and example value
    det_info = get_detector_info_by_category()

    # Build flat detector state list (Entropy shown separately in its own card)
    PII_DOMAIN = "PII"
    detector_states = []
    for det in DETECTORS:
        if det.key == "Entropy Secret":
            continue
        info = det_info.get(det.key, {})
        is_on = bool(categories_cfg.get(det.key, det.default))
        detector_states.append({
            "key":          det.key,
            "name":         det.key,
            "description":  det.description,
            "domain":       det.domain,
            "default":      det.default,
            "enabled":      is_on,
            "types":        info.get("types", []),
            "example":      info.get("example", ""),
            "type_details": info.get("type_details", []),
        })

    # Group non-PII detectors by domain
    domains: dict[str, list] = {}
    for d in detector_states:
        if d["domain"] == PII_DOMAIN:
            continue
        domains.setdefault(d["domain"], []).append(d)

    grouped   = [{"category": domain, "detectors": items}
                 for domain, items in sorted(domains.items())]
    pii_detectors = [d for d in detector_states if d["domain"] == PII_DOMAIN]

    stats: dict = {}
    try:
        stats = get_scan_stats()
    except Exception:
        pass

    plan_modified = False
    if enabled and plan:
        try:
            preset = load_preset(plan)
            plan_modified = preset.get("categories", {}) != categories_cfg
        except Exception:
            pass

    # Total token types across all detectors (for hero display)
    total_types = sum(len(v.get("types", [])) for v in det_info.values())
    total_categories = len(DETECTORS)

    # Per-preset: count of enabled categories (used for plan card specs)
    preset_counts: dict[str, int] = {}
    for pname in ("minimal", "standard", "strict"):
        try:
            p = load_preset(pname)
            preset_counts[pname] = sum(1 for v in p.get("categories", {}).values() if v)
        except Exception:
            preset_counts[pname] = 0

    return {
        "active":            "security",
        "enabled":           enabled,
        "plan":              plan,
        "plan_modified":     plan_modified,
        "scope":             scope,
        "grouped_detectors": grouped,
        "pii_detectors":     pii_detectors,
        "entropy":           {"enabled": entropy_enabled},
        "custom_patterns":   custom_patterns,
        "keyword_blocklist": keyword_blocklist,
        "allowlist":         allowlist,
        "stats":             stats,
        "total_types":       total_types,
        "total_categories":  total_categories,
        "preset_counts":     preset_counts,
        "config_path":       str(SECURITY_CONFIG_PATH),
        "save_success":      None,
        "save_message":      None,
    }


def _time_ago(ts_str: str) -> str:
    """Return human-readable relative time from an ISO timestamp string."""
    if not ts_str:
        return ""
    try:
        from datetime import datetime, timezone
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        diff = int((datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds())
        if diff < 60:     return "just now"
        if diff < 3600:   return f"{diff // 60}m ago"
        if diff < 86400:  return f"{diff // 3600}h ago"
        if diff < 604800: return f"{diff // 86400}d ago"
        return ts.strftime("%b %d")
    except Exception:
        return ""


def _format_detector_name(raw: str) -> str:
    """Convert detector label/category to readable name: AWS_ACCESS_KEY → AWS Access Key"""
    return raw.replace("_", " ").title()


def _period_clause(period: str) -> str:
    if period == "today":
        return "date(timestamp) = date('now')"
    if period == "30d":
        return "timestamp >= datetime('now', '-30 days')"
    if period == "all":
        return "1=1"
    return "timestamp >= datetime('now', '-7 days')"


def _compute_posture(stats: dict) -> tuple[str, int]:
    blocked = stats.get("blocked", 0)
    total   = stats.get("total", 0)
    if total == 0:    return "No activity recorded", 0
    if blocked == 0:  return "No threats detected",  1
    if blocked <= 2:  return "Low threat activity",   2
    if blocked <= 5:  return "Moderate activity",     3
    if blocked <= 10: return "Active threats blocked", 4
    return "High threat frequency", 5


def _finding_label(f: dict) -> str:
    """Extract display label from a finding dict — handles both old and new formats."""
    return f.get("label") or f.get("detector") or f.get("category") or ""


def _get_threat_breakdown(period: str = "7d", limit: int = 8) -> list[dict]:
    from ..routers.db import q as _q
    clause = _period_clause(period)
    try:
        rows = _q(
            f"SELECT findings_json FROM SECURITY_SCAN_EVENT WHERE {clause} AND findings_json IS NOT NULL"
        )
        counts: dict[str, int] = {}
        for row in rows:
            try:
                for f in json.loads(row["findings_json"] or "[]"):
                    det = _finding_label(f)
                    if det:
                        counts[det] = counts.get(det, 0) + 1
            except Exception:
                pass
        sorted_dets = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
        max_c = sorted_dets[0][1] if sorted_dets else 1
        return [
            {
                "detector":     det,
                "display_name": _format_detector_name(det),
                "count":        cnt,
                "pct":          int(cnt / max_c * 100),
            }
            for det, cnt in sorted_dets
        ]
    except Exception:
        return []


def _get_daily_activity(period_days: int = 7) -> list[dict]:
    from datetime import datetime, timezone, timedelta
    from ..routers.db import q as _q
    today = datetime.now(timezone.utc).date()
    days  = []
    for i in range(period_days - 1, -1, -1):
        d  = today - timedelta(days=i)
        ds = d.isoformat()
        try:
            br  = _q("SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT WHERE date(timestamp)=? AND blocked=1",   (ds,), one=True)
            dr  = _q("SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT WHERE date(timestamp)=? AND blocked=0",   (ds,), one=True)
            b   = (dict(br)["n"]  if br  else 0)
            det = (dict(dr)["n"]  if dr  else 0)
        except Exception:
            b = det = 0
        days.append({
            "date":     ds,
            "label":    d.strftime("%b ") + str(d.day),
            "blocked":  b,
            "detected": det,
            "total":    b + det,
        })
    return days


def _generate_insights(
    period: str,
    stats: dict,
    threat_breakdown: list[dict],
) -> list[dict]:
    """Generate 4-6 narrative intelligence bullets from aggregated event data."""
    from ..routers.db import q as _q
    clause  = _period_clause(period)
    total   = stats.get("total", 0)
    blocked = stats.get("blocked", 0)
    resp    = stats.get("response_hits", 0)
    bpct    = stats.get("blocked_pct", 0)

    if total == 0:
        return []

    items: list[dict] = []

    # ── 1. Project / session context ──────────────────────────
    try:
        proj_rows = _q(
            f"""
            SELECT
                COALESCE(p.name, p.path, s.cwd, 'Unknown') AS project_name,
                COUNT(e.event_id) AS event_count,
                SUM(e.blocked)    AS blocked_count
            FROM SECURITY_SCAN_EVENT e
            LEFT JOIN SESSION s  ON e.session_id = s.session_id
            LEFT JOIN PROJECT p  ON s.project_id = p.project_id
            WHERE {clause}
            GROUP BY s.project_id
            ORDER BY event_count DESC
            LIMIT 3
            """
        )
        total_projects = len(proj_rows)
        if proj_rows:
            top = dict(proj_rows[0])
            pname  = (top.get("project_name") or "").split("/")[-1].split("\\")[-1] or "a project"
            pcount = int(top.get("event_count") or 0)
            pblk   = int(top.get("blocked_count") or 0)
            if total_projects == 1:
                items.append({
                    "level": "high",
                    "tag":   "PROJECT",
                    "text":  f"All events this period originated from <b>{pname}</b>. "
                             f"<b>{pblk}</b> of {pcount} scan{'s' if pcount != 1 else ''} "
                             f"triggered a block.",
                })
            else:
                items.append({
                    "level": "high",
                    "tag":   "PROJECT",
                    "text":  f"<b>{pname}</b> was your highest-risk project with <b>{pcount} events</b> "
                             f"({pblk} blocked) across <b>{total_projects} projects</b> scanned this period.",
                })
    except Exception:
        pass

    # ── 2. Top threat ─────────────────────────────────────────
    if threat_breakdown:
        top = threat_breakdown[0]
        items.append({
            "level": "high",
            "tag":   "TOP THREAT",
            "text":  f"<b>{top['display_name']}</b> was the most triggered detector. "
                     f"It fired <b>{top['count']}×</b> and made up <b>{top['pct']}%</b> of all findings. "
                     + ("This is your primary exposure vector." if top["pct"] > 60
                        else "Multiple threat types were active this period."),
        })

    # ── 3. Block effectiveness ────────────────────────────────
    if blocked > 0:
        if bpct == 100:
            items.append({
                "level": "green",
                "tag":   "BLOCKED",
                "text":  f"<b>100% interception rate.</b> All {blocked} detected "
                         f"secret{'s' if blocked != 1 else ''} "
                         f"{'were' if blocked != 1 else 'was'} stopped before reaching Claude.",
            })
        else:
            missed = total - blocked
            tail   = ("Review response scanning coverage." if resp == 0
                      else "Some were caught downstream by response scanning.")
            items.append({
                "level": "amber",
                "tag":   "BLOCKED",
                "text":  f"<b>{blocked}</b> of {total} scans resulted in a block ({bpct}% block rate). "
                         f"<b>{missed}</b> scan{'s' if missed != 1 else ''} passed through. {tail}",
            })

    # ── 4. Response scanning ──────────────────────────────────
    if resp > 0:
        items.append({
            "level": "amber",
            "tag":   "RESPONSE",
            "text":  f"Response scanning surfaced <b>{resp}</b> credential "
                     f"{'exposures' if resp != 1 else 'exposure'} inside Claude's replies. "
                     f"These were logged as warnings. Consider reviewing what context you send.",
        })
    else:
        items.append({
            "level": "green",
            "tag":   "RESPONSE",
            "text":  "Claude's responses were clean this period. "
                     "No secrets detected in any reply. Response scanning is working as expected.",
        })

    # ── 5. Scanner performance ────────────────────────────────
    try:
        perf = _q(
            f"SELECT ROUND(AVG(scan_ms)) as avg_ms, MAX(scan_ms) as max_ms "
            f"FROM SECURITY_SCAN_EVENT WHERE {clause}", one=True
        )
        if perf:
            p      = dict(perf)
            avg_ms = int(p.get("avg_ms") or 0)
            max_ms = int(p.get("max_ms") or 0)
            level  = "green" if avg_ms < 100 else "amber"
            note   = ("No noticeable impact on your session flow."
                      if avg_ms < 100
                      else "Larger prompts trigger chunked scanning. This is expected behaviour.")
            items.append({
                "level": level,
                "tag":   "PERFORMANCE",
                "text":  f"Scanner averaged <b>{avg_ms}ms</b> per check, peaking at <b>{max_ms}ms</b>. {note}",
            })
    except Exception:
        pass

    # ── 6. Last event timeline ────────────────────────────────
    try:
        last = _q(
            f"SELECT timestamp FROM SECURITY_SCAN_EVENT WHERE {clause} ORDER BY timestamp DESC LIMIT 1", one=True
        )
        if last:
            ago = _time_ago(dict(last).get("timestamp", ""))
            if ago and ago != "just now":
                items.append({
                    "level": "blue",
                    "tag":   "TIMELINE",
                    "text":  f"Most recent security event was <b>{ago}</b>. "
                             "Scanner has been continuously monitoring all subsequent prompts.",
                })
    except Exception:
        pass

    return items


def _get_chart_data(period: str, daily_activity: list[dict]) -> list[dict]:
    from datetime import datetime
    if not daily_activity:
        return []

    if period == "today":
        d = daily_activity[-1]
        return [{"label": "Today", "blocked": d.get("blocked", 0), "detected": d.get("detected", 0)}]

    if period == "7d":
        result = []
        for d in daily_activity:
            try:
                dt      = datetime.fromisoformat(d["date"])
                weekday = dt.weekday()
                label   = dt.strftime("%a")
            except Exception:
                weekday = 7
                label   = d.get("label", "?")
            result.append({
                "label":    label,
                "blocked":  d.get("blocked", 0),
                "detected": d.get("detected", 0),
                "_wd":      weekday,
            })
        result.sort(key=lambda x: x["_wd"])
        for r in result:
            r.pop("_wd")
        return result

    n = len(daily_activity)
    buckets = [{"blocked": 0, "detected": 0} for _ in range(4)]
    for i, d in enumerate(daily_activity):
        days_ago = n - 1 - i
        b_idx = min(days_ago // 7, 3)
        buckets[b_idx]["blocked"]  += d.get("blocked", 0)
        buckets[b_idx]["detected"] += d.get("detected", 0)

    weeks = [
        {"label": f"W{4 - i}", "blocked": buckets[3 - i]["blocked"], "detected": buckets[3 - i]["detected"]}
        for i in range(4)
    ]
    while len(weeks) > 1 and weeks[0]["blocked"] == 0 and weeks[0]["detected"] == 0:
        weeks.pop(0)
    return weeks


def get_events_context(
    period: str = "7d",
    scan_target: str | None = None,
    blocked_only: bool = False,
    page: int = 1,
    per_page: int = 25,
) -> dict:
    """Context for the /security/events page."""
    from ..routers.db import q as _q

    offset     = (page - 1) * per_page
    events_raw = get_scan_events(
        limit=per_page,
        offset=offset,
        scan_target=scan_target,
        blocked_only=blocked_only,
    )

    events = []
    for ev in events_raw:
        findings = []
        try:
            findings = json.loads(ev.get("findings_json") or "[]")
        except Exception:
            pass
        is_blocked = bool(ev.get("blocked"))
        ev["status_label"]    = "BLOCKED"     if is_blocked else "DETECTED"
        ev["status_class"]    = "evt-blocked" if is_blocked else "evt-detected"
        ev["findings_parsed"] = findings
        ev["top_detectors"]   = [
            _format_detector_name(_finding_label(f)) for f in findings[:3]
        ]
        ev["extra_count"]  = max(0, ev.get("finding_count", 0) - len(ev["top_detectors"]))
        ev["time_ago"]     = _time_ago(ev.get("timestamp", ""))
        ph = ev.get("prompt_hash") or ""
        ev["hash_short"]   = ph[:12] + "…" if len(ph) > 12 else ph
        events.append(ev)

    stats: dict = {}
    clause = _period_clause(period)
    try:
        total_r = _q(f"SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT WHERE {clause}", one=True)
        blk_r   = _q(f"SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT WHERE {clause} AND blocked=1", one=True)
        resp_r  = _q(f"SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT WHERE {clause} AND scan_target='response'", one=True)
        total   = (dict(total_r)["n"] if total_r else 0)
        blocked = (dict(blk_r)["n"]   if blk_r   else 0)
        resp    = (dict(resp_r)["n"]   if resp_r  else 0)
        stats   = {
            "total":          total,
            "blocked":        blocked,
            "response_hits":  resp,
            "blocked_pct":    round(blocked * 100 / total) if total else 0,
            "prompt_count":   total - resp,
            "response_count": resp,
        }
    except Exception:
        pass

    posture_label, posture_level = "No activity recorded", 0
    try:
        posture_label, posture_level = _compute_posture(stats)
    except Exception:
        pass

    threat_breakdown: list = []
    try:
        threat_breakdown = _get_threat_breakdown(period)
    except Exception:
        pass

    period_days = {"today": 1, "7d": 7, "30d": 30, "all": 30}.get(period, 7)
    daily_activity: list = []
    max_daily = 1
    try:
        daily_activity = _get_daily_activity(period_days)
        max_daily = max((d["total"] for d in daily_activity), default=1) or 1
    except Exception:
        pass

    blocked_count  = sum(1 for ev in events if ev.get("blocked"))
    detected_count = len(events) - blocked_count
    has_more       = len(events) == per_page

    return {
        "active":            "security",
        "period":            period,
        "events":            events,
        "stats":             stats,
        "page":              page,
        "per_page":          per_page,
        "has_more":          has_more,
        "scan_target":       scan_target or "",
        "blocked_only":      blocked_only,
        "posture_label":     posture_label,
        "posture_level":     posture_level,
        "threat_breakdown":  threat_breakdown,
        "daily_activity":    daily_activity,
        "max_daily":         max_daily,
        "blocked_count":     blocked_count,
        "detected_count":    detected_count,
        "chart_data":        _get_chart_data(period, daily_activity),
        "security_insights": _generate_insights(period, stats, threat_breakdown),
    }


# ── Config save ───────────────────────────────────────────────────────────────

def save_from_form(form: dict) -> tuple[bool, str]:
    """
    Build a security config dict from form POST data and write to YAML.
    All detector toggles arrive as 'det_<CATEGORY_with_spaces_as_underscores>' = '1' | '0'.
    Entropy has its own 'entropy_enabled' field (separate toggle card in UI).
    """
    from src.security.detector_registry import DETECTORS

    enabled = form.get("enabled") == "1"
    plan    = form.get("plan", "standard")

    # Hero-enable path: enabled=1 but no det_* inputs (chips not rendered yet)
    has_det_inputs = any(k.startswith("det_") for k in form)
    if enabled and not has_det_inputs:
        return apply_preset(plan)

    scope = form.get("scope", "both")

    # Build categories dict — all detectors use det_ prefix with spaces→_
    categories: dict = {}
    for det in DETECTORS:
        if det.key == "Entropy Secret":
            continue
        field_key = f"det_{det.key.replace(' ', '_')}"
        val = form.get(field_key)
        if val is not None:
            categories[det.key] = (val == "1")

    # Entropy has its own toggle separate from the chip grid
    categories["Entropy Secret"] = form.get("entropy_enabled") == "1"

    # Preserve custom patterns from existing config (edited via modal)
    existing = load_security_yaml()
    custom_patterns   = existing.get("custom_patterns", [])
    keyword_blocklist = _parse_keywords(form.get("keyword_blocklist", ""))
    allowlist         = _parse_list(form.get("allowlist", ""))

    cfg = {
        "enabled":           enabled,
        "plan":              plan,
        "scope":             scope,
        "categories":        categories,
        "custom_patterns":   custom_patterns,
        "keyword_blocklist": keyword_blocklist,
        "allowlist":         allowlist,
    }

    return save_security_yaml(cfg)


def apply_preset(preset_name: str) -> tuple[bool, str]:
    """Load a preset, preserve user's custom patterns / blocklist / allowlist, and save."""
    preset = load_preset(preset_name)
    if not preset:
        return False, f"Preset '{preset_name}' not found."
    existing = load_security_yaml()
    preset["custom_patterns"]   = existing.get("custom_patterns", [])
    preset["keyword_blocklist"] = existing.get("keyword_blocklist", [])
    preset["allowlist"]         = existing.get("allowlist", [])
    return save_security_yaml(preset)


def add_custom_pattern(name: str, pattern: str = "", examples: list[str] | None = None,
                       severity: str = "HIGH") -> tuple[bool, str]:
    """Append a new custom pattern to the existing config."""
    cfg = load_security_yaml()
    patterns: list = cfg.get("custom_patterns", [])
    entry: dict = {"name": name, "severity": severity.upper()}
    if examples:
        entry["examples"] = [e.strip() for e in examples if e.strip()]
        if pattern:
            entry["pattern"] = pattern
    elif pattern:
        entry["pattern"] = pattern
    else:
        return False, "Provide either examples or a pattern."
    patterns.append(entry)
    cfg["custom_patterns"] = patterns
    return save_security_yaml(cfg)


def update_custom_pattern(old_name: str, name: str, pattern: str = "",
                          examples: list[str] | None = None,
                          severity: str = "HIGH") -> tuple[bool, str]:
    """Replace an existing custom pattern by old_name."""
    cfg = load_security_yaml()
    patterns: list = cfg.get("custom_patterns", [])
    idx = next((i for i, p in enumerate(patterns) if p.get("name") == old_name), None)
    if idx is None:
        return False, f"Pattern '{old_name}' not found."
    entry: dict = {"name": name, "severity": severity.upper()}
    if examples:
        entry["examples"] = [e.strip() for e in examples if e.strip()]
        if pattern:
            entry["pattern"] = pattern
    elif pattern:
        entry["pattern"] = pattern
    else:
        return False, "Provide either examples or a pattern."
    patterns[idx] = entry
    cfg["custom_patterns"] = patterns
    return save_security_yaml(cfg)


def remove_custom_pattern(name: str) -> tuple[bool, str]:
    """Remove a custom pattern by name."""
    cfg = load_security_yaml()
    before = len(cfg.get("custom_patterns", []))
    cfg["custom_patterns"] = [
        p for p in cfg.get("custom_patterns", [])
        if p.get("name") != name
    ]
    if len(cfg["custom_patterns"]) == before:
        return False, f"Pattern '{name}' not found."
    return save_security_yaml(cfg)


# ── Pattern builder API ───────────────────────────────────────────────────────

def generate_pattern_preview(name: str, examples: list[str], severity: str = "HIGH") -> dict:
    """Call the pattern builder and return a JSON-serialisable result."""
    try:
        from src.security.pattern_builder import analyze_examples
        result = analyze_examples(name=name, examples=examples, severity=severity)
        return {
            "ok":                    True,
            "pattern":               result.pattern,
            "confidence":            result.confidence,
            "confidence_reason":     result.confidence_reason,
            "false_positive_risk":   result.false_positive_risk,
            "fp_risk_reason":        result.fp_risk_reason,
            "alternatives":          result.alternatives,
            "examples_matched":      result.examples_matched,
            "examples_total":        result.examples_total,
            "warnings":              result.warnings,
            "codebase_fp_count":     result.codebase_fp_count,
            "codebase_files_checked": result.codebase_files_checked,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _float(val, default: float) -> float:
    try:    return float(val)
    except: return default


def _parse_keywords(raw: str) -> list[str]:
    """Parse a comma-separated or newline-separated keyword string."""
    if not raw:
        return []
    parts = re.split(r"[,\n]+", raw)
    return [p.strip() for p in parts if p.strip()]


def _parse_list(raw: str) -> list[str]:
    """Parse a newline-separated list of exact values (preserves commas in values)."""
    if not raw:
        return []
    return [p.strip() for p in raw.split("\n") if p.strip()]
