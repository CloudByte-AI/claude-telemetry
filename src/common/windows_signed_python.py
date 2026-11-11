"""
Keep this plugin's Python environment on a signed interpreter on Windows.

Windows Smart App Control starts a program when Microsoft's cloud service knows
it is safe, or, when the service cannot decide, when the program carries a
valid signature. uv builds the plugin environment on its own Python downloads,
and the launcher it writes into the environment (.venv\\Scripts\\python.exe) is
unsigned, so only the cloud decision lets every hook start. An environment
built on a signed Python (the python.org installer) gets a signed launcher,
which passes on its signature.

This module moves the environment onto a signed Python that is already
installed, and otherwise reports that one is needed. It installs Python only
when the user asks for it (`install` below, run by the fix-python skill).

Contract:
  - Windows only. While Windows enforces signatures (Smart App Control on or
    evaluating, or a policy that just refused the launcher) any signed Python
    >= 3.12 is used, and the user is told when there is none. Otherwise the
    move is prevention, because Smart App Control can be switched on later:
    at most one search a day per plugin folder, only onto the pinned 3.12, and
    never a message.
  - The entry points never raise, and nothing waits for a rebuild: the move
    runs in a background process started on the signed interpreter itself.
  - Every child process starts with CREATE_NO_WINDOW, so no console window
    appears.
  - At most one rebuild per plugin folder at a time, through a lock file in the
    plugin folder itself (.venv.rebuild.lock) that any process and any tool
    rebuilding this environment can honour.
  - Off when CLOUDBYTE_SIGNED_PYTHON_DISABLED is set, and for a development
    checkout (a plugin folder with .git): a developer's environment is theirs.
  - Stdlib only, and no `src` import at module level: this file also runs as a
    script (the background move) and from the MCP launcher, outside the
    plugin environment.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

MINIMUM_VERSION = (3, 12)
PREFERRED_MINOR = (3, 12)               # the version .python-version pins
POLICY_BLOCK_ERRORS = (4551, 1260)      # App Control policy; blocked by group policy
NOTICE_INTERVAL_SECONDS = 24 * 3600     # how often "a signed Python is needed" repeats
SEARCH_INTERVAL_SECONDS = 24 * 3600     # how often an unenforced machine looks for one
# python.org revoked the signing certificate of these releases: Windows may
# refuse to run them, so they never count as signed.
REVOKED_VERSIONS = ((3, 12, 8), (3, 13, 1))
LOCK_STALE_SECONDS = 15 * 60            # a rebuild lock older than this is abandoned
REBUILD_TIMEOUT_SECONDS = 900
PYTHON_ORG_VERSION = "3.12.10"          # the last Python 3.12 release with Windows installers
PYTHON_ORG_MAX_BYTES = 64 * 1024 * 1024
SIGNER = "Python Software Foundation"

KILL_SWITCH = "CLOUDBYTE_SIGNED_PYTHON_DISABLED"
LOCK_NAME = ".venv.rebuild.lock"

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_NO_WINDOW = CREATE_NO_WINDOW if sys.platform == "win32" else 0

FIX_COMMAND = "/claude-telemetry:fix-python"
# The Windows one-line installer. Re-running it installs a signed Python and
# rebuilds a blocked environment on it.
INSTALLER_COMMAND = ("irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/"
                     "main/scripts/bootstrap.ps1 | iex")
INSTALLER_PAGE = "github.com/CloudByte-AI/claude-telemetry"
INSTALLER_HINT = "Or run the CloudByte installer in PowerShell:\n" + INSTALLER_COMMAND

# Session start message (Claude Code CLI). The command sits on its own line so
# it can be copied cleanly.
NEEDS_PYTHON_MESSAGE = (
    "CloudByte: Windows Smart App Control may block this plugin because it runs on an "
    "unsigned Python. To prevent this, use one of these options:\n"
    "1. Type " + FIX_COMMAND + " to install a signed Python.\n"
    "2. Run the CloudByte installer again in PowerShell:\n"
    "   " + INSTALLER_COMMAND + "\n"
    "3. Install Python 3.12 or newer from python.org, then restart Claude Code."
)

# Desktop notifications. A toast shows the title on one line, the text on up
# to two lines and the detail on one line (about 60 characters), and its text
# cannot be copied, so they point to the installer instead of quoting it.
NEEDS_PYTHON_TITLE = "CloudByte: Windows may block the plugin"
NEEDS_PYTHON_TEXT_CLAUDE = "To prevent it, type " + FIX_COMMAND + " or run the installer again."
NEEDS_PYTHON_TEXT_CURSOR = "To prevent it, run the CloudByte installer again in PowerShell."
BLOCKED_TITLE = "CloudByte: Windows blocked the plugin"
BLOCKED_TEXT = "To fix it, run the CloudByte installer again in PowerShell."
NOTICE_DETAIL = "Installer: " + INSTALLER_PAGE
BLOCKED_DETAIL = NOTICE_DETAIL


@dataclass(frozen=True)
class Outcome:
    """What ensure_signed_environment found or started."""

    action: str             # not_windows | disabled | development_checkout | not_enforced |
                            # no_environment | signed | in_progress | moving | needs_python | error
    message: str | None = None   # for the user, only when it is due
    base: str | None = None


# -- detection ------------------------------------------------------------------

def smart_app_control_state() -> int | None:
    """0 off, 1 on, 2 evaluation; None when unknown. Read from the registry, never raises."""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SYSTEM\CurrentControlSet\Control\CI\Policy") as key:
            return int(winreg.QueryValueEx(key, "VerifiedAndReputablePolicyState")[0])
    except (OSError, ValueError):
        return None


def enforced() -> bool:
    """Smart App Control on, or evaluating (Windows may switch it on by itself)."""
    return smart_app_control_state() in (1, 2)


def is_signed(path: Path | str) -> bool:
    """
    True when `path` carries a valid Authenticode signature that chains to a
    trusted root (WinVerifyTrust, the check Windows itself uses). No network,
    no process, no window. False on any other OS or on any error.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        class FileInfo(ctypes.Structure):
            _fields_ = [("cbStruct", wintypes.DWORD), ("pcwszFilePath", wintypes.LPCWSTR),
                        ("hFile", wintypes.HANDLE), ("pgKnownSubject", ctypes.c_void_p)]

        class TrustData(ctypes.Structure):
            _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                        ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                        ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                        ("pFile", ctypes.POINTER(FileInfo)), ("dwStateAction", wintypes.DWORD),
                        ("hWVTStateData", wintypes.HANDLE), ("pwszURLReference", wintypes.LPCWSTR),
                        ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
                        ("pSignatureSettings", ctypes.c_void_p)]

        generic_verify_v2 = GUID(0x00AAC56B, 0xCD44, 0x11D0,
                                 (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
        file_info = FileInfo(ctypes.sizeof(FileInfo), str(path), None, None)
        data = TrustData()
        data.cbStruct = ctypes.sizeof(TrustData)
        data.dwUIChoice = 2                  # WTD_UI_NONE
        data.fdwRevocationChecks = 0         # WTD_REVOKE_NONE
        data.dwUnionChoice = 1               # WTD_CHOICE_FILE
        data.pFile = ctypes.pointer(file_info)
        data.dwStateAction = 1               # WTD_STATEACTION_VERIFY
        data.dwProvFlags = 0x00001000        # WTD_CACHE_ONLY_URL_RETRIEVAL: never the network
        wintrust = ctypes.WinDLL("wintrust")
        wintrust.WinVerifyTrust.restype = ctypes.c_long
        wintrust.WinVerifyTrust.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.c_void_p]
        result = wintrust.WinVerifyTrust(None, ctypes.byref(generic_verify_v2), ctypes.byref(data))
        data.dwStateAction = 2               # WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(None, ctypes.byref(generic_verify_v2), ctypes.byref(data))
        return result == 0
    except Exception:
        return False


def launcher_path(root: Path = PLUGIN_ROOT) -> Path:
    """The environment's own python.exe: the file Windows decides about."""
    return root / ".venv" / "Scripts" / "python.exe"


def starts(executable: Path | str) -> bool | None:
    """True when it runs, None when a Windows policy refuses to start it, False otherwise."""
    try:
        result = subprocess.run([str(executable), "-I", "-c", "0"], capture_output=True,
                                timeout=30, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        return result.returncode == 0
    except OSError as exc:
        return None if getattr(exc, "winerror", None) in POLICY_BLOCK_ERRORS else False
    except Exception:
        return False


def is_policy_block(error: BaseException) -> bool:
    """True when an OSError says a Windows policy refused to start a program."""
    return getattr(error, "winerror", None) in POLICY_BLOCK_ERRORS


def uv_command() -> str | None:
    """The uv binary: the one that started this process (UV), else the one on PATH."""
    return os.environ.get("UV") or shutil.which("uv")


def _child_env() -> dict:
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)     # an active venv would make uv warn or target it
    return env


def _version_of(entry: dict) -> tuple[int, int, int]:
    parts = entry.get("version_parts") or {}
    if parts:
        return (int(parts.get("major", 0)), int(parts.get("minor", 0)), int(parts.get("patch", 0)))
    numbers = []
    for piece in str(entry.get("version", "")).split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        numbers.append(int(digits) if digits else 0)
    return tuple((numbers + [0, 0, 0])[:3])


def find_signed_python(uv: str | None = None, pinned_only: bool = False) -> str | None:
    """
    A system CPython >= 3.12 that is signed and starts: the pinned 3.12 first,
    then the newest (only the pinned 3.12 with `pinned_only`). uv's own
    downloads are skipped (they are unsigned), and so are aliases such as the
    Python install manager's unsigned bin\\python.exe and REVOKED_VERSIONS.
    None when there is none or on any error.
    """
    uv = uv or uv_command()
    if sys.platform != "win32" or not uv:
        return None
    try:
        listing = subprocess.run([uv, "python", "list", "--only-installed", "--output-format", "json"],
                                 capture_output=True, text=True, timeout=60, env=_child_env(),
                                 stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        managed = subprocess.run([uv, "python", "dir"], capture_output=True, text=True, timeout=30,
                                 env=_child_env(), stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        managed_root = Path(managed.stdout.strip()).resolve() if managed.stdout.strip() else None
        candidates = {}
        for entry in json.loads(listing.stdout or "[]"):
            path = Path(entry.get("path") or "")
            version = _version_of(entry)
            if entry.get("implementation") != "cpython" or version[:2] < MINIMUM_VERSION:
                continue
            if version in REVOKED_VERSIONS or (pinned_only and version[:2] != PREFERRED_MINOR):
                continue
            if path.name.lower() != "python.exe" or not path.is_file():
                continue
            resolved = path.resolve()
            if managed_root and (resolved == managed_root or managed_root in resolved.parents):
                continue
            candidates[str(path)] = version
        usable = [(version, path) for path, version in candidates.items()
                  if is_signed(path) and starts(path) is True]
        usable.sort(key=lambda item: (item[0][:2] == PREFERRED_MINOR, item[0]), reverse=True)
        return usable[0][1] if usable else None
    except Exception:
        return None


# -- shared state: lock, notice, parked environments ----------------------------

def folder_key(root: Path = PLUGIN_ROOT) -> str:
    """The per-folder key package_repair uses for its markers."""
    return hashlib.sha1(str(root).lower().encode("utf-8")).hexdigest()[:12]


def _state_dir() -> Path:
    try:
        from src.common.paths import get_cloudbyte_dir

        base = get_cloudbyte_dir()
    except Exception:
        base = Path.home() / ".cloudbyte"
    return base / "package_repair"


def rebuild_lock_path(root: Path = PLUGIN_ROOT) -> Path:
    """In the plugin folder, so every process sees it whatever its home folder."""
    return root / LOCK_NAME


def off_reason(root: Path = PLUGIN_ROOT) -> str | None:
    """'disabled' (kill switch), 'development_checkout' (.git), or None when allowed."""
    if os.environ.get(KILL_SWITCH):
        return "disabled"
    if (root / ".git").exists():
        return "development_checkout"
    return None


def rebuild_in_progress(root: Path = PLUGIN_ROOT) -> bool:
    """A fresh rebuild lock is held for this folder."""
    try:
        return time.time() - rebuild_lock_path(root).stat().st_mtime < LOCK_STALE_SECONDS
    except OSError:
        return False


def _acquire_lock(root: Path) -> bool:
    lock = rebuild_lock_path(root)
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists() and not rebuild_in_progress(root):
        try:
            lock.unlink()                 # abandoned by a process that died
        except OSError:
            return False
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except OSError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} pid={os.getpid()} {root}\n")
    return True


def _release_lock(root: Path) -> None:
    try:
        rebuild_lock_path(root).unlink()
    except OSError:
        pass


def _notice_due(root: Path) -> bool:
    """True at most once per NOTICE_INTERVAL_SECONDS per folder; records the notice."""
    return _due(root, "signed-python-notice", NOTICE_INTERVAL_SECONDS)


def _search_due(root: Path) -> bool:
    """True at most once per SEARCH_INTERVAL_SECONDS per folder; records the search."""
    return _due(root, "signed-python-search", SEARCH_INTERVAL_SECONDS)


def _due(root: Path, name: str, interval: int) -> bool:
    """True at most once per `interval` seconds per folder for `name`; records the time."""
    marker = _state_dir() / f"{folder_key(root)}.{name}"
    try:
        if time.time() - marker.stat().st_mtime < interval:
            return False
    except OSError:
        pass
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    except OSError:
        return False
    return True


def remove_parked(root: Path = PLUGIN_ROOT) -> None:
    """
    Delete environments swapped out earlier, and builds a killed rebuild left.
    One still in use stays for a later call.
    """
    for parked in (*root.glob(".venv.unsigned-*"), *root.glob(".venv.signed-*")):
        if parked.name.startswith(".venv.signed-") and rebuild_in_progress(root):
            continue                      # the running rebuild's own build folder
        shutil.rmtree(parked, ignore_errors=True)


def _write_state(root: Path, record: dict) -> None:
    try:
        path = _state_dir() / f"{folder_key(root)}.signed-python.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, indent=1), encoding="utf-8")
    except OSError:
        pass


def _log(level: str, message: str) -> None:
    """One line in the CloudByte log. The background move sets the log up itself."""
    try:
        from src.common.logging import get_logger

        getattr(get_logger(__name__), level)(message)
    except Exception:
        pass


# -- entry points ---------------------------------------------------------------

def ensure_signed_environment(trigger: str, root: Path = PLUGIN_ROOT,
                              launcher_blocked: bool = False) -> Outcome:
    """
    Make sure this folder's environment is on a signed Python. Starts the move in
    the background when a signed Python is installed; otherwise returns the
    message for the user when it is due. `launcher_blocked` says the caller has
    already seen Windows refuse the launcher. Never raises.
    """
    try:
        if sys.platform != "win32":
            return Outcome("not_windows")
        reason = off_reason(root)
        if reason:
            return Outcome(reason)
        launcher = launcher_path(root)
        if not launcher.is_file():
            return Outcome("no_environment")
        if not launcher_blocked and is_signed(launcher):
            remove_parked(root)
            return Outcome("signed")
        if rebuild_in_progress(root):
            return Outcome("in_progress")
        enforcing = launcher_blocked or enforced()
        if not enforcing and not _search_due(root):
            return Outcome("not_enforced")
        base = find_signed_python(pinned_only=not enforcing)
        if base:
            start_move(base, trigger, root)
            _log("info", f"signed python: moving the plugin environment to {base} ({trigger})")
            return Outcome("moving", base=base)
        if not enforcing:
            return Outcome("not_enforced")
        _log("warning", f"signed python: Windows enforces signatures and no signed Python is "
                        f"installed; the plugin environment stays unsigned ({trigger})")
        return Outcome("needs_python", NEEDS_PYTHON_MESSAGE if _notice_due(root) else None)
    except Exception as exc:
        _log("debug", f"signed python: check failed: {exc}")
        return Outcome("error")


def notify_needs_python(client: str) -> bool:
    """
    The "a signed Python is needed" notification, for surfaces that do not show a
    hook's message (the VS Code extension, Cursor). True when it was handed to
    the OS. Never raises.
    """
    try:
        from src.common.notifications import WARN, Notice, show

        text = NEEDS_PYTHON_TEXT_CLAUDE if client == "claude" else NEEDS_PYTHON_TEXT_CURSOR
        return show(Notice(level=WARN, title=NEEDS_PYTHON_TITLE, text=text,
                           detail=NOTICE_DETAIL, tag="cloudbyte-signed-python"))
    except Exception:
        return False


def notify_blocked() -> bool:
    """The "Windows blocked the plugin's Python" notification, with the steps. Never raises."""
    try:
        from src.common.notifications import CRITICAL, Notice, show

        return show(Notice(level=CRITICAL, title=BLOCKED_TITLE, text=BLOCKED_TEXT,
                           detail=BLOCKED_DETAIL, tag="cloudbyte-signed-python"))
    except Exception:
        return False


def _spawn_background(argv: list[str], root: Path) -> None:
    """
    Start `argv` hidden (no console window), without our stdin, and outside the
    caller's job when the job allows it, so it outlives a short-lived parent
    such as a hook or an MCP launcher the client closes.
    """
    if sys.platform != "win32":                  # creationflags is Windows only
        subprocess.Popen(argv, cwd=str(root), env=_child_env(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         start_new_session=True, creationflags=0)
        return
    flags = _NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(argv, cwd=str(root), env=_child_env(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=flags | CREATE_BREAKAWAY_FROM_JOB)
    except OSError:
        # A job that forbids breakaway makes CreateProcess fail outright; run
        # inside it rather than not at all.
        subprocess.Popen(argv, cwd=str(root), env=_child_env(), stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                         creationflags=flags)


def start_move(base: str, trigger: str, root: Path = PLUGIN_ROOT) -> None:
    """
    Run the move in a background process on the signed interpreter itself, so
    it never depends on the launcher being replaced.
    """
    _spawn_background([base, "-I", str(Path(__file__).resolve()), "move", str(root), trigger], root)


def start_check(trigger: str, root: Path = PLUGIN_ROOT, launcher_blocked: bool = False) -> None:
    """
    Run ensure_signed_environment in a background process on this interpreter,
    returning at once: for a caller that may be closed within a second, like the
    MCP launcher of a session whose first prompt Windows just blocked. That
    process also shows the "blocked" notification when no signed Python exists.
    """
    argv = [sys.executable, "-I", str(Path(__file__).resolve()), "ensure", str(root), trigger]
    _spawn_background(argv + (["--blocked"] if launcher_blocked else []), root)


def move_environment(root: Path, trigger: str, base: str | None = None) -> dict:
    """
    Rebuild root/.venv on `base` (default: the interpreter running this code).
    The new environment is built beside the old one, which keeps serving hooks
    meanwhile, checked, and then swapped in with two renames, so there is no
    moment without an environment. The old one is deleted once nothing uses
    it. Returns the record it also writes next to the lock. Never raises.
    """
    base = base or sys.executable
    record = {"at": datetime.now().isoformat(timespec="seconds"), "trigger": trigger,
              "plugin": str(root), "base": base}
    reason = off_reason(root)
    if reason:
        record["action"] = reason
        return record
    if not _acquire_lock(root):
        record["action"] = "in_progress"
        return record
    started = time.time()
    build = root / f".venv.signed-{int(started)}"
    try:
        venv = root / ".venv"
        if venv.is_dir() and is_signed(launcher_path(root)) and starts(launcher_path(root)) is True:
            record["action"] = "already_signed"
            return record
        uv = uv_command()
        if not uv or not is_signed(base):
            record.update(action="failed", reason="no uv" if not uv else "base not signed")
            return record
        env = {**_child_env(), "UV_PROJECT_ENVIRONMENT": str(build)}
        sync = subprocess.run([uv, "sync", "--frozen", "--python", base, "--directory", str(root)],
                              capture_output=True, text=True, timeout=REBUILD_TIMEOUT_SECONDS,
                              env=env, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        new_launcher = build / "Scripts" / "python.exe"
        if sync.returncode != 0 or not is_signed(new_launcher) or starts(new_launcher) is not True:
            record.update(action="failed", reason=(sync.stderr or f"uv sync exited {sync.returncode}")[-300:])
            return record
        if venv.is_dir():
            os.replace(venv, root / f".venv.unsigned-{int(started)}")
        os.replace(build, venv)
        record["action"] = "moved"
        remove_parked(root)
    except Exception as exc:
        record.update(action="failed", reason=f"{type(exc).__name__}: {exc}"[:300])
    finally:
        if build.exists():
            shutil.rmtree(build, ignore_errors=True)
        _release_lock(root)
        record["seconds"] = round(time.time() - started, 1)
        _write_state(root, record)
    if record["action"] == "moved":
        _log("info", f"signed python: moved the plugin environment to {base} in {record['seconds']}s")
    elif record["action"] == "failed":
        _log("error", f"signed python: moving the plugin environment failed: {record.get('reason')}")
    return record


def sync_python_args(root: Path = PLUGIN_ROOT) -> list[str]:
    """
    `--python <base>` for a uv command that may sync this folder, when the
    environment was moved onto a signed Python: without it uv would rebuild the
    environment on the pinned version again. Empty otherwise. Never raises.
    """
    try:
        if sys.platform != "win32" or not is_signed(launcher_path(root)):
            return []
        for line in (root / ".venv" / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home":
                base = Path(value.strip()) / "python.exe"
                return ["--python", str(base)] if base.is_file() else []
    except Exception:
        pass
    return []


def install(root: Path = PLUGIN_ROOT) -> str:
    """
    Install python.org's Python 3.12 for this user (no PATH change, no py
    launcher) when no signed Python is installed, then move the environment
    onto it. Only for an explicit request from the user. Returns the text to
    show them. Never raises.
    """
    if sys.platform != "win32":
        return "CloudByte: no change needed. This fix is for Windows only."
    if off_reason(root):
        return f"CloudByte: no change made ({off_reason(root).replace('_', ' ')})."
    base = find_signed_python()
    if not base:
        error = _install_python_org()
        if error:
            return f"CloudByte: Python could not be installed from python.org ({error}).\n{INSTALLER_HINT}"
        base = find_signed_python()
        if not base:
            return ("CloudByte: Python is installed but not visible yet. "
                    f"Restart Claude Code, then type {FIX_COMMAND} again.")
    record = move_environment(root, "install", base)
    if record["action"] in ("moved", "already_signed"):
        return f"CloudByte: done. The plugin now uses the signed Python at {base}."
    reason = record.get("reason", record["action"])
    return f"CloudByte: the plugin could not switch to the signed Python ({reason}).\n{INSTALLER_HINT}"


def _signer_subject(path: Path) -> str:
    """The signing certificate's subject, through PowerShell (one call, no window)."""
    script = "(Get-AuthenticodeSignature -LiteralPath $env:CB_SIGNED_FILE).SignerCertificate.Subject"
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                            env={**os.environ, "CB_SIGNED_FILE": str(path)}, capture_output=True,
                            text=True, timeout=60, stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    return result.stdout.strip()


def _install_python_org() -> str:
    """Download, verify and quietly install python.org's installer. '' on success, else the reason."""
    import platform

    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    name = f"python-{PYTHON_ORG_VERSION}-{arch}.exe"
    url = f"https://www.python.org/ftp/python/{PYTHON_ORG_VERSION}/{name}"
    target = Path(tempfile.gettempdir()) / f"cloudbyte-{os.getpid()}-{name}"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "CloudByte-plugin"})
        size = 0
        with urllib.request.urlopen(request, timeout=60) as response, open(target, "wb") as out:
            while chunk := response.read(1 << 20):
                size += len(chunk)
                if size > PYTHON_ORG_MAX_BYTES:
                    return "the download is larger than expected"
                out.write(chunk)
        if not is_signed(target) or SIGNER not in _signer_subject(target):
            return "the installer is not signed by the Python Software Foundation"
        result = subprocess.run([str(target), "/quiet", "InstallAllUsers=0", "PrependPath=0",
                                 "Include_launcher=0", "Include_test=0", "Shortcuts=0",
                                 "AssociateFiles=0"], capture_output=True, timeout=900,
                                stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        if result.returncode not in (0, 3010):          # 3010: installed, restart pending
            return f"the installer exited {result.returncode}"
        return ""
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"[:200]
    finally:
        try:
            target.unlink()
        except OSError:
            pass


def main(argv: list[str]) -> int:
    """
    `ensure <plugin root> <trigger> [--blocked]`: the background check (start_check).
    `move <plugin root> <trigger>`: the background move.
    `install`: the user's explicit request to install a signed Python.
    """
    if str(PLUGIN_ROOT) not in sys.path:
        sys.path.insert(0, str(PLUGIN_ROOT))
    try:
        from src.common.logging import setup_logging

        setup_logging(log_to_file=True, log_to_console=False)
    except Exception:
        pass
    if argv[:1] == ["ensure"] and len(argv) >= 3:
        blocked = "--blocked" in argv[3:]
        outcome = ensure_signed_environment(argv[2], Path(argv[1]), launcher_blocked=blocked)
        if blocked and outcome.action == "needs_python" and outcome.message:
            notify_blocked()
        return 0
    if argv[:1] == ["move"] and len(argv) >= 2:
        record = move_environment(Path(argv[1]), argv[2] if len(argv) > 2 else "unknown")
        return 0 if record.get("action") in ("moved", "already_signed", "in_progress") else 1
    if argv[:1] == ["install"]:
        print(install())
        return 0
    print("usage: windows_signed_python.py move <plugin root> <trigger> | install", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
