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

DB_PATH  = Path.home() / ".cloudbyte" / "data" / "cloudbyte.db"
WAL_PATH = Path(str(DB_PATH) + "-wal")
SHM_PATH = Path(str(DB_PATH) + "-shm")


def _get_mtime() -> float:
    """
    Return the most recent mtime across the DB, WAL, and SHM files.
    SQLite WAL mode writes to -wal first; main .db only updates on checkpoint.
    """
    mtimes = []
    for p in (DB_PATH, WAL_PATH, SHM_PATH):
        try:
            mtimes.append(os.path.getmtime(p))
        except OSError:
            pass
    return max(mtimes) if mtimes else 0.0


@router.get("/events")
async def sse_stream(request: Request):
    async def generator():
        yield ": ok\n\n"

        last_mtime  = _get_mtime()
        heartbeat_i = 0

        while True:
            await asyncio.sleep(2)

            if await request.is_disconnected():
                break

            current_mtime = _get_mtime()
            if current_mtime > last_mtime:
                last_mtime = current_mtime
                yield "data: db_updated\n\n"

            heartbeat_i += 1
            if heartbeat_i >= 8:   # 8 × 2s = 16s
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