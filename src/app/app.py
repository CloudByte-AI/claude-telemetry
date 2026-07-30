"""
CloudByte Dashboard - FastAPI + Jinja2
Run: uvicorn src.app.app:app --reload --port 4723
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import dashboard, sessions, conversations, tokens, tools, observations, projects, config, sse, version, security

BASE_DIR = Path(__file__).parent

DASHBOARD_PORT = 4723


def _pid_file() -> Path:
    from src.common.paths import get_cloudbyte_dir
    return get_cloudbyte_dir() / "worker.pid"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifecycle.

    Writes ~/.cloudbyte/worker.pid on startup and removes it on shutdown.

    This is NOT bookkeeping - it is the teardown contract. Both plugins' session
    end handlers call kill_worker.shutdown_worker_if_no_active_sessions(), whose
    first strategy is kill_worker_by_pid(), which reads this exact file. Without
    it that call returns False immediately and shutdown silently degrades to the
    slower kill_worker_by_port() scan. See
    context/claude-plugin-enhancement/04-risks-and-keep-list.md §1.1.

    Failures here are logged and swallowed on purpose: a missing PID file
    degrades cleanup, it must never stop the dashboard from serving. The previous
    version re-raised, so a background-task error took the whole app down.
    """
    import json
    import logging
    import os
    import time

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    logger.info("=== FastAPI Lifespan Startup ===")

    pid_file = _pid_file()
    try:
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text(json.dumps({
            "pid": os.getpid(),
            "port": DASHBOARD_PORT,
            "start_time": time.time(),
            "type": "fastapi-integrated",
        }, indent=2))
        logger.info(f"Wrote PID file: {pid_file}")
    except Exception as e:
        logger.warning(f"Failed to write PID file (shutdown will fall back to port scan): {e}")

    yield

    try:
        pid_file.unlink(missing_ok=True)
        logger.info(f"Removed PID file: {pid_file}")
    except Exception as e:
        logger.warning(f"Failed to remove PID file: {e}")


app = FastAPI(
    title="CloudByte Dashboard",
    lifespan=lifespan
)

# ── Static files ───────────────────────────────────────────────────────────────
if (BASE_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# ── Routes ─────────────────────────────────────────────────────────────────────
app.include_router(dashboard.router)
app.include_router(sessions.router)
app.include_router(conversations.router)
app.include_router(tokens.router)
app.include_router(tools.router)
app.include_router(observations.router)
app.include_router(projects.router)
app.include_router(config.router)
app.include_router(sse.router)
app.include_router(version.router)
app.include_router(security.router)
