"""
Claude Code adapter for model access (src/guardrails/model_access.py).

    PreModelSwitch     handle_model_switch()     deny a switch to a blocked model
    PostModelSwitch    handle_model_switched()   remember the session's new model
    PreToolUse Agent   handle_agent_model()      deny a subagent that asks for a blocked model
    UserPromptSubmit   check_prompt()            block a prompt while a blocked model is in use
    SessionStart       on_session_start()        remember the model, warn when it is blocked

The three hooks are registered in hooks/model-hooks.json, never in
hooks/hooks.json: a Claude Code release that predates the model-switch events
rejects a hook file that names one, and with it every hook in that file.

Every path fails open. A check that cannot run allows the model, because a
broken plugin must never stop the user; the prompt-time check runs again on
the next prompt. A PreModelSwitch hook that times out blocks the switch, so
these hooks answer quickly and never wait on anything.
"""

from __future__ import annotations

import json
import os

from src.guardrails.model_access import Blocked, ModelCall, ModelRequest, evaluate, message, record

PLATFORM = "claude_code"
# The folder the session was opened in, which picks the workspace policy (the
# payload's `cwd` follows the agent's `cd`).
PROJECT_DIR_ENV = "CLAUDE_PROJECT_DIR"

# Prompts Claude Code submits on its own: a subagent handing its result back.
_NOT_TYPED_BY_THE_USER = ("<agent-message",)

SWITCH_NEXT_STEP = "Pick another model."
PROMPT_NEXT_STEP = "Switch to an allowed model with /model, then send your prompt again."
SESSION_NEXT_STEP = "Switch to an allowed model with /model before you send a prompt."
AGENT_NEXT_STEP = "Run the subagent without a model setting, or with an allowed model."


def _log():
    from src.common.logging import get_logger
    return get_logger(__name__)


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _names(*values) -> tuple[str, ...]:
    return tuple(dict.fromkeys(name for name in (_text(value) for value in values) if name))


