"""
Cursor adapter for model access (src/guardrails/model_access.py).

Cursor names the model on every beforeSubmitPrompt payload: `model_id` (the
cleaner form) and `model`. With Auto selected both are "default" and nothing
says which model Cursor will pick, so the request is an automatic choice,
decided by the profile's `auto` setting.

A prompt belongs to no single folder, so every workspace root's policy is
checked and any one blocking blocks. Fails open: a check that cannot run
allows the prompt.
"""

from __future__ import annotations

from src.guardrails.model_access import Blocked, ModelCall, ModelRequest, evaluate, message, record

PLATFORM = "cursor"
# What Cursor sends as the model while Auto chooses it.
AUTO_MODEL_NAMES = frozenset({"default", "auto"})
PROMPT_NEXT_STEP = "Choose an allowed model in the model picker, then send your prompt again."


def _log():
    from src.common.logging import get_logger
    return get_logger(__name__)


def request_for(hook_data: dict) -> ModelRequest:
    """The model this prompt will use, as Cursor reported it."""
    names = []
    for key in ("model_id", "model"):
        value = hook_data.get(key)
        if isinstance(value, str) and value.strip():
            names.append(value.strip())
    if any(name.lower() in AUTO_MODEL_NAMES for name in names):
        return ModelRequest(auto=True)
    return ModelRequest(tuple(dict.fromkeys(names)))


def check_prompt(hook_data: dict, session_id: str | None, prompt_id: str | None,
                 roots: list[str]) -> Blocked | None:
    """
    The block when the model is blocked, else None. The caller prints
    `.answer` (the beforeSubmitPrompt answer) and then calls `.notify()`.
    Never raises.
    """
    try:
        request = request_for(hook_data)
        decision = None
        for root in roots or [None]:
            decision = evaluate(request, workspace_root=root, cwd=root)
            if decision is not None:
                break
        if decision is None:
            return None
        call = ModelCall(
            platform=PLATFORM,
            session_id=session_id,
            prompt_id=prompt_id,
            cwd=roots[0] if roots else None,
            workspace_root=decision.workspace_root or (roots[0] if roots else None),
            model="auto" if request.auto else request.display,
        )
        record(decision, call)
        _log().info(f"model access: blocked {decision.target!r} ({decision.rule_id}, {decision.policy_scope})")
        return Blocked({"continue": False, "user_message": message(decision, PROMPT_NEXT_STEP)},
                       decision, call)
    except Exception as exc:
        _log().warning(f"model access: prompt check failed, prompt allowed: {exc}")
        return None
