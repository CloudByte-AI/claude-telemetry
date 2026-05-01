"""
Sync runner — orchestrates collect → POST → advance checkpoint.

Call run_sync() from Stop hook (per-prompt, fast) and
from session_end handler (full session flush).
"""

from src.common.logging import get_logger
from src.common.file_io import read_json
from src.common.paths import get_config_file
from src.sync.collector import collect, has_data, advance_checkpoint
from src.sync.client import build_payload, post_telemetry

logger = get_logger(__name__)


def _load_central_config() -> dict | None:
    """
    Read central config from ~/.cloudbyte/config.json.
    Returns None if sync is disabled or not configured.
    """
    cfg_file = get_config_file()
    if not cfg_file.exists():
        return None

    try:
        cfg = read_json(cfg_file)
    except Exception as e:
        logger.warning(f"Could not read config: {e}")
        return None

    central = cfg.get("central", {})
    if not central.get("enabled"):
        return None

    url = (central.get("url") or "").strip()
    key = (central.get("api_key") or "").strip()
    if not url or not key:
        logger.warning("Central sync enabled but url/api_key not set — skipping")
        return None

    return central


def run_sync(session_id: str | None = None, mode: str = "stop") -> bool:
    """
    Run one sync cycle.

    Args:
        session_id: Restrict collection to this session (Stop hook).
                    None = collect across all sessions (SessionEnd flush).
        mode:       "stop" | "session_end" — checked against config flags.

    Returns:
        True if sync succeeded or was skipped cleanly, False on error.
    """
    central = _load_central_config()
    if central is None:
        return True  # disabled — not an error

    # Check per-mode flag
    flag_key = "sync_on_stop" if mode == "stop" else "sync_on_session_end"
    if not central.get(flag_key, True):
        logger.debug(f"Central sync skipped: {flag_key}=false")
        return True

    try:
        data = collect(session_id=session_id)

        if not has_data(data):
            logger.debug("Central sync: nothing new to send")
            return True

        counts = {k: len(data[k]) for k in
                  ("sessions", "prompts", "responses", "io_tokens",
                   "tool_calls", "tool_tokens", "thinking", "observations", "summaries")}
        logger.info(f"Central sync [{mode}]: {counts}")

        payload = build_payload(data)
        result  = post_telemetry(central["url"], central["api_key"], payload)

        if result["ok"]:
            advance_checkpoint(data)
            logger.info("Central sync: checkpoint advanced")
            return True
        else:
            # Leave checkpoint unchanged — will retry next cycle
            logger.warning(f"Central sync failed (will retry): {result['status']}")
            return False

    except Exception as e:
        logger.error(f"Central sync error: {e}", exc_info=True)
        return False
