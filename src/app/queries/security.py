"""DB queries for the security scanning feature."""

import json
from ..routers.db import q


def get_scan_events(
    limit: int = 50,
    offset: int = 0,
    scan_target: str | None = None,
    blocked_only: bool = False,
) -> list[dict]:
    try:
        conditions = []
        params: list = []

        if scan_target:
            conditions.append("scan_target = ?")
            params.append(scan_target)
        if blocked_only:
            conditions.append("blocked = 1")

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = q(
            f"""
            SELECT event_id, session_id, scan_target, prompt_hash,
                   masked_text, findings_json, finding_count,
                   blocked, scan_ms, scan_strategy, timestamp
            FROM SECURITY_SCAN_EVENT
            {where}
            ORDER BY timestamp DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + (limit, offset),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_scan_event(event_id: str) -> dict | None:
    try:
        row = q(
            "SELECT * FROM SECURITY_SCAN_EVENT WHERE event_id = ? LIMIT 1",
            (event_id,),
            one=True,
        )
        return dict(row) if row else None
    except Exception:
        return None


def get_scan_stats() -> dict:
    """Summary counts for the dashboard and events page header."""
    _empty = {"total": 0, "blocked": 0, "response_hits": 0, "top_detector": None}
    try:
        total = q("SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT", one=True)
        blocked = q("SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT WHERE blocked = 1", one=True)
        response_hits = q(
            "SELECT COUNT(*) as n FROM SECURITY_SCAN_EVENT WHERE scan_target = 'response'",
            one=True,
        )

        # Most common detector across all events
        all_findings = q(
            "SELECT findings_json FROM SECURITY_SCAN_EVENT WHERE findings_json IS NOT NULL LIMIT 500"
        )
        detector_counts: dict[str, int] = {}
        for row in all_findings:
            try:
                findings = json.loads(row["findings_json"] or "[]")
                for f in findings:
                    det = f.get("detector", "")
                    if det:
                        detector_counts[det] = detector_counts.get(det, 0) + 1
            except Exception:
                pass

        top_detector = max(detector_counts, key=detector_counts.get) if detector_counts else None

        return {
            "total":         total["n"] if total else 0,
            "blocked":       blocked["n"] if blocked else 0,
            "response_hits": response_hits["n"] if response_hits else 0,
            "top_detector":  top_detector,
        }
    except Exception:
        return _empty


def get_recent_events(limit: int = 5) -> list[dict]:
    """Used for the dashboard security card."""
    try:
        rows = q(
            """
            SELECT event_id, scan_target, finding_count, blocked, timestamp,
                   findings_json
            FROM SECURITY_SCAN_EVENT
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        result = []
        for row in rows:
            d = dict(row)
            try:
                findings = json.loads(d.get("findings_json") or "[]")
                d["top_detector"] = findings[0].get("detector", "") if findings else ""
            except Exception:
                d["top_detector"] = ""
            result.append(d)
        return result
    except Exception:
        return []
