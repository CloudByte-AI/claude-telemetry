"""
Shell snapshots: the exact lines a shell command (Bash, PowerShell) changed in a git repo.

The client reports almost nothing a shell command writes, so the hooks look for themselves:

    before  PreToolUse          one `git status --porcelain=v2 -z -uall --branch` in each repo
                                the command works in (shell_targets.py: the folders it runs in
                                after any `cd`, `git -C` folders, path arguments; at most 3),
                                plus a copy of every file that already differs from HEAD (git
                                cannot give their "before" later). Saved per tool call:
                                shell/pending/<tool_use_id>.json
    after   PostToolUse, or     the same status calls. For each file that changed, the "before"
            PostToolUseFailure  is the copy (dirty or untracked before) or the HEAD blob (clean
            for a failed call   before, one `git cat-file --batch`); the lines are a difflib
                                diff in the structuredPatch hunk shape. One record per file.

Copies live in a content-addressed store (shell/blobs/<sha1>), so a file that did not change
between commands is not written again. Limits: MAX_COPY_FILE per file and MAX_COPY_TOTAL per
call; a changed file beyond them is recorded with no lines (no_diff). Excluded paths are never
copied.

Rules:
  - moved code keeps its author: a rename (staged, or a plain mv of identical content, also
    between two snapshotted repos) is recorded with no lines
  - HEAD moved, or a git working-tree command (checkout, stash, reset, ...): the record is
    marked git_operation; those lines are not the assistant's writing
  - binary files (a NUL in the first 8000 bytes, git's own test) get no lines; a file with a
    byte-order mark (UTF-8, UTF-16, UTF-32: Windows PowerShell 5.1 writes UTF-16) is text
  - notes (kind "note") say where changes may be unseen: a background command (its changes
    land after the hook), a command that ends in a repo that was not snapshotted, a repo
    skipped for the rest of the session because its status was slow (over SLOW_SECONDS,
    SLOW_STRIKES times) or too big

Git runs read-only (--no-optional-locks: never takes the user's index lock), with no window and
no stdin. Never raises, never prints. Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time

SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
SHELL_DIRNAME = "shell"
STATUS_TIMEOUT = 3.0
SLOW_SECONDS = 0.5
SLOW_STRIKES = 2
MAX_COPY_FILE = 1_000_000
MAX_COPY_TOTAL = 20_000_000
MAX_ENTRIES = 5_000
MAX_DIFF_CELLS = 40_000_000          # difflib work bound: changed-region lines before x after
BINARY_PROBE = 8000
STATE_TTL_SECONDS = 12 * 3600
SESSION_TTL_SECONDS = 2 * 86400
_NO_WINDOW = 0x08000000

# Commands whose file changes come from git, not from the assistant writing code.
GIT_WORKTREE_COMMAND = re.compile(
    r"\bgit\s+(?:-C\s+\S+\s+)?(?:checkout|switch|stash|pull|merge|rebase|reset|restore|"
    r"cherry-pick|revert|am|apply|clean)\b")

# Entry tuple positions (lists after a JSON round trip).
KIND, XY, HEAD_BLOB, LINE, ORIG = range(5)


# ── Small helpers ─────────────────────────────────────────────────────────────

def _shell_dir() -> str:
    from src.capture.config import capture_dir
    return os.path.join(capture_dir(), SHELL_DIRNAME)


def _safe_name(value) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "none"))[:128]


def pending_path(tool_use_id) -> str:
    return os.path.join(_shell_dir(), "pending", _safe_name(tool_use_id) + ".json")


def _blob_path(sha: str) -> str:
    return os.path.join(_shell_dir(), "blobs", sha[:2], sha)


def _session_path(session_id) -> str:
    return os.path.join(_shell_dir(), "sessions", _safe_name(session_id) + ".json")


def _write_atomic(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.{os.getpid()}.{os.urandom(3).hex()}.tmp"
    try:
        with open(temp, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError:
                pass


def _load_json(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def find_repo(start) -> str | None:
    """The working-tree root above `start` (a folder holding .git, a folder or a file)."""
    if not start:
        return None
    folder = os.path.abspath(str(start))
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return folder
        parent = os.path.dirname(folder)
        if parent == folder:
            return None
        folder = parent


def _same_path(a, b) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _full(root: str, path: str) -> str:
    return os.path.join(root, *path.split("/"))


def _stat(path: str):
    """[size, mtime_ns] of a regular file, else None."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    import stat as stat_mod
    return [st.st_size, st.st_mtime_ns] if stat_mod.S_ISREG(st.st_mode) else None


