"""
Background repair of this plugin's Python packages.

Every hook runs with `uv run --no-sync`, so no hook waits on a package install or
on uv's environment lock (several open sessions starting at once would otherwise
queue behind each other). The packages are installed instead by one detached
`uv sync --frozen` for this plugin folder, started when a hook finds a required
package missing: a fresh install or an update (uv creates an empty environment),
an interrupted install, or a damaged environment. Until it finishes, code that
needs a missing package degrades (see src/common/text_repair.py) instead of
failing the hook.

Contract:
  - Nothing here raises, and nothing waits for the install.
  - At most one repair per plugin folder per REPAIR_COOLDOWN_SECONDS, across all
    processes and sessions, so a repair that cannot succeed (no network) is not
    retried on every hook.
  - The install's output goes to logs/setup/package-repair-<date>.log.
  - Stdlib only: this module must import in an environment with no packages.
"""

from __future__ import annotations

import functools
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from src.common.logging import get_logger
from src.common.paths import get_cloudbyte_dir, get_logs_dir

logger = get_logger(__name__)

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
REPAIR_COOLDOWN_SECONDS = 30 * 60
MARKER_DIR_NAME = "package_repair"
LOG_PREFIX = "package-repair"

_DISTRIBUTION_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_NAME_SEPARATORS = re.compile(r"[-_.]+")
_reported: set[str] = set()


@functools.cache
def required_distributions() -> tuple[str, ...]:
    """The distribution names in pyproject.toml `[project] dependencies`. Empty if unreadable."""
    try:
        import tomllib

        with open(PLUGIN_ROOT / "pyproject.toml", "rb") as handle:
            dependencies = tomllib.load(handle).get("project", {}).get("dependencies", [])
    except Exception as exc:
        logger.debug(f"package repair: pyproject.toml unreadable: {exc}")
        return ()
    names = (_DISTRIBUTION_NAME.match(str(spec)) for spec in dependencies)
    return tuple(match.group(1) for match in names if match)


def normalize_name(name: str) -> str:
    """The standard (PEP 503) normalized form of a distribution name."""
    return _NAME_SEPARATORS.sub("-", name).lower()


def installed_distributions() -> set[str]:
    """
    Normalized names of the distributions on sys.path, from their `.dist-info`
    and `.egg-info` folder names (`<name>-<version>.dist-info`). The same scan
    importlib.metadata does, without its import cost.
    """
    names = set()
    for entry in sys.path:
        try:
            with os.scandir(entry or ".") as folder:
                for item in folder:
                    stem, dot, suffix = item.name.rpartition(".")
                    if dot and suffix in ("dist-info", "egg-info"):
                        names.add(normalize_name(stem.split("-", 1)[0]))
        except OSError:
            continue
    return names


def missing_distributions() -> list[str]:
    """The required distributions not installed in the running environment."""
    installed = installed_distributions()
    return [name for name in required_distributions() if normalize_name(name) not in installed]


def repair_log_path() -> Path:
    """Today's repair log: logs/setup/package-repair-YYYY-MM-DD.log."""
    return get_logs_dir() / "setup" / f"{LOG_PREFIX}-{datetime.now():%Y-%m-%d}.log"


def check_and_repair() -> str | None:
    """
    Start a background repair when a required package is missing.

    Returns a short message for the user when a repair was started, None when
    nothing is missing or a repair already ran within the cooldown. Never raises.
    """
    try:
        missing = missing_distributions()
        if not missing:
            return None
        names = ", ".join(missing)
        # Every package missing is a new environment: the first start after an
        # install or update, which is expected. Some missing is a damaged one.
        new_environment = len(missing) == len(required_distributions())
        if new_environment:
            logger.info(f"package repair: new environment at {PLUGIN_ROOT}, installing packages")
        else:
            logger.error(f"CloudByte: plugin packages missing from {PLUGIN_ROOT}: {names}")
        if not start_repair(names):
            return None
        if new_environment:
            return (
                f"CloudByte: installing the plugin's packages in the background after "
                f"an install or update. The plugin keeps working with fewer features "
                f"until then. Details: {repair_log_path()}"
            )
        return (
            f"CloudByte: some plugin packages are missing ({names}). They are being "
            f"installed in the background, and the plugin keeps working with fewer "
            f"features until then. Details: {repair_log_path()}"
        )
    except Exception as exc:
        logger.debug(f"package repair: check failed: {exc}")
        return None


def report_missing(package: str, error: BaseException | None = None) -> None:
    """
    Record that `package` could not be imported, and start a background repair.

    For code that falls back when an optional import fails. Logs once per
    package per process. Never raises.
    """
    try:
        if package in _reported:
            return
        _reported.add(package)
        detail = f" ({type(error).__name__}: {error})" if error else ""
        logger.error(f"CloudByte: plugin package '{package}' could not be imported{detail}")
        start_repair(package)
    except Exception as exc:
        logger.debug(f"package repair: report failed: {exc}")


def report_if_missing(error: BaseException, package: str) -> None:
    """report_missing(package, error) when `error` is an ImportError; otherwise nothing."""
    if isinstance(error, ImportError):
        report_missing(package, error)


def start_repair(reason: str) -> bool:
    """
    Start one detached `uv sync --frozen` for this plugin folder unless another
    started within the cooldown. True when this call started it. Never raises.
    """
    try:
        if not _claim_repair_slot():
            logger.debug("package repair: a repair already ran within the cooldown")
            return False
        _spawn_sync(reason)
        logger.info(f"package repair: started a background uv sync for {PLUGIN_ROOT}")
        return True
    except Exception as exc:
        logger.warning(f"package repair: could not start uv sync: {exc}")
        return False


def _marker_path() -> Path:
    """One marker per plugin folder, so each installed version repairs its own environment."""
    folder_key = hashlib.sha1(str(PLUGIN_ROOT).lower().encode("utf-8")).hexdigest()[:12]
    return get_cloudbyte_dir() / MARKER_DIR_NAME / f"{folder_key}.started"


def _claim_repair_slot() -> bool:
    """
    Atomically claim the right to start a repair. A marker younger than the
    cooldown means no; an older one is renamed away first, and only the process
    whose rename succeeds goes on to create the new marker.
    """
    marker = _marker_path()
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        age = time.time() - marker.stat().st_mtime
    except FileNotFoundError:
        age = None
    if age is not None:
        if age < REPAIR_COOLDOWN_SECONDS:
            return False
        stale = marker.with_name(f"{marker.name}.{os.getpid()}.old")
        try:
            os.rename(marker, stale)
        except OSError:
            return False
        try:
            stale.unlink()
        except OSError:
            pass
    try:
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {PLUGIN_ROOT}\n")
    return True


def _sync_command() -> list[str]:
    """`uv sync --frozen` for this folder; UV is the uv binary that started this hook."""
    command_name = os.environ.get("UV") or shutil.which("uv") or "uv"
    return [command_name, "sync", "--frozen", "--directory", str(PLUGIN_ROOT)]


def _spawn_sync(reason: str) -> None:
    """Launch the sync detached from the hook, its output appended to the repair log."""
    log_path = repair_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = _sync_command()
    with open(log_path, "ab") as log:
        header = f"{datetime.now():%Y-%m-%d %H:%M:%S} repair started ({reason}): {subprocess.list2cmdline(command)}\n"
        log.write(header.encode("utf-8"))
        log.flush()
        if sys.platform == "win32":
            # Same launch as the dashboard (worker_checker): `start /B` keeps the
            # process alive after the hook that started it exits.
            subprocess.Popen(
                f'start /B "" {subprocess.list2cmdline(command)}',
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                command,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
