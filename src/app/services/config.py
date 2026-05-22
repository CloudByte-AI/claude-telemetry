"""Business logic for the configuration page."""

from ..queries.config import load_config, save_config, config_exists

PLACEHOLDER_KEYS = {"enter_your_api_key_here", "YOUR_GEMINI_API_KEY", ""}


def _api_key_is_real(key: str) -> bool:
    return (key or "").strip() not in PLACEHOLDER_KEYS


def get_config_context() -> dict:
    """
    Always reads directly from the config file.
    No defaults injected — what's in the file is what the user sees.
    """
    cfg      = load_config()
    endpoint = cfg.get("llm", {}).get("endpoints", {}).get("default", {})
    settings = cfg.get("settings", {})
    worker   = cfg.get("worker", {})
    key      = endpoint.get("api_key", "")
    key_set  = _api_key_is_real(key)

    # Warn if features are enabled but key is not real
    feature_warning = (
        settings.get("enable_observations")
        and not key_set
    )

    return {
        "active":          "config",
        "config":          cfg,
        "endpoint":        endpoint,
        "config_exists":   config_exists(),
        "settings":        settings,
        "worker":          worker,
        "api_key_set":     key_set,
        "feature_warning": feature_warning,
    }


def update_config(form: dict) -> tuple[bool, str]:
    """
    Read the file, apply only the fields from the form, write back.
    Features can be set to any value — if key is missing we just warn, not block.
    """
    try:
        cfg = load_config()

        # Ensure nested structure exists
        cfg.setdefault("settings", {})
        cfg.setdefault("llm", {}).setdefault("endpoints", {}).setdefault("default", {})
        cfg.setdefault("worker", {})

        ep = cfg["llm"]["endpoints"]["default"]

        # ── LLM Provider ──────────────────────────────────────────────────────
        ep["provider"]    = form.get("provider", ep.get("provider", "")).strip()
        ep["model"]       = form.get("model",    ep.get("model",    "")).strip()
        ep["temperature"] = _float(form.get("temperature"), ep.get("temperature", 0.7))
        ep["max_tokens"]  = _int(form.get("max_tokens"),    ep.get("max_tokens",  4000))

        # Only update api_key if user typed something new
        new_key = form.get("api_key", "").strip()
        if new_key and new_key not in PLACEHOLDER_KEYS:
            ep["api_key"] = new_key

        # ── Features — save whatever user set, just warn if key missing ───────
        cfg["settings"]["enable_observations"] = form.get("enable_observations") == "1"

        # ── Worker port ───────────────────────────────────────────────────────
        new_port = form.get("worker_port", "").strip()
        if new_port:
            cfg["worker"]["port"] = _int(new_port, cfg["worker"].get("port", 8765))

        save_config(cfg)

        # Check if we should add a warning about features + missing key
        key_is_real = _api_key_is_real(ep.get("api_key", ""))
        features_on = cfg["settings"]["enable_observations"]
        if features_on and not key_is_real:
            return True, (
                "Configuration saved — but features are enabled without a valid API key. "
                "Observations will not work until you provide a real API key."
            )

        return True, "Configuration saved successfully."

    except Exception as e:
        return False, f"Failed to save configuration: {e}"


def _float(val, default: float) -> float:
    try:    return float(val)
    except: return default


def _int(val, default: int) -> int:
    try:    return int(val)
    except: return default


# ---------------------------------------------------------------------------
# Log Cleanup helpers
# ---------------------------------------------------------------------------

def count_old_log_files() -> int:
    """Return the number of log files older than 3 days."""
    import time
    from common.paths import get_logs_dir

    logs_dir = get_logs_dir()
    if not logs_dir.exists():
        return 0

    cutoff = time.time() - (3 * 24 * 60 * 60)
    return sum(
        1 for f in logs_dir.iterdir()
        if f.is_file() and f.stat().st_mtime < cutoff
    )


def run_log_cleanup() -> int:
    """Delete log files older than 3 days and return the number deleted."""
    import time
    from common.paths import get_logs_dir

    logs_dir = get_logs_dir()
    if not logs_dir.exists():
        return 0

    cutoff = time.time() - (3 * 24 * 60 * 60)
    deleted = 0
    for f in list(logs_dir.iterdir()):
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                deleted += 1
            except Exception:
                pass
    return deleted


def preview_database_cleanup() -> dict:
    """
    Preview how many entries would be affected by database cleanup.
    Returns counts and details without deleting anything.
    """
    from ..routers.db import q
    from common.logging import get_cloudbyte_logger

    logger = get_cloudbyte_logger(__name__)
    logger.info("Previewing database cleanup")

    sessions = q("""
        SELECT session_id, cwd FROM SESSION
        WHERE session_id NOT IN (SELECT DISTINCT session_id FROM USER_PROMPT)
    """)

    projects = q("""
        SELECT project_id, name, path FROM PROJECT
        WHERE project_id NOT IN (SELECT DISTINCT project_id FROM SESSION)
    """)

    return {
        "session_count": len(sessions),
        "project_count": len(projects),
    }


def run_database_cleanup() -> dict:
    """
    Remove sessions with 0 prompts and projects with 0 sessions.
    Returns a summary of deleted items.
    """
    from ..routers.db import cmd, q
    from common.logging import get_cloudbyte_logger
    
    logger = get_cloudbyte_logger(__name__)
    logger.info("Starting database cleanup process")
    
    # 1. Identify sessions with 0 prompts
    sessions_to_delete = q("""
        SELECT session_id, cwd FROM SESSION 
        WHERE session_id NOT IN (SELECT DISTINCT session_id FROM USER_PROMPT)
    """)
    
    session_ids = [s["session_id"] for s in sessions_to_delete]
    sessions_deleted = 0
    
    if session_ids:
        logger.info(f"Found {len(session_ids)} empty sessions to delete")
        for s in sessions_to_delete:
            logger.info(f"Deleting empty session: {s['session_id']} (CWD: {s['cwd']})")
            
        sessions_deleted = cmd("""
            DELETE FROM SESSION 
            WHERE session_id NOT IN (SELECT DISTINCT session_id FROM USER_PROMPT)
        """)
        logger.info(f"Successfully deleted {sessions_deleted} sessions")
    else:
        logger.info("No empty sessions found")
    
    # 2. Identify projects with 0 sessions
    projects_to_delete = q("""
        SELECT project_id, name, path FROM PROJECT 
        WHERE project_id NOT IN (SELECT DISTINCT project_id FROM SESSION)
    """)
    
    project_ids = [p["project_id"] for p in projects_to_delete]
    projects_deleted = 0
    
    if project_ids:
        logger.info(f"Found {len(project_ids)} empty projects to delete")
        for p in projects_to_delete:
            logger.info(f"Deleting empty project: {p['name']} (ID: {p['project_id']}, Path: {p['path']})")
            
        projects_deleted = cmd("""
            DELETE FROM PROJECT 
            WHERE project_id NOT IN (SELECT DISTINCT project_id FROM SESSION)
        """)
        logger.info(f"Successfully deleted {projects_deleted} projects")
    else:
        logger.info("No empty projects found")
    
    logger.info("Database cleanup process completed", 
                sessions_removed=sessions_deleted, 
                projects_removed=projects_deleted)
    
    return {
        "sessions_deleted": sessions_deleted,
        "projects_deleted": projects_deleted
    }