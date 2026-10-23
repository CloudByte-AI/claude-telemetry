"""
Workspace profiles - per-folder policy files, shared by guardrails and
security scanning.

    ~/.cloudbyte/<feature>/
        <global profile>                       every session on this machine
        workspaces/
            web-app-3f9a1c2b7d4e.yaml          sessions opened in that folder

A workspace is the folder a session was opened in (Claude Code's project
directory; the Cursor workspace root that holds the call's cwd), not the folder
a tool call happens to touch. A workspace file has its feature's profile format
and can only ADD restrictions to the global profile: each feature combines them
strictest-wins.

Which workspace files apply to a session opened in folder S:

    1. the nearest file: S's own, or else the closest parent folder's
       (opening `repo/src` must not escape `repo`'s policy)
    2. plus every managed file further up (a local file in a subfolder
       must not escape a policy the organisation manages)

The file NAME is the binding: `<folder>-<first 12 hex of sha256(root)>.yaml`,
where root is the folder's normalised path (normalise_root). Finding the files
for a session is a handful of existence checks up the folder chain - no index
to keep in step, no directory listing on the hot path. A file may declare its
root under `ui_workspace.root`; when it does and the root does not match its
name (a copied or renamed file), it is ignored rather than applied to the
wrong folder.

A file that carries a `ui_managed: {by: ...}` block is centrally managed: the
plugin applies it more widely (see find_workspace_files) and does not edit it.

Whatever writes these files computes the names with this exact algorithm;
tests/guardrails/test_workspaces.py pins the vectors it must reproduce.

This module knows nothing about either feature's profile format: callers pass
the directory and how to read a file, and parse what comes back themselves.
Stdlib only at import time.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
from pathlib import Path
from typing import Callable

from src.common.lexical_paths import resolve_path
from src.common.yaml_cache import EDITOR_KEY_PREFIX

WORKSPACES_DIRNAME = "workspaces"
PROFILE_SUFFIX = ".yaml"

# Editor data read here (and only here): which folder a file is for, and
# whether it is centrally managed. Both are `ui_` keys, so policy parsers and
# fingerprints ignore them.
WORKSPACE_KEY = f"{EDITOR_KEY_PREFIX}workspace"
MANAGED_KEY = f"{EDITOR_KEY_PREFIX}managed"

_HASH_LENGTH = 12
_SLUG_LENGTH = 40
_SLUG_INVALID = re.compile(r"[^a-z0-9._-]+")
_DRIVE_ROOT = re.compile(r"^[a-z]:/$", re.IGNORECASE)

# A found workspace file: (normalised root, file path, raw mapping or None).
Found = tuple[str, Path, "dict | None"]
Reader = Callable[[Path, str], "dict | None"]


def _log():
    """The logger, imported on first use (hot path)."""
    from src.common.logging import get_logger
    return get_logger(__name__)


def workspaces_dir(base: Path) -> Path:
    """The workspaces folder under a feature's directory."""
    return base / WORKSPACES_DIRNAME


def normalise_root(path: str | None) -> str | None:
    """
    A folder path in the one form used for matching and hashing, or None when
    it is empty or not absolute. Purely lexical (no filesystem access):

        C:\\Users\\Raj\\repo\\   ->  c:/users/raj/repo
        /home/Raj/repo/        ->  /home/raj/repo
        ~/repo                 ->  the home folder's path + /repo

        /c:/Users/Raj/repo     ->  c:/users/raj/repo   (Cursor's form of a Windows path)

    A drive or filesystem root keeps its slash (`c:/`, `/`).

    Always lowercased, on every OS: Windows and macOS file systems ignore case
    (`C:/Repo` and `c:/repo` must be one workspace), and one rule everywhere
    keeps the file name identical for whatever writes the files and keeps the
    engines free of host-platform checks. On a case-sensitive Linux disk, two
    sibling folders differing only in case share a policy - the strictest-wins
    rule makes that err on the safe side.
    """
    text = str(path).strip() if path else ""
    if len(text) > 2 and text[0] == "/" and text[2] == ":" and text[1].isalpha():
        text = text[1:]
    resolved = resolve_path(text, None)
    if not resolved:
        return None
    if not (resolved.startswith("/") or (len(resolved) > 2 and resolved[1] == ":" and resolved[2] == "/")):
        return None
    if len(resolved) > 1 and not _DRIVE_ROOT.match(resolved):
        resolved = resolved.rstrip("/") or "/"
    return resolved.lower()


def file_name_for(root: str) -> str:
    """
    The workspace file name for a normalised root:
    `<folder name, slugged>-<first 12 hex of sha256(root)>.yaml`.

    The folder name keeps the file recognisable; the hash keeps two folders
    with the same name apart. Changing this breaks every existing file and
    anything else that writes them - see the pinned vectors in
    tests/guardrails/test_workspaces.py.
    """
    base = posixpath.basename(root.rstrip("/")) if not _DRIVE_ROOT.match(root) else ""
    slug = _SLUG_INVALID.sub("-", base.lower()).strip("-.")[:_SLUG_LENGTH].strip("-.")
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:_HASH_LENGTH]
    return f"{slug or 'workspace'}-{digest}{PROFILE_SUFFIX}"


