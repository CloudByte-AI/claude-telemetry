"""Business logic for the configuration page."""

from ..queries.config import load_config, save_config, config_exists


def get_config_context() -> dict:
    """
    Always reads directly from the config file.
    No defaults injected — what's in the file is what the user sees.

    The `llm` and `worker` blocks are no longer read: the LLM generation layer and
    the task-queue worker were removed, and `worker.port` was never actually wired
    to the dashboard port (4723 is hardcoded). Existing config.json files keep those
    keys — they are simply inert now.
    """
    cfg = load_config()

    return {
        "active":        "config",
        "config":        cfg,
        "config_exists": config_exists(),
        "settings":      cfg.get("settings", {}),
    }


def update_config(form: dict) -> tuple[bool, str]:
    """
    Read the file, apply only the fields from the form, write back.

    Nothing on the config page currently posts editable settings — the LLM
    provider, feature-toggle and worker-port fields were all removed. Kept as the
    POST target so the form still round-trips, and so future settings have a home.
    """
    try:
        cfg = load_config()
        cfg.setdefault("settings", {})
        save_config(cfg)
        return True, "Configuration saved successfully."

    except Exception as e:
        return False, f"Failed to save configuration: {e}"


# ---------------------------------------------------------------------------
# Log Cleanup helpers
# ---------------------------------------------------------------------------

def count_old_log_files() -> int:
    """Return the number of log files older than 3 days."""
    import time
    from src.common.paths import get_logs_dir

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
    from src.common.paths import get_logs_dir

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
    from src.common.logging import get_cloudbyte_logger

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
    from src.common.logging import get_cloudbyte_logger
    
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