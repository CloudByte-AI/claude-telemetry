"""
Migration History Log

Append-only audit trail of schema migrations, written alongside the DB file
so it's easy to find during debugging without querying the DB itself.

One JSON line per migration run (not per column) - safe to append from
multiple concurrent processes, since a single line write never requires
reading or rewriting the rest of the file.
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from src.common.logging import get_logger
from src.common.time_utils import get_now_ist_iso


logger = get_logger(__name__)

MIGRATION_LOG_FILENAME = "migration_history.jsonl"


def append_migration_log(
    db_path: Path,
    from_version: int,
    to_version: int,
    changes: List[Dict[str, Any]],
    duration_ms: float,
) -> None:
    """
    Append one migration-run record to <db_path's folder>/migration_history.jsonl.

    Never raises - a logging failure (disk full, permissions, bad path) must
    never be able to break the migration it's describing. Worst case, one
    history line is lost; the migration itself already committed before this
    is called.
    """
    if not changes:
        return

    try:
        import os

        entry = {
            "timestamp": get_now_ist_iso(),
            "from_version": from_version,
            "to_version": to_version,
            "db_file": Path(db_path).name,
            "pid": os.getpid(),
            "duration_ms": round(duration_ms, 2),
            "changes": changes,
        }

        log_path = Path(db_path).parent / MIGRATION_LOG_FILENAME
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        logger.info(f"Migration history appended to {log_path} ({len(changes)} change(s))")

    except Exception as e:
        logger.warning(f"Could not write migration history log (non-fatal): {e}")