def _read(path: str, limit: int = MAX_COPY_FILE):
    """The file's bytes, or None when it is missing, not a regular file or over `limit`."""
    st = _stat(path)
    if st is None or st[0] > limit:
        return None
    try:
        with open(path, "rb") as handle:
            return handle.read(limit + 1)
    except OSError:
        return None


def _git(root: str, *args: str, data: bytes | None = None, timeout: float | None = None):
    import subprocess
    options = {"capture_output": True, "timeout": STATUS_TIMEOUT if timeout is None else timeout,
               "env": dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")}
    if os.name == "nt":
        options["creationflags"] = _NO_WINDOW
    if data is None:
        options["stdin"] = subprocess.DEVNULL
    else:
        options["input"] = data
    return subprocess.run(["git", "--no-optional-locks", "-C", root, *args], **options)


# ── git status ────────────────────────────────────────────────────────────────

def parse_status(output: bytes):
    """`git status --porcelain=v2 -z --branch` -> ({path: [kind, xy, head blob, line, orig]}, head).
    kind: '1' changed, '2' renamed/copied, 'u' unmerged, '?' untracked, 'R-' a rename's old path.
    Submodules are left out: their changes are not lines."""
    items = output.split(b"\0")
    entries, head, i = {}, None, 0
    while i < len(items):
        entry = items[i].decode("utf-8", "replace")
        i += 1
        if entry.startswith("# branch.oid "):
            head = entry.split(" ", 2)[2]
        elif entry.startswith("? "):
            entries[entry[2:]] = ["?", "??", None, entry, None]
        elif entry.startswith("1 "):
            parts = entry.split(" ", 8)          # 1 XY sub mH mI mW hH hI path
            if len(parts) == 9 and parts[2].startswith("N"):
                entries[parts[8]] = ["1", parts[1], parts[6], entry, None]
        elif entry.startswith("2 "):
            parts = entry.split(" ", 9)          # 2 XY sub mH mI mW hH hI Xscore path, then origPath
            orig = items[i].decode("utf-8", "replace") if i < len(items) else ""
            i += 1
            if len(parts) == 10 and parts[2].startswith("N"):
                entries[parts[9]] = ["2", parts[1], parts[6], entry, orig]
                entries.setdefault(orig, ["R-", parts[1], None, entry, None])
        elif entry.startswith("u "):
            parts = entry.split(" ", 10)
            if len(parts) == 11:
                entries[parts[10]] = ["u", parts[1], None, entry, None]
    return entries, head


def _status(root: str):
    """(entries, head, seconds) or None when git cannot answer."""
    import subprocess
    started = time.perf_counter()
    try:
        result = _git(root, "status", "--porcelain=v2", "-z", "-uall", "--branch")
    except subprocess.TimeoutExpired:
        return "timeout"
    except OSError:
        return None
    if result.returncode != 0:
        return None
    entries, head = parse_status(result.stdout)
    return entries, head, time.perf_counter() - started


def _head_blobs(root: str, shas) -> dict:
    """{sha: bytes} for blobs, in one `git cat-file --batch`."""
    shas = sorted({s for s in shas if s and set(s) != {"0"}})
    if not shas:
        return {}
    import subprocess
    try:
        out = _git(root, "cat-file", "--batch", data=("\n".join(shas) + "\n").encode()).stdout
    except (subprocess.TimeoutExpired, OSError):
        return {}
    found, pos = {}, 0
    for sha in shas:
        newline = out.find(b"\n", pos)
        if newline < 0:
            break
        header = out[pos:newline].split()
        if len(header) == 3 and header[1] == b"blob":
            size = int(header[2])
            found[sha] = out[newline + 1:newline + 1 + size]
            pos = newline + 1 + size + 1
        else:
            pos = newline + 1
    return found


# ── Session state: slow repos ─────────────────────────────────────────────────

def _skipped(session_id, root: str) -> bool:
    state = _load_json(_session_path(session_id)) or {}
    return any(_same_path(root, r) for r in state.get("skipped", []))


def _strike(session_id, root: str, force: bool = False) -> bool:
    """Count a slow status for this repo; True when the repo is now skipped for the session."""
    path = _session_path(session_id)
    state = _load_json(path) or {}
    strikes = state.setdefault("strikes", {})
    key = os.path.normcase(os.path.abspath(root))
    strikes[key] = strikes.get(key, 0) + 1
    skip = force or strikes[key] >= SLOW_STRIKES
    if skip and key not in state.setdefault("skipped", []):
        state["skipped"].append(key)
    try:
        _write_atomic(path, json.dumps(state).encode("utf-8"))
    except OSError:
        pass
    return skip


def _note(payload: dict, client: str, now: float, reason: str, repo) -> dict:
    from src.capture.edits import FORMAT_VERSION, _iso
    return {
        "v": FORMAT_VERSION, "kind": "note", "time": _iso(now), "client": client,
        "session_id": payload.get("session_id"), "prompt_id": payload.get("prompt_id"),
        "tool_use_id": payload.get("tool_use_id"), "tool_name": payload.get("tool_name"),
        "cwd": payload.get("cwd"), "repo": repo, "reason": reason,
    }


# ── Before ────────────────────────────────────────────────────────────────────

def _store_blob(content: bytes):
    sha = hashlib.sha1(content).hexdigest()
    path = _blob_path(sha)
    try:
        if os.path.exists(path):
            os.utime(path, None)                          # still in use: keep past the TTL
        else:
            _write_atomic(path, content)
        return sha
    except OSError:
        return None


def command_roots(payload: dict) -> list:
    """The repos a shell call works in (shell_targets.py): the folders its commands run in after
    any `cd`, `git -C` folders and path arguments. Falls back to the start folder's repo."""
    cwd = payload.get("cwd")
    start = find_repo(cwd)
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = tool_input.get("command")
    if not cwd or not isinstance(command, str) or not command.strip():
        return [start] if start else []
    try:
        from src.capture.shell_targets import command_repos, is_powershell
        return command_repos(command, os.path.abspath(str(cwd)), is_powershell(payload.get("tool_name")), find_repo)
    except Exception:
        return [start] if start else []


def take_before(payload: dict, client: str, profile, now: float):
    """Snapshot every repo the command works in (command_roots). Returns (outcome, note records):
    'taken' when at least one repo was snapshotted, else the first repo's outcome."""
    try:
        roots = command_roots(payload)
        if not roots:
            return "no_repo", []
        session_id = payload.get("session_id")
        notes, repos, outcomes, budget = [], [], [], [0]
        for root in roots:
            outcome, repo_state = _before_repo(payload, client, profile, now, root, notes, budget)
            outcomes.append(outcome)
            if repo_state is not None:
                repos.append(repo_state)
        if not repos:
            return outcomes[0], notes
        state = {"v": 2, "repos": repos, "time": now, "session_id": session_id,
                 "tool_use_id": payload.get("tool_use_id")}
        _write_atomic(pending_path(payload.get("tool_use_id")), json.dumps(state).encode("utf-8"))
        return "taken", notes
    except Exception:
        return "error", []


def _before_repo(payload: dict, client: str, profile, now: float, root: str, notes: list, budget: list):
    """One repo's status and copies: (outcome, {root, head, entries, copies} or None). Notes are
    appended to `notes`; budget[0] is the bytes copied so far in this call (MAX_COPY_TOTAL)."""
    session_id = payload.get("session_id")
    if _skipped(session_id, root):
        return "skipped", None
    status = _status(root)
    if status == "timeout":
        _strike(session_id, root, force=True)
        notes.append(_note(payload, client, now, "slow_repo", root))
        return "slow", None
    if status is None:
        return "git_failed", None
    entries, head, seconds = status
    if seconds > SLOW_SECONDS and _strike(session_id, root):
        notes.append(_note(payload, client, now, "slow_repo", root))
    if len(entries) > MAX_ENTRIES:
        _strike(session_id, root, force=True)
        notes.append(_note(payload, client, now, "too_many_changes", root))
        return "too_many", None

    from src.capture.edits import excluded
    copies = {}
    for path, entry in entries.items():
        if entry[KIND] == "R-":
            continue                                      # a rename's old path: gone from the tree
        full = _full(root, path)
        st = _stat(full)
        info = {"stat": st}
        if excluded(full, profile.exclude):
            info["excluded"] = True
        elif st is not None and st[0] <= MAX_COPY_FILE and budget[0] + st[0] <= MAX_COPY_TOTAL:
            content = _read(full)
            sha = _store_blob(content) if content is not None else None
            if sha:
                info["copy"] = sha
                budget[0] += len(content)
        copies[path] = info
    return "taken", {"root": root, "head": head, "entries": entries, "copies": copies}


# ── After ─────────────────────────────────────────────────────────────────────

# Byte-order marks, longest first (a UTF-32 LE mark starts like a UTF-16 LE one). Windows
# PowerShell 5.1 writes `>` and Out-File as UTF-16 LE with a mark, so such a file is text.
_BOMS = ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
         (b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16"))


def decode_text(content: bytes | None) -> str | None:
    """The file's text, or None when it is binary (git's test: a NUL in the first 8000
    bytes, checked after a byte-order mark has chosen the encoding). Missing is ""."""
    if not content:
        return ""
    for bom, encoding in _BOMS:
        if content.startswith(bom):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                return None
    if b"\0" in content[:BINARY_PROBE]:
        return None
    return content.decode("utf-8", "replace")


def _is_binary(*contents) -> bool:
    return any(c and decode_text(c) is None for c in contents)


def _lines(content: bytes | None) -> list[str]:
    return (decode_text(content) or "").splitlines()


def diff_hunks(before: list[str], after: list[str]):
    """Hunks in the structuredPatch shape (3 context lines), or None when the changed region
    is too big to diff within the budget."""
    import difflib
    lead = 0
    while lead < len(before) and lead < len(after) and before[lead] == after[lead]:
        lead += 1
    tail = 0
    while (tail < len(before) - lead and tail < len(after) - lead
           and before[-1 - tail] == after[-1 - tail]):
        tail += 1
    if (len(before) - lead - tail) * (len(after) - lead - tail) > MAX_DIFF_CELLS:
        return None
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    hunks = []
    for group in matcher.get_grouped_opcodes(3):
        i1, i2, j1, j2 = group[0][1], group[-1][2], group[0][3], group[-1][4]
        lines = []
        for tag, a1, a2, b1, b2 in group:
            if tag == "equal":
                lines += [" " + x for x in before[a1:a2]]
            if tag in ("replace", "delete"):
                lines += ["-" + x for x in before[a1:a2]]
            if tag in ("replace", "insert"):
                lines += ["+" + x for x in after[b1:b2]]
        hunks.append({"old_start": i1 + 1 if i2 > i1 else i1, "old_lines": i2 - i1,
                      "new_start": j1 + 1 if j2 > j1 else j1, "new_lines": j2 - j1, "lines": lines})
    return hunks


def _blob(sha):
    if not sha:
        return None
    try:
        with open(_blob_path(sha), "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _snapshot_repos(state) -> list:
    """The per-repo snapshots of a before-state: v2 lists them, v1 (one repo) is the state itself."""
    if not isinstance(state, dict):
        return []
    if state.get("v") == 2 and isinstance(state.get("repos"), list):
        return [r for r in state["repos"] if isinstance(r, dict) and r.get("root")]
    return [state] if state.get("root") else []


def take_after(payload: dict, client: str, profile, now: float, failed: bool = False):
    """The records for one shell call (changed files of every snapshotted repo, then notes), or
    None when no before-snapshot exists for it (no repo, skipped, or the before step failed)."""
    try:
        pending = pending_path(payload.get("tool_use_id"))
        state = _load_json(pending)
        repos = _snapshot_repos(state)
        if not repos:
            return None
        try:
            os.remove(pending)
        except OSError:
            pass
        notes = []
        result = payload.get("tool_response")
        if isinstance(result, dict) and result.get("backgroundTaskId"):
            notes.append(_note(payload, client, now, "background", repos[0]["root"]))
        # The after-hook's cwd is where the command ended (a kept `cd` moves it: checked live
        # 24 Nov 2026). A repo there that was not snapshotted may hold unseen changes.
        end_root = find_repo(payload.get("cwd"))
        if end_root and not any(_same_path(end_root, r["root"]) for r in repos):
            notes.append(_note(payload, client, now, "other_repo", end_root))

        records = []
        for repo in repos:
            root = repo["root"]
            status = _status(root)
            if status == "timeout":
                _strike(state.get("session_id"), root, force=True)
                notes.append(_note(payload, client, now, "slow_repo", root))
                continue
            if status is None:
                continue
            entries_now, head_now, seconds = status
            if seconds > SLOW_SECONDS and _strike(state.get("session_id"), root):
                notes.append(_note(payload, client, now, "slow_repo", root))
            records += _changed_records(payload, client, profile, now, failed, repo, entries_now, head_now)
        return _pair_moves(records) + notes
    except Exception:
        return None


def _changed_records(payload, client, profile, now, failed, state, entries_now, head_now):
    from src.capture.edits import _record, _with_lines, excluded

    root, before_entries, copies = state["root"], state.get("entries", {}), state.get("copies", {})
    command = (payload.get("tool_input") or {}).get("command") if isinstance(payload.get("tool_input"), dict) else None
    head_moved = head_now != state.get("head")
    git_operation = head_moved or bool(GIT_WORKTREE_COMMAND.search(str(command or "")))
    renamed_old = {e[ORIG] for p, e in entries_now.items() if e[KIND] == "2" and p not in before_entries}

    changed = []
    for path in sorted(set(before_entries) | set(entries_now)):
        was, now_entry = before_entries.get(path), entries_now.get(path)
        if (now_entry and now_entry[KIND] == "R-") or path in renamed_old:
            continue                                      # shown as the new path's rename
        full = _full(root, path)
        st_now = _stat(full)
        if was and now_entry and was[LINE] == now_entry[LINE] and copies.get(path, {}).get("stat") == st_now:
            continue                                      # same status line, same size and time
        changed.append((path, was, now_entry, full))

    blobs = _head_blobs(root, [e[HEAD_BLOB] for _, was, e, _ in changed if not was and e])
    records = []
    for path, was, now_entry, full in changed:
        content_now = _read(full)
        exists_now = os.path.lexists(full)
        renamed_from = now_entry[ORIG] if now_entry and now_entry[KIND] == "2" and not was else None
        info = copies.get(path, {})
        if was:
            before = _blob(info.get("copy"))
        else:
            sha = now_entry[HEAD_BLOB] if now_entry else None
            before = blobs.get(sha) if sha and set(sha) != {"0"} else b""

        op = ("delete" if not exists_now else "rename" if renamed_from
              else "create" if not was and before == b"" else "edit")
        record = _record(payload, client, now, full, op, None, None, None)
        record.update(source="snapshot", repo=root, head_moved=head_moved, git_operation=git_operation)
        if failed:
            record["failed"] = True
        if renamed_from:
            record["renamed_from"] = _full(root, renamed_from)

        no_diff = None
        if was and before is None:
            no_diff = "excluded" if info.get("excluded") else "no_before_copy"
        elif exists_now and content_now is None:
            no_diff = "too_large"
        elif _is_binary(before, content_now):
            no_diff = "binary"
        if no_diff:
            if before == content_now:
                continue
            record.update(no_diff=True, reason=no_diff)
            if no_diff == "excluded":
                record["excluded"] = True
            records.append(record)
            continue

        before_lines, after_lines = _lines(before), _lines(content_now)
        if before_lines == after_lines:
            # No line changed. A pure rename, or a file that appeared or went away empty
            # (`touch x`), is still a change to the tree: recorded with no lines.
            if renamed_from or op in ("create", "delete"):
                record.update(added=0, removed=0)
                records.append(_with_lines(record, profile, hunks=[]))
            continue
        hunks = diff_hunks(before_lines, after_lines)
        if hunks is None:
            record.update(no_diff=True, reason="diff_too_large")
            records.append(record)
            continue
        record["added"] = sum(1 for h in hunks for x in h["lines"] if x.startswith("+"))
        record["removed"] = sum(1 for h in hunks for x in h["lines"] if x.startswith("-"))
        record = _with_lines(record, profile, hunks=hunks)
        if op == "delete" and before:
            record["_before"] = before                    # for _pair_moves, never written
        records.append(record)
    return records


def _pair_moves(records: list) -> list:
    """A plain `mv`, within one repo or between two snapshotted repos: a deleted file and a new
    file with the same content are one rename, and moved code keeps its author, so no lines are
    credited."""
    deleted = {}
    for record in records:
        before = record.pop("_before", None)
        if before is not None:
            deleted[before] = record
    for record in list(records):
        if record["op"] == "create" and record.get("removed") == 0 and deleted:
            content = _read(record["file"])
            old = deleted.pop(content, None) if content is not None else None
            if old is not None:
                records.remove(old)
                record.update(op="rename", renamed_from=old["file"], added=0, removed=0, hunks=[])
    return records


# ── Housekeeping ──────────────────────────────────────────────────────────────

def cleanup(now: float | None = None) -> dict:
    """Remove before-snapshots never finished (a denied call has no after hook), copies no
    call has used for STATE_TTL_SECONDS, and old session files. Never raises."""
    now = time.time() if now is None else now
    removed = {"pending": 0, "blobs": 0, "sessions": 0}
    for sub, ttl, key in (("pending", STATE_TTL_SECONDS, "pending"), ("blobs", STATE_TTL_SECONDS, "blobs"),
                          ("sessions", SESSION_TTL_SECONDS, "sessions")):
        top = os.path.join(_shell_dir(), sub)
        for folder, _dirs, files in os.walk(top) if os.path.isdir(top) else []:
            for name in files:
                path = os.path.join(folder, name)
                try:
                    if now - os.stat(path).st_mtime > ttl:
                        os.remove(path)
                        removed[key] += 1
                except OSError:
                    pass
    return removed
