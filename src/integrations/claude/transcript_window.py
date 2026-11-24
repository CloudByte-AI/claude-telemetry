"""
Incremental reading of a Claude Code transcript for the Stop hook and the
UserPromptSubmit recovery pass.

Both used to read the whole transcript on every run, so each reply cost more as
the session grew (11 s of reading per 342 MB, twice per turn). Each now reads from
the first line of the last prompt it had not seen before. Positions are kept in
TRANSCRIPT_READ_STATE (src/db/schema.py), one row per transcript and reader. Each
reader keeps its own position: whichever runs first, each one still sees every
line, so it does exactly what its full read did. (A shared position let Stop move
past history the recovery pass had never read: interrupted turns, and replies Stop
does not store because a subagent's text won the pairing.)

Why a prompt line and not the end of the file:
  - a prompt's reply, tools, interrupt markers and title all lie at or after its
    own prompt line;
  - a turn still running at the last read must be read again whole.

Subagent files (the Stop reader). The pairing in prompt_response.py lets a
subagent's text compete for its parent prompt's reply (the latest text wins,
across all subagent files). So the reader reads:
  - every subagent file whose size changed since its last read;
  - every other subagent file holding records of a prompt being read.
It then moves the start back to the line of every prompt those files refer to,
and repeats until nothing changes.

When it reads from the start (today's behaviour):
  - the file shrank, or its first bytes or the bytes before the saved position
    changed;
  - the state cannot be read.
Position rules:
  - it never moves past the prompt of a write the caller reports as failed
    (hold());
  - nothing is saved unless the caller commits.

SessionEnd keeps the full read as a once-per-session safety net.
"""

import glob as _glob
import hashlib
import io
import json
import os
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from src.common.logging import get_logger
from src.integrations.claude.prompt_response import get_text_content

logger = get_logger(__name__)

READERS = ("stop", "recovery")   # "stop" also reads the subagent files
_FP_BYTES = 4096          # bytes hashed at the start of the file and before the saved position
_STATE_KEEP_DAYS = 35     # rows not updated for this long are deleted


def prompt_key(record: Dict[str, Any]) -> Optional[str]:
    """The id extract_prompt_response_pairs() (step 1) gives a real user prompt, else None."""
    if record.get("type") != "user" or record.get("isMeta"):
        return None
    pid = record.get("promptId") or record.get("uuid")
    if not pid:
        return None
    message = record.get("message")
    text = get_text_content(message.get("content") if isinstance(message, dict) else None)
    if not text or text.lstrip().startswith("<task-notification>"):
        return None
    return pid


def _parse(raw_lines, base: int) -> Tuple[List[Tuple[int, Dict[str, Any]]], int]:
    """(file offset, record) for each JSON object line, and the offset after the last line.

    raw_lines yields byte lines with their newline (a binary file object), the first starting
    at offset base. Lines that are not JSON objects (a line still being written, a damaged
    line) are skipped, as read_jsonl_file() does.
    """
    out = []
    pos = base
    for raw in raw_lines:
        line = raw.strip()
        if line:
            try:
                rec = json.loads(line.decode("utf-8", "ignore"))
            except ValueError:
                rec = None
            if isinstance(rec, dict):
                out.append((pos, rec))
        pos += len(raw)
    return out, pos


def _hash(f, start: int, length: int) -> str:
    f.seek(start)
    return hashlib.blake2b(f.read(length), digest_size=16).hexdigest()


