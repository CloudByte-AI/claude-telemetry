"""DB queries for the security scanning feature."""

import json
from ..routers.db import q, client_where


def get_scan_events(
    limit: int = 50,
    offset: int = 0,
    scan_target: str | None = None,
    blocked_only: bool = False,
    client: str | None = None,
) -> list[dict]:
    try:
        conditions = []
        params: list = []

        if scan_target:
            conditions.append("scan_target = ?")
            params.append(scan_target)
        if blocked_only:
            conditions.append("blocked = 1")

        extra = (" AND " + " AND ".join(conditions)) if conditions else ""
        client_extra, client_params = client_where(client, "s")
        rows = q(
            f"""
            SELECT e.event_id, e.session_id, e.scan_target, e.prompt_hash,
                   e.masked_text, e.findings_json, e.finding_count,
                   e.blocked, e.scan_ms, e.scan_strategy, e.timestamp,
                   s.client
            FROM SECURITY_SCAN_EVENT e
            LEFT JOIN SESSION s ON s.session_id = e.session_id
            WHERE 1=1 {extra} {client_extra}
            ORDER BY e.timestamp DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params) + client_params + (limit, offset),
        )
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_scan_event(event_id: str) -> dict | None:
    try:
        row = q(
            """
            SELECT e.*, s.client
            FROM SECURITY_SCAN_EVENT e
            LEFT JOIN SESSION s ON s.session_id = e.session_id
            WHERE e.event_id = ? LIMIT 1
            """,
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
                    # New format stores 'label'; old events may have 'detector'
                    det = f.get("label") or f.get("detector") or f.get("category") or ""
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
            SELECT e.event_id, e.scan_target, e.finding_count, e.blocked, e.timestamp,
                   e.findings_json, s.client
            FROM SECURITY_SCAN_EVENT e
            LEFT JOIN SESSION s ON s.session_id = e.session_id
            ORDER BY e.timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        result = []
        for row in rows:
            d = dict(row)
            try:
                findings = json.loads(d.get("findings_json") or "[]")
                first = findings[0] if findings else {}
                d["top_detector"] = (
                    first.get("label") or first.get("detector") or first.get("category") or ""
                )
            except Exception:
                d["top_detector"] = ""
            result.append(d)
        return result
    except Exception:
        return []
