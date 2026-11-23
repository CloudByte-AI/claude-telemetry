"""
Evidence files - one file per record.

    ~/.cloudbyte/capture/evidence/
        2026-11-23/                         one folder per UTC day
            1795431234567890123-00000004817263549182-3f9a1c2b.json

One file per record, not a shared append-only file, for the same reason as the guardrails
audit spool: many hook processes write at once and appends are not atomic on Windows. Each
record is written to a temporary name and renamed into place (atomic within one directory),
so a reader sees the whole record or no file. Names start with the write time in
nanoseconds and then the machine's monotonic counter, so a plain name sort is the write
order, even for writes within one tick of a coarse wall clock.

Housekeeping (retention, abandoned temp files, a size cap) runs at most once every few hours,
from a write, and costs one stat otherwise. Capture is on even where nothing ever reads the
evidence, so this folder must look after itself.

Hot path: `os`, `json` and `time` only (json is already loaded by the hook); shutil loads
only for housekeeping.
"""

from __future__ import annotations

import json
import os
import time

EVIDENCE_DIRNAME = "evidence"
RECORD_SUFFIX = ".json"
TEMP_SUFFIX = ".tmp"
HOUSEKEEPING_MARKER = ".housekeeping"
HOUSEKEEPING_EVERY_SECONDS = 6 * 3600
STALE_TEMP_SECONDS = 300
# Bounds disk use whatever happens: when exceeded, the OLDEST days are dropped first.
MAX_RECORDS = 50_000


def evidence_dir() -> str:
    from src.capture.config import capture_dir
    return os.path.join(capture_dir(), EVIDENCE_DIRNAME)


def _day(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _is_day(name: str) -> bool:
    return (len(name) == 10 and name[4] == "-" and name[7] == "-"
            and (name[:4] + name[5:7] + name[8:]).isdigit())


def write_record(record: dict, retention_days: int, now: float | None = None) -> str | None:
    """Write one evidence record. Returns its path, or None when it could not be written."""
    now = time.time() if now is None else now
    try:
        directory = os.path.join(evidence_dir(), _day(now))
        os.makedirs(directory, exist_ok=True)
        # Wall time first (orders across reboots), then the monotonic high-resolution counter
        # (shared by every process on the machine), because the wall clock can be coarse:
        # Python 3.12 on Windows reads GetSystemTimeAsFileTime, up to 15.6 ms per step.
        name = f"{int(now * 1_000_000_000)}-{time.perf_counter_ns():020d}-{os.urandom(4).hex()}"
        temp = os.path.join(directory, name + TEMP_SUFFIX)
        final = os.path.join(directory, name + RECORD_SUFFIX)
        with open(temp, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        os.replace(temp, final)
    except Exception:
        return None
    maybe_housekeep(retention_days, now)
    return final


def maybe_housekeep(retention_days: int, now: float | None = None) -> bool:
    """Run housekeeping when it has not run for HOUSEKEEPING_EVERY_SECONDS. Never raises."""
    now = time.time() if now is None else now
    try:
        marker = os.path.join(evidence_dir(), HOUSEKEEPING_MARKER)
        if os.path.exists(marker) and now - os.stat(marker).st_mtime < HOUSEKEEPING_EVERY_SECONDS:
            return False
        housekeep(retention_days, now)
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(str(int(now)))
        os.utime(marker, (now, now))
        return True
    except Exception:
        return False


def _day_dirs(root: str) -> list[tuple[str, str]]:
    """(name, path) of each day folder, oldest first. Anything else is not ours."""
    try:
        with os.scandir(root) as entries:
            days = [(e.name, e.path) for e in entries if e.is_dir() and _is_day(e.name)]
    except OSError:
        return []
    return sorted(days)


def housekeep(retention_days: int, now: float | None = None) -> dict:
    """Delete days older than the retention, abandoned temp files, and the oldest days over
    the cap. Returns what it removed. Never raises."""
    now = time.time() if now is None else now
    removed = {"days": 0, "temp": 0, "over_cap_days": 0}
    try:
        import shutil

        cutoff = _day(now - max(1, int(retention_days)) * 86400)
        days = _day_dirs(evidence_dir())
        for name, path in [d for d in days if d[0] < cutoff]:
            shutil.rmtree(path, ignore_errors=True)
            removed["days"] += 1
        days = [d for d in days if os.path.isdir(d[1])]
        counts = []
        for _name, path in days:
            n = 0
            with os.scandir(path) as entries:
                for entry in entries:
                    if entry.name.endswith(TEMP_SUFFIX):
                        try:
                            if now - entry.stat().st_mtime > STALE_TEMP_SECONDS:
                                os.unlink(entry.path)
                                removed["temp"] += 1
                        except OSError:
                            pass
                    elif entry.name.endswith(RECORD_SUFFIX):
                        n += 1
            counts.append(n)
        total = sum(counts)
        today = _day(now)
        for (name, path), n in zip(days, counts):
            if total <= MAX_RECORDS or name == today:
                break
            shutil.rmtree(path, ignore_errors=True)
            removed["over_cap_days"] += 1
            total -= n
    except Exception:
        pass
    try:
        from src.capture.snapshot import cleanup
        removed["shell"] = cleanup(now)                   # unfinished snapshots, old copies
    except Exception:
        pass
    return removed


def iter_records(since_day: str | None = None):
    """Every readable record, oldest first, as (path, record). Unreadable files are skipped."""
    for name, directory in _day_dirs(evidence_dir()):
        if since_day and name < since_day:
            continue
        try:
            files = sorted(f for f in os.listdir(directory) if f.endswith(RECORD_SUFFIX))
        except OSError:
            continue
        for file_name in files:
            path = os.path.join(directory, file_name)
            try:
                with open(path, encoding="utf-8") as handle:
                    yield path, json.load(handle)
            except Exception:
                continue
