"""
Writes security scan events to the SECURITY_SCAN_EVENT table.
One row per scan event (all findings stored as a JSON array).
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.logging import get_logger
from src.security.detectors.base import ScanResult

logger = get_logger(__name__)


def _source_dicts(sources) -> list[dict]:
    return [source.to_dict() for source in (sources or ())]


def write_finding(
    session_id: str | None,
    scan_target: str,    # 'prompt' | 'response'
    result: ScanResult,
    blocked: bool,
    masked_text: str,
    *,
    platform: str | None = None,
    prompt_id: str | None = None,
    config=None,                     # the SecurityConfig the scan ran with
    session_root: str | None = None, # the folder the session ran in
) -> str | None:
    """
    Insert one SECURITY_SCAN_EVENT row for a scan event.
    Returns the event_id on success, None on failure.
    Never raises - logging errors must not break the hook flow.

    With `config`, the row records which profile decided (the global one when
    it applied, else the nearest workspace profile), every profile that
    applied, and for each finding the profiles that asked for it. Folder paths
    are recorded as normalised paths in this local database only.
    """
    try:
        from src.common.workspace_profiles import normalise_root
        from src.db.manager import get_db_connection
        from src.db.schema import add_security_scan_event_columns

        conn = get_db_connection()
        cursor = conn.cursor()
        add_security_scan_event_columns(cursor)

        event_id = str(uuid.uuid4())
        findings_list = [
            {
                "category":    f.category,
                "type":        f.type,
                "label":       f.label,
                "severity":    f.severity,
                "masked_value": f.masked_value,
                "line_number": f.line_number,
                **({"custom": True} if getattr(f, "custom", False) else {}),
                **({"sources": _source_dicts(f.sources)} if getattr(f, "sources", ()) else {}),
            }
            for f in result.findings
        ]
        sources = tuple(getattr(config, "sources", ()) or ())
        primary = sources[0] if sources else None

        cursor.execute(
            """
            INSERT INTO SECURITY_SCAN_EVENT (
                event_id, session_id, scan_target,
                prompt_hash, masked_text, findings_json,
                finding_count, blocked, scan_ms, scan_strategy, timestamp,
                platform, prompt_id, profile_hash, policy_scope,
                workspace_root, session_root, policy_sources_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                scan_target,
                result.prompt_hash,
                masked_text,
                json.dumps(findings_list),
                len(result.findings),
                1 if blocked else 0,
                result.scan_ms,
                result.scan_strategy,
                datetime.now(timezone.utc).isoformat(),
                platform,
                prompt_id,
                primary.profile_hash if primary else None,
                primary.scope if primary else None,
                primary.root if primary else None,
                normalise_root(session_root),
                json.dumps(_source_dicts(sources)) if sources else None,
            ),
        )
        conn.commit()
        logger.info(
            f"Security event logged: id={event_id} target={scan_target} "
            f"findings={len(result.findings)} blocked={blocked}"
        )
        return event_id

    except Exception as e:
        logger.warning(f"write_finding failed (non-fatal): {e}")
        return None
