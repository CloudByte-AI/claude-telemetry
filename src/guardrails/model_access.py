"""
Model access - which AI models a guardrails profile allows.

A profile may carry a `models:` section:

    models:
      mode: block            # block: the listed models are blocked, every other one is allowed
                             # allow_only: only the listed models are allowed
      block: [grok, kimi]
      allow: [sonnet, haiku]
      auto: allow            # the client chose the model itself and does not say which
                             # (Cursor's Auto): allow | block, in either mode
      unknown: allow         # the model could not be determined: allow | block

Matching is by whole words. A model name's words are its runs of letters and
digits, in lower case, and an entry matches when its words appear next to each
other, in order, among them:

    grok              grok-4.7-high, cursor-grok-4.6-medium
    opus              every Opus model, whatever the provider prefix or date
    claude-opus-4-6   claude-opus-4-6, us.anthropic.claude-opus-4-6-v1:0,
                      claude-opus-4-6[1m]; not claude-opus-4-5
    gpt-5             gpt-5, gpt-5.6-sol-medium (version numbers are words too)

Every enabled profile that applies (the global one and the workspace ones, see
workspaces.py) is checked, and any one of them blocking blocks: a workspace can
only add restrictions. Kept free of any platform: adapters say what the client
reported (ModelRequest) and render the Decision.

A broken section never blocks anything: an unreadable `mode` or an allow list
with nothing usable in it drops the section and is reported, so an editor
refuses to save it.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from src.guardrails.decision import (
    DENY,
    MESSAGE_PREFIX,
    SCOPE_GLOBAL,
    SCOPE_WORKSPACE,
    SOURCE_POLICY,
    WARN,
    Decision,
)

MODE_BLOCK = "block"
MODE_ALLOW_ONLY = "allow_only"
MODES = (MODE_BLOCK, MODE_ALLOW_ONLY)

ALLOW = "allow"
BLOCK = "block"
CHOICES = (ALLOW, BLOCK)

SECTION_KEYS = ("mode", "block", "allow", "auto", "unknown")

# The audit row's operation, and the rule ids that say why a model was blocked.
OPERATION = "model.blocked"
RULE_BLOCK_LIST = "models.block"
RULE_NOT_ALLOWED = "models.allow_only"
RULE_AUTO = "models.auto"
RULE_UNKNOWN = "models.unknown"

_WORD = re.compile(r"[a-z0-9]+")


def _log():
    """The logger, imported on first use - see config._log() for why."""
    from src.common.logging import get_logger
    return get_logger(__name__)


def words(name: str | None) -> tuple[str, ...]:
    """A model name or list entry as lower-case words: `Grok-4.7-High` -> (grok, 4, 7, high)."""
    return tuple(_WORD.findall((name or "").lower()))


def _contains(haystack: tuple[str, ...], needle: tuple[str, ...]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(haystack[start:start + width] == needle for start in range(len(haystack) - width + 1))


@dataclass(frozen=True)
class ModelRules:
    """One profile's `models:` section, parsed."""

    mode: str = MODE_BLOCK
    block: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    auto: str = ALLOW
    unknown: str = ALLOW

    def listed(self, entries: tuple[str, ...], name: str) -> str | None:
        """The first entry that matches this model name, or None."""
        name_words = words(name)
        return next((entry for entry in entries if _contains(name_words, words(entry))), None)


@dataclass(frozen=True)
class ModelRequest:
    """
    What the client reported about the model a prompt, switch or subagent will use.

    `names` holds every name the client gave for the one model, most specific
    first (a resolved id, then the alias the user typed). Empty means the model
    could not be determined. `auto` means the client chooses the model itself
    and does not say which.
    """

    names: tuple[str, ...] = ()
    auto: bool = False

    @property
    def display(self) -> str:
        return self.names[0] if self.names else ""


