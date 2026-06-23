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


def write_finding(
    session_id: str,
    scan_target: str,    # 'prompt' | 'response'
    result: ScanResult,
    blocked: bool,
    masked_text: str,
) -> str | None:
    """
    Insert one SECURITY_SCAN_EVENT row for a scan event.
    Returns the event_id on success, None on failure.
    Never raises - logging errors must not break the hook flow.
    """
    try:
        from src.db.manager import get_db_connection

        conn = get_db_connection()
        cursor = conn.cursor()

        event_id = str(uuid.uuid4())
        findings_list = [
            {
                "category":    f.category,
                "type":        f.type,
                "label":       f.label,
                "severity":    f.severity,
                "masked_value": f.masked_value,
                "line_number": f.line_number,
            }
            for f in result.findings
        ]

        cursor.execute(
            """
            INSERT INTO SECURITY_SCAN_EVENT (
                event_id, session_id, scan_target,
                prompt_hash, masked_text, findings_json,
                finding_count, blocked, scan_ms, scan_strategy, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
