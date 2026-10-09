"""
Guardrails evaluation over HTTP, inside the running dashboard process, so no
Python process is spawned per governed tool call.

The adapters' `decide_payload()` does the work, so nothing here reimplements a
decision. An opt-in transport: the shipped hooks run the check inside the hook
process, because a client that cannot reach this process lets the call through
unchecked. Claude Code can use it directly (`"type": "http"`); Cursor hooks
accept only "command" and "prompt", so Cursor needs a relay such as curl.
`/guardrails/health` lets a deployment verify this process is running.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def _kill_switch(request: Request) -> bool:
    """
    The caller's CLOUDBYTE_GUARDRAILS_DISABLED, forwarded as a header.

    Not this process's environment, which was fixed when the dashboard started.
    The hook forwards it (hooks/hooks.json `allowedEnvVars`); unset arrives empty.
    """
    from src.guardrails.config import KILL_SWITCH_HEADER, kill_switch_set
    return kill_switch_set(request.headers.get(KILL_SWITCH_HEADER))


def _notification_task(decision, build_call):
    """
    The decision's desktop notification, as a task Starlette runs after the
    response is sent. None when the decision earns none, or on any failure:
    it must never cost the decision its answer.
    """
    if not decision.is_opinion:
        return None
    try:
        from starlette.background import BackgroundTask
        from src.guardrails.notify import notify_decision
        return BackgroundTask(notify_decision, decision, build_call())
    except Exception as exc:
        logger.debug(f"guardrails: notification not scheduled (non-fatal): {exc}")
        return None


async def _read_payload(request: Request) -> dict:
    """
    The request body, decoded exactly as a command hook decodes its stdin.

    `request.json()` rejects a body that starts with a UTF-8 BOM, and a relay
    that pipes Cursor's stdin through unchanged sends one. Imported here, not at
    module level, so the dashboard does not load guardrails code at start-up.
    """
    from src.guardrails.hook_input import parse_payload
    return parse_payload(await request.body())


@router.get("/guardrails/health")
def guardrails_health() -> JSONResponse:
    """
    Whether guardrails are active, and on what.

    An HTTP-transport deployment should check this at SessionStart: if this
    process is not running, the hook fails open without logging anything.
    """
    try:
        from src.guardrails.config import CURRENT_SCHEMA_VERSION, load_profile, user_profile_path
        from src.guardrails.health import plugin_version
        from src.guardrails.registry import MatcherRegistry
        from src.guardrails.workspaces import list_workspace_files
        import src.guardrails.matchers  # noqa: F401

        profile = load_profile()
        return JSONResponse({
            "ok": True,
            # The version THIS PROCESS loaded, which may not be the installed
            # one; the SessionStart probe restarts the daemon on a mismatch.
            "plugin_version": plugin_version(),
            "enabled": profile.enabled,
            "plan": profile.plan,
            "source": profile.source,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "profile_errors": list(profile.errors),
            # Which file the global policy was read from (current or legacy name).
            "profile_file": user_profile_path().name,
            # Every workspace policy on this machine; which ones apply depends
            # on the session's folder, so none is "active" here.
            "workspaces": list_workspace_files(),
            "operations_implemented": len(MatcherRegistry.implemented_operations()),
            "operations_total": MatcherRegistry.count(),
        })
    except Exception as exc:
        logger.warning(f"guardrails health check failed: {exc}")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/guardrails/claude/pre_tool_use")
async def claude_pre_tool_use(request: Request) -> JSONResponse:
    """
    Claude Code PreToolUse over HTTP. Same output as the command hook, so `{}`
    means no opinion.

    Always answers 200: Claude Code treats a non-2xx response as a non-blocking
    error, so a 500 would look the same as the daemon being down.
    """
    if _kill_switch(request):
        return JSONResponse({})

    try:
        payload = await _read_payload(request)
    except Exception as exc:
        logger.warning(f"guardrails: unreadable request body, no opinion: {exc}")
        return JSONResponse({})

    try:
        from src.handlers.pre_tool_use import PROJECT_DIR_HEADER, decide_payload, normalise

        # The hook's CLAUDE_PROJECT_DIR, forwarded like the kill switch; this
        # process's own environment belongs to whichever session started it.
        project_dir = request.headers.get(PROJECT_DIR_HEADER) or None
        output, decision = decide_payload(payload if isinstance(payload, dict) else {}, project_dir)

        if decision.should_audit:
            try:
                from src.guardrails.spool import append_event
                append_event(decision, normalise(payload, project_dir))
            except Exception as exc:
                logger.debug(f"guardrails: audit spool write failed (non-fatal): {exc}")

        call = lambda: normalise(payload, project_dir)  # noqa: E731 - built only if notified
        return JSONResponse(output, background=_notification_task(decision, call))
    except Exception as exc:
        logger.error(f"guardrails: HTTP evaluation failed: {exc}", exc_info=True)
        return JSONResponse({})


@router.post("/guardrails/cursor/{hook_name}")
async def cursor_hook(hook_name: str, request: Request) -> JSONResponse:
    """
    Cursor's four pre-execution hooks over HTTP, for a relay-based deployment.

    Cursor blocks on a response that does not match the hook schema, so an
    unknown hook or a failed evaluation returns a plain `allow`, never an error.
    """
    from src.cursor.handlers.guardrails import HOOKS, decide_payload, normalise

    if _kill_switch(request):
        return JSONResponse({"permission": "allow"})

    hook = HOOKS.get(hook_name)
    if hook is None:
        logger.warning(f"guardrails: unknown cursor hook '{hook_name}'")
        return JSONResponse({"permission": "allow"})

    try:
        payload = await _read_payload(request)
    except Exception as exc:
        logger.warning(f"guardrails: unreadable request body, no opinion: {exc}")
        return JSONResponse({"permission": "allow"})

    try:
        output, decision = decide_payload(payload if isinstance(payload, dict) else {}, hook)

        if decision.should_audit:
            try:
                from src.guardrails.spool import append_event
                append_event(decision, normalise(payload, hook))
            except Exception as exc:
                logger.debug(f"guardrails: audit spool write failed (non-fatal): {exc}")

        return JSONResponse(output, background=_notification_task(decision, lambda: normalise(payload, hook)))
    except Exception as exc:
        logger.error(f"guardrails: HTTP evaluation failed: {exc}", exc_info=True)
        return JSONResponse({"permission": "allow"})