def _deny(event: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def _read_payload() -> dict:
    from src.guardrails.hook_input import read_stdin_payload
    return read_stdin_payload()


_log_ready = False


def _claude_log() -> None:
    """
    Send this hook process's log lines to the plugin's Claude log, once, so a
    block and its notification outcome are written down. Only a block calls
    it: setting it up costs more than the check. Never raises.
    """
    global _log_ready
    if _log_ready:
        return
    try:
        from src.common.logging import setup_logging
        from src.common.paths import get_claude_logs_dir
        setup_logging(log_to_file=True, log_to_console=False, log_dir=get_claude_logs_dir())
    except Exception:
        pass
    _log_ready = True


def _check(payload: dict, request: ModelRequest, answer, *, prompt_id=None, tool_use_id=None,
           tool_name: str = "model", own_log: bool = False) -> Blocked | None:
    """
    None when the model is allowed. Otherwise the block, with its audit event
    spooled; `answer(decision)` builds the hook's answer. `own_log`: this hook
    process has no log file yet (the model-hooks.json hooks), so a block sets
    it up before writing its line.
    """
    workspace_root = os.environ.get(PROJECT_DIR_ENV)
    cwd = _text(payload.get("cwd")) or None
    decision = evaluate(request, workspace_root=workspace_root, cwd=cwd)
    if decision is None:
        return None
    if own_log:
        _claude_log()
    call = ModelCall(
        platform=PLATFORM,
        session_id=_text(payload.get("session_id")) or None,
        prompt_id=prompt_id,
        tool_use_id=tool_use_id,
        tool_name=tool_name,
        cwd=cwd,
        workspace_root=workspace_root,
        model=request.display,
    )
    record(decision, call)
    _log().info(f"model access: blocked {request.display or 'unknown model'!r} "
                f"({decision.rule_id}, {decision.policy_scope})")
    return Blocked(answer(decision), decision, call)


def _answer_hook(blocked: Blocked | None) -> int:
    """Print the hook's answer (`{}` when allowed), then notify about a block."""
    print(json.dumps(blocked.answer if blocked is not None else {}))
    if blocked is not None:
        blocked.notify()
    return 0


def handle_model_switch() -> int:
    """PreModelSwitch: deny a switch to a blocked model; `{}` lets it happen."""
    blocked = None
    try:
        payload = _read_payload()
        request = ModelRequest(_names(payload.get("to_model"), payload.get("requested_model")))
        if request.names:
            blocked = _check(payload, request, lambda decision: _deny(
                "PreModelSwitch", message(decision, SWITCH_NEXT_STEP)), own_log=True)
    except Exception as exc:
        _log().warning(f"model access: model switch check failed, switch allowed: {exc}")
        blocked = None
    return _answer_hook(blocked)


def handle_model_switched() -> int:
    """PostModelSwitch: remember the new model. Prints nothing (stdout would reach Claude)."""
    try:
        from src.guardrails.model_state import SOURCE_SWITCH, record as remember
        payload = _read_payload()
        remember(_text(payload.get("session_id")), _text(payload.get("to_model")), SOURCE_SWITCH)
    except Exception as exc:
        _log().debug(f"model access: model switch not recorded: {exc}")
    return 0


def handle_agent_model() -> int:
    """PreToolUse for Agent: deny a subagent whose `model` setting names a blocked model."""
    blocked = None
    try:
        payload = _read_payload()
        tool_input = payload.get("tool_input")
        model = _text(tool_input.get("model")) if isinstance(tool_input, dict) else ""
        if model:
            blocked = _check(payload, ModelRequest((model,)),
                             lambda decision: _deny("PreToolUse", message(decision, AGENT_NEXT_STEP)),
                             tool_use_id=_text(payload.get("tool_use_id")) or None,
                             tool_name=_text(payload.get("tool_name")) or "Agent", own_log=True)
    except Exception as exc:
        _log().warning(f"model access: subagent model check failed, allowed: {exc}")
        blocked = None
    return _answer_hook(blocked)


def check_prompt(hook_data: dict, prompt_id: str | None = None) -> Blocked | None:
    """
    UserPromptSubmit: the block when the session's model is blocked, else
    None. The caller prints `.answer` and then calls `.notify()`. The model
    comes from model_state (the hooks' records and the transcript). Never raises.
    """
    try:
        prompt = hook_data.get("prompt")
        if isinstance(prompt, str) and prompt.lstrip().startswith(_NOT_TYPED_BY_THE_USER):
            return None
        from src.guardrails.model_state import current_model
        model = current_model(_text(hook_data.get("session_id")) or None,
                              _text(hook_data.get("transcript_path")) or None)
        return _check(hook_data, ModelRequest(_names(model)),
                      lambda decision: {"decision": "block",
                                        "reason": message(decision, PROMPT_NEXT_STEP)},
                      prompt_id=prompt_id)
    except Exception as exc:
        _log().warning(f"model access: prompt check failed, prompt allowed: {exc}")
        return None


def on_session_start(hook_data: dict) -> str | None:
    """
    SessionStart: remember the starting model when the policy has model rules,
    and return a warning for the user when that model is blocked. The block
    itself happens at the first prompt. Never raises.
    """
    try:
        from src.guardrails import model_state
        from src.guardrails.model_access import has_rules

        model_state.sweep()
        model = _text(hook_data.get("model"))
        workspace_root = os.environ.get(PROJECT_DIR_ENV)
        cwd = _text(hook_data.get("cwd")) or None
        if not model or not has_rules(workspace_root, cwd):
            return None
        model_state.record(_text(hook_data.get("session_id")), model, model_state.SOURCE_STARTUP)
        decision = evaluate(ModelRequest((model,)), workspace_root=workspace_root, cwd=cwd)
        return message(decision, SESSION_NEXT_STEP) if decision is not None else None
    except Exception as exc:
        _log().debug(f"model access: session start check failed: {exc}")
        return None


# src/hook_entry.py's `claude <command>` for each hook in hooks/model-hooks.json.
HANDLERS = {
    "model_switch": handle_model_switch,
    "model_switched": handle_model_switched,
    "agent_model": handle_agent_model,
}
