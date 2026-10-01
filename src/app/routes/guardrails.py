"""
Guardrails evaluation over HTTP, inside the running dashboard process, so no
Python process is spawned per governed tool call.

The adapters' pure `normalise()` and `render()` do the work, so nothing here
reimplements a decision. Claude Code can use this directly (`"type": "http"`);
Cursor hooks accept only "command" and "prompt", so Cursor needs a relay such
as curl. Enforcement depends on this process running; `/guardrails/health`
lets a deployment verify it.
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
        from src.guardrails.config import CURRENT_SCHEMA_VERSION, load_profile
        from src.guardrails.health import plugin_version
        from src.guardrails.registry import MatcherRegistry
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
        from src.handlers.pre_tool_use import evaluate_payload, normalise

        output, decision = evaluate_payload(payload if isinstance(payload, dict) else {})

        if decision.should_audit:
            try:
                from src.guardrails.spool import append_event
                append_event(decision, normalise(payload))
            except Exception as exc:
                logger.debug(f"guardrails: audit spool write failed (non-fatal): {exc}")

        return JSONResponse(output, background=_notification_task(decision, lambda: normalise(payload)))
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
    from src.cursor.handlers.guardrails import HOOKS, evaluate_payload, normalise

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
        output, decision = evaluate_payload(payload if isinstance(payload, dict) else {}, hook)

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
