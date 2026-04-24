"""
Server-Sent Events endpoint.
Watches the CloudByte SQLite DB file for changes and sends
'db_updated' to all connected browser tabs when new data arrives.
"""

import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

DB_PATH = Path.home() / ".cloudbyte" / "data" / "cloudbyte.db"


def _get_mtime() -> float | None:
    try:
        return os.path.getmtime(DB_PATH)
    except OSError:
        return None


@router.get("/events")
async def sse_stream(request: Request):
    async def generator():
        # Send a comment immediately so the browser marks the connection open
        # and does not hold up the page load waiting for first bytes
        yield ": ok\n\n"

        last_mtime  = _get_mtime()
        heartbeat_i = 0

        while True:
            await asyncio.sleep(3)

            if await request.is_disconnected():
                break

            current_mtime = _get_mtime()
            if current_mtime is not None and current_mtime != last_mtime:
                last_mtime = current_mtime
                yield "data: db_updated\n\n"

            heartbeat_i += 1
            if heartbeat_i >= 5:   # 5 × 3s = 15s
                heartbeat_i = 0
                yield "data: heartbeat\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":        "keep-alive",
        },
    )