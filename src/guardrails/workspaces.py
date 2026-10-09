"""
Workspace policies - guardrails for one folder, on top of the global profile.

    ~/.cloudbyte/guardrails/
        global_profile.yaml                    every session on this machine
        workspaces/
            web-app-3f9a1c2b7d4e.yaml          sessions opened in that folder

A workspace is the folder a session was opened in (Claude Code's project
directory; the Cursor workspace root that holds the call's cwd), not the folder
a tool call happens to touch. Each workspace file has the same format as the
global profile, and the engine checks a call against the global profile and
the workspace profiles that apply, taking the stricter decision. So a
workspace file can only ADD restrictions to the global policy, never lift one.

Which workspace files apply to a session opened in folder S:

    1. the nearest file: S's own, or else the closest parent folder's
       (opening `repo/src` must not escape `repo`'s policy)
    2. plus every managed file further up (a local file in a subfolder
       must not escape a policy the organisation manages)

A nested folder with its own file therefore uses its own policy, not its
parent's, unless the parent's is managed.

The file NAME is the binding: `<folder>-<first 12 hex of sha256(root)>.yaml`,
where root is the folder's normalised path (see normalise_root). Finding the
files for a session is a handful of existence checks up the folder chain -
no index to keep in step, no directory listing on the hot path. A file may
declare its root under `ui_workspace.root`; when it does and the root does not
match its name (a copied or renamed file), it is ignored rather than applied
to the wrong folder.

Who writes the files:
    - the user, through `python -m src.main guardrails workspace init`
    - the CloudByte sync agent, for policies the organisation manages
      centrally. Its files carry a `ui_managed: {by: ...}` block and replace a
      user's file for the same folder (the agent keeps the user's copy as
      `<file>.user-backup`).

Whatever writes these files computes the names with the same algorithm;
tests/guardrails/test_workspaces.py pins the vectors it must reproduce.
"""

from __future__ import annotations

import hashlib
import os
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from src.common.paths import get_guardrails_dir
from src.guardrails.config import (
    EDITOR_KEY_PREFIX,
    GuardrailsProfile,
    load_profile_file,
    read_yaml_mapping,
)
from src.guardrails.toolcall import resolve_path

WORKSPACES_DIRNAME = "workspaces"
PROFILE_SUFFIX = ".yaml"

# Editor data the plugin reads here (and only here): which folder a file is
# for, and whether it is centrally managed. Both are `ui_` keys, so the policy
# parser and the fingerprint ignore them.
WORKSPACE_KEY = f"{EDITOR_KEY_PREFIX}workspace"
MANAGED_KEY = f"{EDITOR_KEY_PREFIX}managed"

SOURCE_WORKSPACE = "workspace"
SOURCE_WORKSPACE_MANAGED = "workspace:managed"

_HASH_LENGTH = 12
_SLUG_LENGTH = 40
_SLUG_INVALID = re.compile(r"[^a-z0-9._-]+")
_DRIVE_ROOT = re.compile(r"^[a-z]:/$", re.IGNORECASE)


def _log():
    """The logger, imported on first use - see config._log() for why."""
    from src.common.logging import get_logger
    return get_logger(__name__)


@dataclass(frozen=True)
class WorkspaceProfile:
    """One workspace file that applies to a session, loaded."""

    root: str               # normalised folder path (normalise_root)
    path: Path              # the file
    managed: bool           # centrally managed (written by the CloudByte sync agent)
    profile: GuardrailsProfile


def workspaces_dir() -> Path:
    return get_guardrails_dir() / WORKSPACES_DIRNAME


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
    engine free of host-platform checks (test_contracts.py). On a
    case-sensitive Linux disk, two sibling folders differing only in case
    share a policy - the stricter-decision rule makes that err on the safe side.
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
    with the same name apart. Changing this breaks every existing file, the
    sync agent - see the pinned vectors in test_workspaces.py.
    """
    base = posixpath.basename(root.rstrip("/")) if not _DRIVE_ROOT.match(root) else ""
    slug = _SLUG_INVALID.sub("-", base.lower()).strip("-.")[:_SLUG_LENGTH].strip("-.")
    digest = hashlib.sha256(root.encode("utf-8")).hexdigest()[:_HASH_LENGTH]
    return f"{slug or 'workspace'}-{digest}{PROFILE_SUFFIX}"


def path_for(root: str) -> Path:
    """Where the workspace file for this normalised root lives."""
    return workspaces_dir() / file_name_for(root)


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


def any_workspace_policies() -> bool:
    """
    Whether any workspace file exists at all. Never raises (an unreadable
    folder counts as having some, so a failure path does not fail open).
    """
    try:
        directory = workspaces_dir()
        if not directory.is_dir():
            return False
        return any(entry.name.endswith(PROFILE_SUFFIX) for entry in os.scandir(directory))
    except Exception:
        return True


def load_workspace_profiles(workspace_root: str | None, cwd: str | None) -> list[WorkspaceProfile]:
    """
    The workspace profiles that apply to a session, nearest first.

    Costs one directory check when no workspace policy exists, and one
    existence check per folder level otherwise. A broken FILE degrades like
    the global profile (load_profile_file); an unexpected error is logged and
    raised, so the adapters give their fail-closed answer rather than silently
    dropping a workspace policy. The kill switch is the caller's concern
    (engine.load_policies).
    """
    try:
        start = session_root(workspace_root, cwd)
        if start is None:
            return []
        directory = workspaces_dir()
        if not directory.is_dir():
            return []

        found: list[tuple[str, Path, dict | None]] = []
        for root in ancestors(start):
            path = directory / file_name_for(root)
            if not path.is_file():
                continue
            raw = read_yaml_mapping(path, what="workspace guardrails profile")
            declared = declared_root(raw)
            if declared is not None and declared != root:
                _log().warning(
                    f"workspace guardrails profile {path.name} is for {declared}, but its name "
                    f"is for {root} (copied or renamed?) - ignored"
                )
                continue
            found.append((root, path, raw))

        if not found:
            return []

        nearest, *above = found
        chosen = [nearest] + [entry for entry in above if is_managed(entry[2])]
        return [_load(root, path, raw) for root, path, raw in chosen]
    except Exception as exc:
        _log().warning(f"workspace guardrails profiles could not be read: {exc}")
        raise


def _load(root: str, path: Path, raw: dict | None) -> WorkspaceProfile:
    managed = is_managed(raw)
    source = SOURCE_WORKSPACE_MANAGED if managed else SOURCE_WORKSPACE
    return WorkspaceProfile(
        root=root,
        path=path,
        managed=managed,
        profile=load_profile_file(path, source=source, raw=raw),
    )


def list_workspace_files() -> list[dict]:
    """
    Every workspace file with what it says about itself, for the status
    command and the health route. Not the hot path. Never raises.
    """
    entries: list[dict] = []
    try:
        directory = workspaces_dir()
        if not directory.is_dir():
            return []
        for path in sorted(directory.glob(f"*{PROFILE_SUFFIX}")):
            raw = read_yaml_mapping(path, what="workspace guardrails profile")
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
        _log().warning(f"workspace guardrails profiles could not be listed: {exc}")
    return entries
