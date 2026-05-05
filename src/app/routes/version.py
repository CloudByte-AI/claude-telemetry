"""
Version check and update route for CloudByte.

Checks local plugin cache for newer versions and kills
old processes so Claude Code picks up the new version.
"""

import json
import os
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.common.logging import get_logger
from src.workers.kill_worker import (
    kill_worker_by_pid,
    kill_worker_by_port,
    kill_all_claude_telemetry_processes,
    kill_uv_process,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/version", tags=["version"])


def _get_cache_dir() -> Path:
    return Path.home() / ".claude" / "plugins" / "cache" / "claude-telemetry" / "claude-telemetry"


def _get_version_folders(cache_dir: Path) -> list:
    """Get version folders sorted newest first."""
    versions = []
    for folder in cache_dir.iterdir():
        if folder.is_dir():
            parts = folder.name.split(".")
            if len(parts) >= 2 and all(p.isdigit() for p in parts):
                versions.append(folder.name)
    versions.sort(key=lambda v: tuple(int(x) for x in v.split(".")), reverse=True)
    return versions


def _get_current_version() -> str:
    """Get currently running version from plugin.json."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT")

    if not plugin_root:
        cache_dir = _get_cache_dir()
        if cache_dir.exists():
            versions = _get_version_folders(cache_dir)
            if versions:
                plugin_root = str(cache_dir / versions[0])

    if plugin_root:
        plugin_json = Path(plugin_root) / ".claude-plugin" / "plugin.json"
        if plugin_json.exists():
            try:
                data = json.loads(plugin_json.read_text())
                return data.get("version", "unknown")
            except Exception:
                pass

    return "unknown"


@router.get("/status")
def version_status():
    """Check if a newer version exists in local cache."""
    try:
        cache_dir = _get_cache_dir()

        if not cache_dir.exists():
            return JSONResponse({
                "current": "unknown",
                "latest_cached": "unknown",
                "update_available": False,
            })

        versions = _get_version_folders(cache_dir)

        if not versions:
            return JSONResponse({
                "current": "unknown",
                "latest_cached": "unknown",
                "update_available": False,
            })

        latest_cached = versions[0]
        current = _get_current_version()

        def parse_ver(v):
            try:
                return tuple(int(x) for x in v.split("."))
            except Exception:
                return (0,)

        update_available = (
            current != "unknown" and
            parse_ver(latest_cached) > parse_ver(current)
        )

        logger.info(f"Version status: current={current}, latest_cached={latest_cached}, update_available={update_available}")

        return JSONResponse({
            "current": current,
            "latest_cached": latest_cached,
            "update_available": update_available,
        })

    except Exception as e:
        logger.error(f"Version status check failed: {e}", exc_info=True)
        return JSONResponse({
            "current": "unknown",
            "latest_cached": "unknown",
            "update_available": False,
            "error": str(e)
        }, status_code=500)


@router.post("/apply")
def apply_update():
    """Kill all plugin processes so Claude Code uses new cached version."""
    try:
        results = {
            "kill_pid":       kill_worker_by_pid(),
            "kill_port":      kill_worker_by_port(),
            "kill_processes": kill_all_claude_telemetry_processes(),
            "kill_uv":        kill_uv_process(),
        }

        logger.info(f"Apply update completed: {results}")

        return JSONResponse({
            "success": True,
            "message": "All processes killed. Open a new Claude Code session to use the new version.",
            "results": results,
        })

    except Exception as e:
        logger.error(f"Apply update failed: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)