def blocking_rule(rules: ModelRules, request: ModelRequest) -> str | None:
    """The rule id that blocks this request under these rules, or None when it is allowed."""
    if request.auto:
        return RULE_AUTO if rules.auto == BLOCK else None
    names = tuple(name for name in request.names if words(name))
    if not names:
        return RULE_UNKNOWN if rules.unknown == BLOCK else None
    if rules.mode == MODE_ALLOW_ONLY:
        allowed = any(rules.listed(rules.allow, name) for name in names)
        return None if allowed else RULE_NOT_ALLOWED
    blocked = any(rules.listed(rules.block, name) for name in names)
    return RULE_BLOCK_LIST if blocked else None


def summary_for(request: ModelRequest) -> str:
    """What was blocked, in a few words, for a notification: `model grok-4.7-high`."""
    if request.auto:
        return "automatic model selection"
    return f"model {request.display}" if request.display else "a model that could not be determined"


def reason_for(rules: ModelRules, request: ModelRequest, rule_id: str) -> str:
    """Why the model is blocked, in plain words, without the message prefix."""
    if rule_id == RULE_AUTO:
        return "automatic model selection is not allowed by your organization's policy"
    if rule_id == RULE_UNKNOWN:
        return ("the model this session uses could not be determined, and your organization's "
                "policy allows only known models")
    reason = f"the model {request.display} is not allowed by your organization's policy"
    if rule_id == RULE_NOT_ALLOWED and rules.allow:
        reason += f" (allowed: {', '.join(rules.allow)})"
    return reason


# -- Reading the section --------------------------------------------------------

def _describe(value: Any) -> str:
    from src.guardrails.config import _describe as describe
    return describe(value)


def _read_entries(section: dict, key: str, errors: list[str]) -> tuple[str, ...]:
    value = section.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
        errors.append(f"models.{key} must be a list - write it as `{key}: [\"{section.get(key)}\"]`; "
                      f"read as a one-item list")
    if not isinstance(value, list):
        errors.append(f"models.{key} must be a list of model names, not {_describe(value)} - ignored")
        return ()
    entries: list[str] = []
    for index, item in enumerate(value):
        if isinstance(item, (dict, list)) or item is None or isinstance(item, bool):
            errors.append(f"models.{key}[{index}] must be a model name, not {_describe(item)} - ignored")
            continue
        text = str(item).strip()
        if not words(text):
            errors.append(f"models.{key}[{index}] '{text}' has no letters or digits to match - ignored")
            continue
        entries.append(text)
    return tuple(dict.fromkeys(entries))


def _read_choice(section: dict, key: str, errors: list[str]) -> str:
    value = section.get(key, ALLOW)
    if isinstance(value, str) and value in CHOICES:
        return value
    errors.append(f"models.{key} must be allow or block, not {_describe(value)} - read as allow")
    return ALLOW


