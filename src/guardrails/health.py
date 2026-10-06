"""
Daemon health probe for the opt-in HTTP transport.

The shipped hooks run the check inside the hook process and need no daemon, so
callers probe only when http_transport_configured() says a hook config routes
guardrails over HTTP. With that transport, enforcement needs a process that is
alive AND running current code, and both can fail silently:

    daemon stopped    every governed call fails open. Claude Code treats a
                      connection failure as a non-blocking error, so the tool
                      proceeds and nothing is logged.

    daemon stale      the engine is loaded once at start-up, so a plugin update
                      does not reach it. Rules stay live (the profile is re-read
                      per call), but new matchers and bug fixes do not.

Platform-neutral: no adapter imports, no platform branching (enforced by
tests/guardrails/test_contracts.py).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

HEALTH_URL = "http://127.0.0.1:4723/guardrails/health"

# Every guardrails route lives under this path.
ROUTE_PREFIX = "/guardrails/"

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

# Every dashboard version, including pre-guardrails ones, serves /openapi.json.
# Its title tells a 404 from an old CloudByte dashboard (safe to restart) apart
# from another program using the port (never touched).
OPENAPI_URL = "http://127.0.0.1:4723/openapi.json"
DASHBOARD_TITLE = "CloudByte Dashboard"

# Short on purpose: a daemon this slow to answer is one the user needs telling
# about either way.
PROBE_TIMEOUT_SECONDS = 2.0

OK = "ok"
SKIPPED = "skipped"          # guardrails are off - nothing to check
UNREACHABLE = "unreachable"  # nothing is answering on the port
STALE = "stale"              # answering, but running superseded code
ERROR = "error"


@dataclass(frozen=True)
class ProbeResult:
    status: str
    message: str = ""
    daemon_version: str | None = None
    plugin_version: str | None = None

    @property
    def degraded(self) -> bool:
        """True when the user should be told something."""
        return self.status in (UNREACHABLE, STALE, ERROR)


def plugin_version() -> str | None:
    """
    This plugin's version, from its own manifest.

    Read here rather than imported from src.main, which pulls in every handler,
    ftfy and the database layer for a single string.
    """
    try:
        manifest = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
        return json.loads(manifest.read_text(encoding="utf-8")).get("version")
    except Exception:
        return None


def http_transport_configured(hooks_file: Path) -> bool:
    """
    True when a hook config sends guardrails checks to this plugin's HTTP
    routes. Never raises; an unreadable file counts as not configured.

    Probing a daemon the configured transport does not use would warn that
    tool calls are not checked while they are, and would delay every prompt.
    """
    try:
        config = json.loads(Path(hooks_file).read_text(encoding="utf-8"))
        for groups in (config.get("hooks") or {}).values():
            for group in groups or ():
                for handler in group.get("hooks") or ():
                    if handler.get("type") == "http" and ROUTE_PREFIX in str(handler.get("url", "")):
                        return True
    except Exception:
        pass
    return False


def guardrails_enabled() -> bool:
    """True when a profile on disk switches guardrails on. Never raises."""
    try:
        from src.guardrails.config import load_profile
        return bool(load_profile().enabled)
    except Exception:
        return False


def probe(url: str = HEALTH_URL, timeout: float = PROBE_TIMEOUT_SECONDS) -> ProbeResult:
    """
    Ask the daemon whether it is alive and current. Never raises.

    Returns SKIPPED when guardrails are disabled, so a user who never enabled
    the feature is never warned about infrastructure they are not using.
    """
    if not guardrails_enabled():
        return ProbeResult(SKIPPED)
    return _probe_http(url, timeout)


def _probe_http(url: str = HEALTH_URL, timeout: float = PROBE_TIMEOUT_SECONDS) -> ProbeResult:
    """The network half of probe(), without re-reading the profile each time."""
    mine = plugin_version()

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # Something IS answering, so this is not UNREACHABLE (a spawn would find
        # the port taken and do nothing). A 404 from our own dashboard means it
        # predates the guardrails routes: stale, and a restart fixes it.
        if exc.code == 404 and _is_our_dashboard(timeout):
            return ProbeResult(
                STALE,
                "The CloudByte dashboard on port 4723 is running a version without "
                "guardrails, so tool calls are NOT being checked. Restarting it.",
                daemon_version="pre-guardrails",
                plugin_version=mine,
            )
        return ProbeResult(
            ERROR,
            f"Guardrails are enabled but port 4723 answered the health check with HTTP "
            f"{exc.code}"
            + ("" if _is_our_dashboard(timeout) else
               ", and it is not the CloudByte dashboard - another program is using the port")
            + ". Tool calls are NOT being checked.",
            plugin_version=mine,
        )
    except Exception:
        return ProbeResult(
            UNREACHABLE,
            "Guardrails are enabled but the CloudByte dashboard is not answering on "
            "port 4723, so tool calls are NOT being checked. Start it, or switch the "
            "PreToolUse hook back to the command form.",
            plugin_version=mine,
        )

    if not body.get("ok"):
        return ProbeResult(
            ERROR,
            f"Guardrails are enabled but the policy engine reported a problem: "
            f"{body.get('error', 'unknown')}.",
            plugin_version=mine,
        )

    theirs = body.get("plugin_version")
    if mine and theirs and theirs != mine:
        return ProbeResult(
            STALE,
            f"The dashboard is running plugin {theirs} while {mine} is installed, so "
            f"guardrails are being enforced by superseded code. Restarting it.",
            daemon_version=theirs,
            plugin_version=mine,
        )

    return ProbeResult(OK, daemon_version=theirs, plugin_version=mine)


def _is_our_dashboard(timeout: float = PROBE_TIMEOUT_SECONDS) -> bool:
    """True when the process on port 4723 is a CloudByte dashboard, of any version."""
    try:
        import urllib.request
        with urllib.request.urlopen(OPENAPI_URL, timeout=timeout) as response:
            info = json.loads(response.read().decode("utf-8")).get("info") or {}
        return info.get("title") == DASHBOARD_TITLE
    except Exception:
        return False


def _port_in_use() -> bool:
    try:
        from src.workers.worker_checker import is_port_open
        return bool(is_port_open(port=4723))
    except Exception:
        return False


def restart_daemon() -> bool:
    """
    Stop and restart the dashboard so it picks up current code. Never raises.

    Only called for STALE, which is only reported for a process confirmed to be
    our own dashboard. Other sessions use plain HTTP, so they reconnect on their
    next call. worker.pid is not always written, so if the port is still held
    after stopping by PID, the process holding the port is stopped.
    """
    try:
        from src.workers.kill_worker import kill_worker_by_pid
        kill_worker_by_pid()
    except Exception:
        pass
    try:
        if _port_in_use():
            from src.workers.kill_worker import kill_worker_by_port
            kill_worker_by_port()
    except Exception:
        pass
    try:
        from src.workers.llm_client import ensure_worker_running
        return bool(ensure_worker_running())
    except Exception:
        return False


def start_daemon() -> None:
    """Spawn the dashboard without waiting for it. Never raises."""
    try:
        from src.workers.worker_checker import ensure_worker_quick_sync
        ensure_worker_quick_sync()
    except Exception:
        pass


# How long to wait for a starting daemon before warning: well above a normal
# cold start, yet short enough that a broken daemon is reported quickly.
READY_TIMEOUT_SECONDS = 5.0


def ensure_ready(timeout: float = READY_TIMEOUT_SECONDS) -> ProbeResult:
    """
    Make the daemon ready before the turn's tool calls start, and say so if it
    cannot be. Never raises.

    Called per prompt: starts a dead daemon and WAITS for it (start_daemon()
    does not, so tool calls could hit a port not listening yet), catches a
    wedged daemon a port check would miss, and restarts one left stale by a
    plugin update.
    """
    import time

    if not guardrails_enabled():
        return ProbeResult(SKIPPED)

    deadline = time.monotonic() + max(0.0, timeout)
    spawned = False
    restarted = False
    last = _probe_http()

    while True:
        if last.status == OK:
            return last

        acted = False
        if last.status == STALE and not restarted:
            restarted = acted = True
            restart_daemon()
        elif last.status == UNREACHABLE and not spawned:
            spawned = acted = True
            start_daemon()
        elif last.status == ERROR:
            # The daemon is answering and reporting a problem of its own.
            # Restarting will not fix a broken profile or taxonomy.
            return last

        if time.monotonic() >= deadline:
            # A restart can use the whole budget by itself, so re-probe to judge
            # what it did, not the state before it.
            if acted:
                last = _probe_http()
            break
        time.sleep(0.25)
        last = _probe_http()

    if last.status == OK:
        return last
    if last.status == STALE:
        return ProbeResult(
            STALE,
            f"The dashboard is running plugin {last.daemon_version} while "
            f"{last.plugin_version} is installed, and it could not be restarted "
            f"automatically. Restart it so guardrails enforce current policy.",
            daemon_version=last.daemon_version,
            plugin_version=last.plugin_version,
        )
    return last


# The name SessionStart imports.
check_and_repair = ensure_ready
