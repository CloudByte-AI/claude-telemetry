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

The naming, folder walk and managed marker are shared with security scanning
(src/common/workspace_profiles.py); this module adds the guardrails parts:
where the files live and how a file is loaded as a GuardrailsProfile.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.common import workspace_profiles as _shared
from src.common.paths import get_guardrails_dir
from src.common.workspace_profiles import (  # re-exported: guardrails code and tests import them from here
    MANAGED_KEY,
    PROFILE_SUFFIX,
    WORKSPACE_KEY,
    WORKSPACES_DIRNAME,
    ancestors,
    declared_root,
    file_name_for,
    is_managed,
    normalise_root,
    session_root,
)
from src.guardrails.config import (
    GuardrailsProfile,
    load_profile_file,
    read_yaml_mapping,
)

SOURCE_WORKSPACE = "workspace"
SOURCE_WORKSPACE_MANAGED = "workspace:managed"

_WHAT = "workspace guardrails profile"


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
    return _shared.workspaces_dir(get_guardrails_dir())


def path_for(root: str) -> Path:
    """Where the workspace file for this normalised root lives."""
    return _shared.path_for(root, get_guardrails_dir())


def any_workspace_policies() -> bool:
    """
    Whether any workspace file exists at all. Never raises (an unreadable
    folder counts as having some, so a failure path does not fail open).
    """
    try:
        return _shared.any_workspace_files(workspaces_dir())
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
        found = _shared.find_workspace_files(
            workspace_root, cwd, workspaces_dir(), read_yaml_mapping, what=_WHAT,
        )
        return [_load(root, path, raw) for root, path, raw in found]
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
    return _shared.list_workspace_files(workspaces_dir(), read_yaml_mapping, what=_WHAT)