def read_models(value: Any, errors: list[str]) -> ModelRules | None:
    """
    The `models:` section as ModelRules, or None when the profile has none or it
    cannot be used. Problems are appended to `errors`. Never raises.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        errors.append(f"models must be a mapping like `{{mode: block, block: [grok]}}`, not "
                      f"{_describe(value)} - no model is blocked")
        return None
    for key in value:
        if key not in SECTION_KEYS:
            errors.append(f"models: unknown setting '{key}' (known: {', '.join(SECTION_KEYS)})")
    mode = value.get("mode", MODE_BLOCK)
    if not isinstance(mode, str) or mode not in MODES:
        errors.append(f"models.mode must be one of {', '.join(MODES)}, not {_describe(mode)} "
                      f"- no model is blocked")
        return None
    rules = ModelRules(
        mode=mode,
        block=_read_entries(value, "block", errors),
        allow=_read_entries(value, "allow", errors),
        auto=_read_choice(value, "auto", errors),
        unknown=_read_choice(value, "unknown", errors),
    )
    if mode == MODE_ALLOW_ONLY and not rules.allow:
        # Read as written it would block every model; a mistake that large must
        # never reach users unreviewed.
        errors.append("models.mode is allow_only but models.allow names no model - "
                      "no model is blocked until it does")
        return None
    return rules


# -- Deciding -------------------------------------------------------------------

@dataclass(frozen=True)
class ModelCall:
    """
    The audit spool's view of a model check (spool.append_event reads these
    fields). The subject is the model, so the stored hash is of its name.
    """

    platform: str
    session_id: str | None = None
    prompt_id: str | None = None
    tool_use_id: str | None = None
    tool_name: str = "model"
    cwd: str | None = None
    workspace_root: str | None = None
    model: str = ""

    @property
    def subject_hash(self) -> str:
        import hashlib
        return hashlib.sha256(f"model:{self.model}".encode("utf-8")).hexdigest()


def _policies(workspace_root: str | None, cwd: str | None) -> list[tuple[ModelRules, str, str | None, str | None]]:
    """
    (rules, scope, workspace folder, fingerprint) for every enabled profile with
    a `models:` section, global first. Honours the kill switch. Raises only if
    the workspace files cannot be read at all.
    """
    from src.guardrails.config import load_profile
    from src.guardrails.workspaces import load_workspace_profiles

    global_profile = load_profile()
    if global_profile.source == "kill_switch":
        return []
    found = []
    if global_profile.enabled and global_profile.models is not None:
        found.append((global_profile.models, SCOPE_GLOBAL, None, global_profile.fingerprint))
    for workspace in load_workspace_profiles(workspace_root, cwd):
        profile = workspace.profile
        if profile.enabled and profile.models is not None:
            found.append((profile.models, SCOPE_WORKSPACE, workspace.root, profile.fingerprint))
    return found


def has_rules(workspace_root: str | None, cwd: str | None) -> bool:
    """Whether any enabled profile that applies has a `models:` section. Never raises."""
    try:
        return bool(_policies(workspace_root, cwd))
    except Exception as exc:
        _log().warning(f"model access: policies could not be read: {exc}")
        return False


def evaluate(request: ModelRequest, workspace_root: str | None = None,
             cwd: str | None = None) -> Decision | None:
    """
    The deny Decision when any applicable profile blocks this model, else None.

    Never raises. A failure to read the policies is logged and allows the
    model: a broken plugin must never stop the user from working, and the
    prompt-time check runs again on the next prompt.
    """
    started = time.perf_counter()
    try:
        for rules, scope, folder, fingerprint in _policies(workspace_root, cwd):
            rule_id = blocking_rule(rules, request)
            if rule_id is None:
                continue
            return Decision(
                action=DENY,
                reason=reason_for(rules, request, rule_id),
                alert_level=WARN,
                operation=OPERATION,
                rule_id=rule_id,
                profile_hash=fingerprint,
                source=SOURCE_POLICY,
                policy_scope=scope,
                workspace_root=folder,
                target="auto" if request.auto else (request.display or None),
                evidence=", ".join(request.names) or None,
                summary=summary_for(request),
                # The whole check, policy files included, as for a tool call.
                eval_ms=round((time.perf_counter() - started) * 1000, 3),
            )
    except Exception as exc:
        _log().warning(f"model access: check failed, the model is allowed: {exc}")
    return None


def message(decision: Decision, next_step: str) -> str:
    """What the person reads: `CloudByte guardrails: <reason>. <next step>`."""
    text = f"{MESSAGE_PREFIX}: {decision.reason_text}."
    return f"{text} {next_step}" if next_step else text


def record(decision: Decision, call: ModelCall) -> None:
    """Spool the audit event for a blocked model. Never raises."""
    try:
        from src.guardrails import spool
        spool.append_event(decision, call)
    except Exception as exc:
        _log().debug(f"model access: audit event not written: {exc}")


def notify(decision: Decision, call: ModelCall) -> None:
    """
    The desktop notification for a blocked model, through the guardrails
    notifications (same settings, icons and repeat window). Call it once the
    hook's answer is on stdout; never raises, never waits.
    """
    try:
        import sys
        sys.stdout.flush()
        from src.guardrails.notify import notify_decision
        notify_decision(decision, call)
    except Exception as exc:
        _log().debug(f"model access: notification failed (non-fatal): {exc}")


@dataclass(frozen=True)
class Blocked:
    """A blocked model: the hook's answer, and the decision and call to notify about."""

    answer: dict
    decision: Decision
    call: ModelCall

    def notify(self) -> None:
        notify(self.decision, self.call)