def _key(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _connection():
    from src.db.manager import get_db_connection
    return get_db_connection()


def _load_state(conn, key: str, reader: str) -> Optional[Dict[str, Any]]:
    try:
        row = conn.execute(
            "SELECT read_offset, head_len, head_hash, before_hash, prompt_offsets, subagents "
            "FROM TRANSCRIPT_READ_STATE WHERE transcript_path = ? AND reader = ?", (key, reader)).fetchone()
    except Exception as e:  # table missing (migration not run yet) or locked: read it all
        logger.debug(f"transcript state unavailable: {e}")
        return None
    if not row:
        return None
    try:
        return {"read_offset": int(row[0]), "head_len": int(row[1]), "head_hash": row[2],
                "before_hash": row[3], "prompts": json.loads(row[4] or "{}"),
                "subagents": json.loads(row[5]) if row[5] else {}}
    except Exception:
        return None


class TranscriptWindow:
    """What one run read, and how to save the position once the caller has stored it all."""

    def __init__(self, path, key: str, reader: str, events: List[Dict[str, Any]], *, start: int,
                 end: int, old_offset: int, new_offset: int, prompts: Dict[str, int],
                 subagents: Optional[Dict[str, Any]], had_state: bool, reset: Optional[str],
                 sub_read: int, sub_total: int, moved_back: bool):
        self.path = path
        self.key = key
        self.reader = reader
        self.events = events
        self.start = start
        self.end = end
        self.old_offset = old_offset
        self.new_offset = new_offset
        self.prompts = prompts
        self.subagents = subagents
        self.had_state = had_state
        self.reset = reset
        self.sub_read = sub_read
        self.sub_total = sub_total
        self.moved_back = moved_back
        self._held: Set[str] = set()

    def hold(self, prompt_id: Optional[str]) -> None:
        """The caller could not store something of this prompt: read it again next time."""
        self._held.add(prompt_id or "")

    def describe(self) -> str:
        sub = f", subagent files {self.sub_read}/{self.sub_total}" if self.sub_total else ""
        why = f", full read: {self.reset}" if self.reset else ""
        back = ", moved back for a subagent" if self.moved_back else ""
        return (f"read {self.end - self.start} of {self.end} bytes from {self.start}"
                f"{sub}{back}{why}, {len(self.events)} records")

    def commit(self, conn=None) -> bool:
        """Save the position. Never raises; False when it could not be saved (next run reads more)."""
        try:
            offset = self.new_offset
            if self._held:
                spots = [self.prompts.get(p) for p in self._held]
                offset = min([offset] + [s if s is not None else self.old_offset for s in spots])
            conn = conn or _connection()
            with open(self.path, "rb") as f:
                size = os.fstat(f.fileno()).st_size
                if offset > size:   # rewritten since the read: start over next time
                    offset = 0
                head_len = min(size, _FP_BYTES)
                head_hash = _hash(f, 0, head_len)
                before = min(offset, _FP_BYTES)
                before_hash = _hash(f, offset - before, before)
            # Prompts at or after a held position are read again: forget them, so the
            # position moves on once they are stored.
            prompts = {p: o for p, o in self.prompts.items() if o < offset} if offset < self.new_offset \
                else self.prompts
            now = time.time()
            values = (self.key, self.reader, offset, head_len, head_hash, before_hash,
                      json.dumps(prompts, separators=(",", ":")),
                      None if self.subagents is None else json.dumps(self.subagents, separators=(",", ":")),
                      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
            sql = ("INSERT INTO TRANSCRIPT_READ_STATE (transcript_path, reader, read_offset, head_len, "
                   "head_hash, before_hash, prompt_offsets, subagents, updated_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT (transcript_path, reader) DO UPDATE SET read_offset = excluded.read_offset, "
                   "head_len = excluded.head_len, head_hash = excluded.head_hash, "
                   "before_hash = excluded.before_hash, prompt_offsets = excluded.prompt_offsets, "
                   "subagents = excluded.subagents, updated_at = excluded.updated_at")
            try:
                conn.execute(sql, values)
            except Exception as e:
                if "no such table" not in str(e):
                    raise
                from src.db.schema import TRANSCRIPT_READ_STATE_DDL
                conn.execute(TRANSCRIPT_READ_STATE_DDL)
                conn.execute(sql, values)
            if not self.had_state:   # a new session: drop rows of transcripts Claude has deleted by now
                cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - _STATE_KEEP_DAYS * 86400))
                conn.execute("DELETE FROM TRANSCRIPT_READ_STATE WHERE updated_at < ?", (cutoff,))
            conn.commit()
            return True
        except Exception as e:
            logger.warning(f"Could not save the transcript read position (the next run reads more): {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            return False


def read_window(path, reader: str, conn=None) -> TranscriptWindow:
    """Read what this reader's earlier runs have not fully processed (all of it on its first run).

    reader is "stop" (the Stop hook; also reads the subagent files the pairing needs, see the
    module docstring) or "recovery" (the UserPromptSubmit recovery pass).
    Raises only what reading the main transcript raises (it was checked to exist).
    """
    if reader not in READERS:
        raise ValueError(f"unknown transcript reader {reader!r}")
    key = _key(path)
    try:
        conn = conn or _connection()
        state = _load_state(conn, key, reader)
    except Exception as e:
        logger.debug(f"transcript state unavailable: {e}")
        state = None
    had_state = state is not None
    reset = None if had_state else "no saved position"

    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        if state is not None:
            off = state["read_offset"]
            if off > size or state["head_len"] > size:
                reset = "the file is shorter than before"
            elif _hash(f, 0, state["head_len"]) != state["head_hash"]:
                reset = "the start of the file changed"
            elif _hash(f, off - min(off, _FP_BYTES), min(off, _FP_BYTES)) != state["before_hash"]:
                reset = "the file changed before the saved position"
            if reset:
                state = None
        old_offset = state["read_offset"] if state else 0
        prompts: Dict[str, int] = dict(state["prompts"]) if state else {}
        sub_state: Dict[str, Any] = dict(state["subagents"]) if state else {}
        start = old_offset
        f.seek(start)
        lines, end = _parse(f, start)
    keys = [prompt_key(r) for _, r in lines]

    sub_events: List[Dict[str, Any]] = []
    new_sub_state: Optional[Dict[str, Any]] = None
    sub_read = sub_total = 0
    moved_back = False
    if reader == "stop":
        sub_dir = str(path).replace(".jsonl", "") + os.sep + "subagents"
        files = _glob.glob(os.path.join(sub_dir, "*.jsonl")) if os.path.isdir(sub_dir) else []
        loaded: Dict[str, Tuple[int, List[Dict[str, Any]], Set[str]]] = {}

        def load(fp: str) -> None:
            try:
                with open(fp, "rb") as sf:
                    parsed, n_bytes = _parse(sf, 0)
            except OSError as e:
                logger.warning(f"Failed reading subagent file {fp}: {e}")
                parsed, n_bytes = [], 0
            recs = [r for _, r in parsed]
            pids = {r["promptId"] for r in recs if r.get("type") == "user" and r.get("promptId")}
            loaded[fp] = (n_bytes, recs, pids)

        for fp in files:
            prev = sub_state.get(os.path.basename(fp))
            try:
                fsize = os.path.getsize(fp)
            except OSError:
                fsize = -1
            if not isinstance(prev, dict) or prev.get("size") != fsize:
                load(fp)
        window_pids = set(keys) - {None}
        while True:
            grew = False
            for fp in files:
                if fp in loaded:
                    continue
                prev = sub_state.get(os.path.basename(fp))
                if isinstance(prev, dict) and set(prev.get("pids") or ()) & window_pids:
                    load(fp)
                    grew = True
            need: Set[str] = set()
            for _, _, pids in loaded.values():
                need |= pids
            back = [prompts[p] for p in need if p in prompts and prompts[p] < start]
            if back:
                new_start = min(back)
                with open(path, "rb") as f:
                    f.seek(new_start)
                    extra, _ = _parse(io.BytesIO(f.read(start - new_start)), new_start)
                extra_keys = [prompt_key(r) for _, r in extra]
                lines, keys = extra + lines, extra_keys + keys
                window_pids |= set(extra_keys) - {None}
                start = new_start
                moved_back = grew = True
            if not grew:
                break
        sub_events = [r for fp in files if fp in loaded for r in loaded[fp][1]]
        sub_read, sub_total = len(loaded), len(files)
        names = {os.path.basename(fp) for fp in files}
        new_sub_state = {n: v for n, v in sub_state.items() if n in names}
        for fp, (n_bytes, _, pids) in loaded.items():
            new_sub_state[os.path.basename(fp)] = {"size": n_bytes, "pids": sorted(pids)}

    # The next read starts at the last prompt this read saw for the first time.
    new_offset = old_offset
    for (off, _), pid in zip(lines, keys):
        if pid and pid not in prompts:
            prompts[pid] = off
            new_offset = off

    return TranscriptWindow(
        path, key, reader, [r for _, r in lines] + sub_events, start=start, end=end,
        old_offset=old_offset, new_offset=new_offset, prompts=prompts, subagents=new_sub_state,
        had_state=had_state, reset=reset, sub_read=sub_read, sub_total=sub_total, moved_back=moved_back)


def apply_titles(session_id: str, events: List[Dict[str, Any]], conn=None) -> None:
    """Store the latest ai-title / custom-title found in events (COALESCE keeps earlier ones)."""
    ai_title = custom_title = None
    for event in events:
        if event.get("type") == "ai-title":
            ai_title = event.get("aiTitle")
        elif event.get("type") == "custom-title":
            custom_title = event.get("customTitle")
    if not (ai_title or custom_title):
        return
    try:
        conn = conn or _connection()
        conn.execute(
            """UPDATE SESSION
               SET ai_title     = COALESCE(?, ai_title),
                   custom_title = COALESCE(?, custom_title)
               WHERE session_id = ?""",
            (ai_title, custom_title, session_id),
        )
        conn.commit()
        logger.debug(f"Session titles updated - ai: {ai_title!r}, custom: {custom_title!r}")
    except Exception as e:
        logger.warning(f"Could not update session titles: {e}")
