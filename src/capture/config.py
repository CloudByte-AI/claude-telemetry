"""
Capture settings.

    ~/.cloudbyte/capture/
        global_profile.yaml                 every session on this machine
        workspaces/<folder>-<hash>.yaml     sessions opened in that folder (workspace_profiles)

    enabled: true            # capture is ON when this file does not exist
    retention_days: 90       # evidence older than this is deleted (1-365)
    exclude: ["*.secret"]    # added to the built-in list of paths never recorded

Capture ships ON: a machine with no settings file records evidence. Turning it off is
`enabled: false` in the global file (or in a folder's file, for that folder and the folders
below it). Workspace files can only make capture stricter: switch it off or add exclusions.

Degradation ladder (load_profile never raises):

    kill switch env set           -> off
    global file absent            -> on, defaults
    global file unreadable        -> off   (a file exists to say something; never guess "on")
    schema newer than this code   -> off   (same reason)
    a workspace file unreadable   -> off for that folder
    anything unexpected           -> off, logged

Hot path: only `os` and `collections` (already loaded by then) at import time; the folder is
found with os.path (capture_dir, asserted equal to src.common.paths.get_capture_dir in the
tests). YAML, pathlib and the workspace lookup load only when a settings file exists.
"""

from __future__ import annotations

import os
from collections import namedtuple

KILL_SWITCH_ENV = "CLOUDBYTE_CAPTURE_DISABLED"
CAPTURE_RELATIVE_DIR = (".cloudbyte", "capture")
PROFILE_FILENAME = "global_profile.yaml"
WORKSPACES_DIRNAME = "workspaces"
SCHEMA_VERSION = 1
DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 365
_TRUTHY = ("1", "true", "yes", "on")

# Paths whose contents are never written to evidence: credentials and keys. The record
# still says that a file was edited and how many lines changed, but not what they were.
DEFAULT_EXCLUDE = (
    ".env", ".env.*", "*.env",
    "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore",
    "id_rsa*", "id_dsa*", "id_ecdsa*", "id_ed25519*",
    ".npmrc", ".pypirc", ".netrc", ".git-credentials",
    "credentials*", "*credentials.json", "secrets.*", "*.secret", "*.secrets",
)

CaptureProfile = namedtuple(
    "CaptureProfile", "enabled retention_days exclude source",
    defaults=(DEFAULT_RETENTION_DAYS, DEFAULT_EXCLUDE, "default"),
)


def capture_dir() -> str:
    """~/.cloudbyte/capture, with os.path alone (src.common.paths.get_capture_dir's value)."""
    return os.path.join(os.path.expanduser("~"), *CAPTURE_RELATIVE_DIR)


def _log():
    from src.common.logging import get_logger
    return get_logger(__name__)


def _off(source: str) -> CaptureProfile:
    return CaptureProfile(enabled=False, source=source)


def kill_switch_set(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def _retention(raw: dict) -> int:
    value = raw.get("retention_days", DEFAULT_RETENTION_DAYS)
    if isinstance(value, bool) or not isinstance(value, int):
        return DEFAULT_RETENTION_DAYS
    return max(MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, value))


def _extra_exclude(raw: dict) -> tuple:
    value = raw.get("exclude") or []
    if not isinstance(value, list):
        return ()
    return tuple(p.strip() for p in value if isinstance(p, str) and p.strip())


def _too_new(raw: dict) -> bool:
    version = raw.get("schema_version", SCHEMA_VERSION)
    return isinstance(version, int) and not isinstance(version, bool) and version > SCHEMA_VERSION


def load_profile(workspace_root: str | None = None, cwd: str | None = None) -> CaptureProfile:
    """The capture settings for a session in `workspace_root` / `cwd`. Never raises."""
    try:
        if kill_switch_set(os.environ.get(KILL_SWITCH_ENV)):
            return _off("kill_switch")
        directory = capture_dir()
        global_path = os.path.join(directory, PROFILE_FILENAME)
        workspaces = os.path.join(directory, WORKSPACES_DIRNAME)
        has_global, has_workspaces = os.path.exists(global_path), os.path.isdir(workspaces)
        if not (has_global or has_workspaces):
            return CaptureProfile(enabled=True, source="default")    # the usual path: nothing to read

        from pathlib import Path
        from src.common.yaml_cache import read_yaml_mapping

        retention, exclude, source = DEFAULT_RETENTION_DAYS, DEFAULT_EXCLUDE, "default"
        if has_global:
            raw = read_yaml_mapping(Path(global_path), "capture profile")
            if not isinstance(raw, dict):
                _log().warning("capture profile unreadable - capture off until it is fixed")
                return _off("unreadable")
            if _too_new(raw):
                return _off("schema_too_new")
            if raw.get("enabled") is False:
                return _off("user")
            retention, exclude, source = _retention(raw), exclude + _extra_exclude(raw), "user"

        if has_workspaces:
            from src.common.workspace_profiles import find_workspace_files
            found = find_workspace_files(workspace_root, cwd, Path(workspaces), read_yaml_mapping,
                                         what="capture workspace profile", every_ancestor=True)
            for _root, _path, raw in found:
                if not isinstance(raw, dict) or _too_new(raw):
                    return _off("workspace_unreadable")
                if raw.get("enabled") is False:
                    return _off("workspace")
                exclude = exclude + _extra_exclude(raw)
                source = "workspace"
        return CaptureProfile(enabled=True, retention_days=retention, exclude=exclude, source=source)
    except Exception as exc:
        try:
            _log().warning(f"capture settings could not be read - capture off: {exc}")
        except Exception:
            pass
        return _off("error")