def path_for(root: str, base: Path) -> Path:
    """Where the workspace file for this normalised root lives under a feature's directory."""
    return workspaces_dir(base) / file_name_for(root)


def _is_top(path: str) -> bool:
    return path == "/" or bool(_DRIVE_ROOT.match(path))


def ancestors(root: str) -> list[str]:
    """
    A normalised root and every parent folder up to the drive / filesystem
    root, nearest first: `c:/a/b` -> [`c:/a/b`, `c:/a`, `c:/`].
    """
    chain = [root]
    current = root
    while not _is_top(current):
        parent = posixpath.dirname(current)
        if len(parent) == 2 and parent[1] == ":":
            parent += "/"                   # posixpath leaves `c:` for `c:/a`
        if not parent or parent == current:
            break
        chain.append(parent)
        current = parent
    return chain


def session_root(workspace_root: str | None, cwd: str | None) -> str | None:
    """The normalised folder a session's workspace lookup starts from."""
    return normalise_root(workspace_root) or normalise_root(cwd)


def workspace_root_for(cwd: str | None, roots: list) -> str | None:
    """
    Which of Cursor's workspace roots a call belongs to: the deepest root that
    holds `cwd` (a multi-root workspace has several), else the first root. The
    root, not the cwd, is the workspace, as Claude Code's project directory is.
    Returns the root as given. Never raises.
    """
    try:
        here = normalise_root(cwd)
        best, best_length = None, -1
        for root in roots:
            candidate = normalise_root(root)
            if not candidate or not here:
                continue
            inside = here == candidate or here.startswith(candidate.rstrip("/") + "/")
            if inside and len(candidate) > best_length:
                best, best_length = root, len(candidate)
        return best or (roots[0] if roots else None)
    except Exception:
        return roots[0] if roots else None


def is_managed(raw: dict | None) -> bool:
    """
    Whether a file is centrally managed: it carries a `ui_managed` block that
    names who manages it (`by`). The value itself is not checked - marking
    your own file as managed only makes it apply more widely, which can only
    make things stricter.
    """
    if not isinstance(raw, dict):
        return False
    marker = raw.get(MANAGED_KEY)
    return isinstance(marker, dict) and bool(str(marker.get("by") or "").strip())


def declared_root(raw: dict | None) -> str | None:
    """The folder a file says it is for (`ui_workspace.root`), normalised; None if it does not say."""
    if not isinstance(raw, dict):
        return None
    block = raw.get(WORKSPACE_KEY)
    if not isinstance(block, dict):
        return None
    return normalise_root(block.get("root")) if block.get("root") else None


def any_workspace_files(directory: Path) -> bool:
    """
    Whether any workspace file exists in `directory`. Never raises (an
    unreadable folder counts as having some, so a failure path does not fail
    open).
    """
    try:
        if not directory.is_dir():
            return False
        return any(entry.name.endswith(PROFILE_SUFFIX) for entry in os.scandir(directory))
    except Exception:
        return True


def find_workspace_files(
    workspace_root: str | None,
    cwd: str | None,
    directory: Path,
    read: Reader,
    what: str = "workspace profile",
    every_ancestor: bool = False,
) -> list[Found]:
    """
    The workspace files that apply to a session, nearest first, as
    (root, path, raw) - the nearest file plus every managed file above it, or
    with `every_ancestor` every file from the session's folder up (for a
    feature whose profiles can only add to each other).

    Costs one directory check when no workspace file exists, and one existence
    check per folder level otherwise. A file whose declared root disagrees with
    its name is skipped and logged. Unexpected errors propagate: the caller
    decides how to fail (guardrails fails closed).
    """
    start = session_root(workspace_root, cwd)
    if start is None or not directory.is_dir():
        return []

    found: list[Found] = []
    for root in ancestors(start):
        path = directory / file_name_for(root)
        if not path.is_file():
            continue
        raw = read(path, what)
        declared = declared_root(raw)
        if declared is not None and declared != root:
            _log().warning(
                f"{what} {path.name} is for {declared}, but its name is for {root} "
                f"(copied or renamed?) - ignored"
            )
            continue
        found.append((root, path, raw))

    if not found or every_ancestor:
        return found
    nearest, *above = found
    return [nearest] + [entry for entry in above if is_managed(entry[2])]


def list_workspace_files(directory: Path, read: Reader, what: str = "workspace profile") -> list[dict]:
    """
    Every workspace file in `directory` with what it says about itself, for
    status commands and health routes. Not the hot path. Never raises.
    """
    entries: list[dict] = []
    try:
        if not directory.is_dir():
            return []
        for path in sorted(directory.glob(f"*{PROFILE_SUFFIX}")):
            raw = read(path, what)
            root = declared_root(raw)
            entries.append({
                "file": path.name,
                "root": root,
                "managed": is_managed(raw),
                "name_matches_root": root is not None and file_name_for(root) == path.name,
                "readable": raw is not None,
                "enabled": bool(isinstance(raw, dict) and raw.get("enabled") is True),
            })
    except Exception as exc:
        _log().warning(f"{what}s could not be listed: {exc}")
    return entries
