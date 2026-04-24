"""
CloudByte Dashboard — FastAPI + Jinja2
Run: uvicorn src.app.app:app --reload --port 8765
"""

from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import dashboard, sessions, conversations, tokens, tools, observations, projects, config, sse, worker

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifecycle.
    Starts worker processing on startup and stops it on shutdown.
    """
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    logger.info("=== FastAPI Lifespan Startup ===")

    # Startup
    try:
        await worker.start_worker_processing()
        logger.info("Worker processing started successfully")
    except Exception as e:
        logger.error(f"Failed to start worker processing: {e}", exc_info=True)
        raise

    yield

    # Shutdown
    try:
        await worker.stop_worker_processing()
        logger.info("Worker processing stopped successfully")
    except Exception as e:
        logger.error(f"Failed to stop worker processing: {e}", exc_info=True)
        raise


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
app.include_router(worker.router)