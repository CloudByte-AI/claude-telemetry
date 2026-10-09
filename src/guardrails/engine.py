"""
The evaluator - normalised call in, platform-neutral verdict out.

    ToolCall -> matchers (detect) -> table (decide) -> Decision

Nothing here knows which platform is calling, so the same operation produces
the same Decision on Claude Code and Cursor. Adapters own the platform-specific
edges on either side.

Several policies can apply to one call: the global profile and the workspace
profiles for the session's folder (src/guardrails/workspaces.py). Each is
decided on its own - each table is its own first-match list - and the
strictest decision wins (deny > ask > allow; on a tie, the global one). That
is exact: combining the files into one first-match list could not be.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from typing import Sequence

from src.guardrails.config import GuardrailsProfile, load_profile
from src.guardrails.decision import (
    ALLOW,
    CRITICAL,
    DENY,
    Decision,
    SCOPE_GLOBAL,
    SCOPE_WORKSPACE,
    SOURCE_DISABLED,
    SOURCE_ERROR,
    strongest,
)
from src.guardrails.matchers.base import MatchResult
from src.guardrails.registry import MatcherRegistry
from src.guardrails.rules import decide
from src.guardrails.toolcall import ToolCall

def _log():
    """The logger, imported on first use - see config._log() for why."""
    from src.common.logging import get_logger
    return get_logger(__name__)


def run_matchers(call: ToolCall) -> list[MatchResult]:
    """
    Run every matcher that can apply to this call's kind.

    A matcher that raises is skipped, not fatal, so one bad matcher cannot take
    out the whole policy layer. Logged at debug only, so a broken matcher does
    not write a log line per tool call.
    """
    # Importing the package triggers @register_matcher on every matcher module.
    import src.guardrails.matchers  # noqa: F401

    results: list[MatchResult] = []
    for matcher in MatcherRegistry.for_kind(call.kind):
        try:
            hit = matcher.match(call)
        except Exception as exc:
            _log().debug(f"matcher {type(matcher).__name__} raised, skipped: {exc}")
            continue
        if hit is not None:
            # Recorded here rather than by each matcher, so every MatchResult
            # carries its provenance without every matcher remembering to.
            hit.facts.setdefault("matcher_id", type(matcher).__name__)
            results.append(hit)
    return results


@dataclass(frozen=True)
class Policy:
    """One profile that applies to a call, and where it came from."""

    profile: GuardrailsProfile
    scope: str = SCOPE_GLOBAL           # SCOPE_GLOBAL | SCOPE_WORKSPACE
    workspace_root: str | None = None   # the workspace's normalised folder


def load_policies(call: ToolCall) -> list[Policy]:
    """
    Every policy that applies to this call: the global profile first, then the
    workspace profiles for the session's folder, nearest first.

    The kill switch turns everything off, workspace files included. Raises
    only when the workspace files cannot be read at all (workspaces.py), so
    the adapters fail closed instead of silently dropping a workspace policy.
    """
    from src.guardrails.workspaces import load_workspace_profiles

    global_profile = load_profile()
    if global_profile.source == "kill_switch":
        return [Policy(global_profile)]
    policies = [Policy(global_profile)]
    for workspace in load_workspace_profiles(call.workspace_root, call.cwd):
        policies.append(Policy(workspace.profile, SCOPE_WORKSPACE, workspace.root))
    return policies


def evaluate(
    call: ToolCall,
    profile: GuardrailsProfile | None = None,
) -> Decision:
    """
    Decide what should happen to this tool call.

    With `profile`, only that profile is checked. Without it, every policy that
    applies (load_policies) is checked and the strictest decision wins.

    Returns a no-opinion Decision when guardrails are off, when no table
    governs this kind of call, or when the call is allowlisted. Raises only
    when the policies cannot be read (see load_policies); the adapters turn
    that into their fail-closed answer.
    """
    started = time.perf_counter()
    policies = [Policy(profile)] if profile is not None else load_policies(call)

    def elapsed() -> float:
        return round((time.perf_counter() - started) * 1000, 3)

    active = [policy for policy in policies if policy.profile.enabled]
    if not active:
        return Decision.no_opinion(source=SOURCE_DISABLED, eval_ms=elapsed())

    # Before any profile, and not something a profile can allow.
    from src.guardrails.selfguard import check as self_protection
    guarded = self_protection(call)
    if guarded is not None:
        return replace(guarded, eval_ms=elapsed())

    matches: list[MatchResult] | None = None
    decisions: list[Decision] = []
    for policy in active:
        current = policy.profile
        # Exact-match allowlist, checked before any work (mirrors the scanner's).
        # It skips this profile's rules only, never another file's.
        if current.is_allowlisted(call.command, call.target_path, call.tool_name):
            continue
        table = current.table_for(call.kind)
        if table is None:
            continue
        if matches is None:
            matches = run_matchers(call)    # the same for every profile, so once
        decisions.append(replace(
            decide(table, call, matches),
            profile_hash=current.fingerprint,
            policy_scope=policy.scope,
            workspace_root=policy.workspace_root,
        ))

    if not decisions:
        return Decision.no_opinion(source=SOURCE_DISABLED, eval_ms=elapsed())
    return replace(combine(decisions), eval_ms=elapsed())


def combine(decisions: list[Decision]) -> Decision:
    """
    The decision that applies when several policies decided one call.

    The strictest action wins; on a tie the first (the global policy) is
    reported. An allow that a rule marked `audit: true` is kept over a plain
    allow, so combining never loses an audit row a policy asked for.
    """
    best = strongest(decisions)
    if best.action == ALLOW and not best.requires_audit:
        audited = next((d for d in decisions if d.requires_audit), None)
        if audited is not None:
            return audited
    return best


def deny_tier_literals(profile: GuardrailsProfile) -> tuple[str, ...]:
    """
    Literal fragments drawn from the profile's `deny` rules.

    Feeds fallback_decision(): if the rule engine itself fails, `deny` rules
    are still enforced by a plain substring test. Regex patterns are excluded
    so the check does nothing that can fail.
    """
    literals: list[str] = []
    for table in profile.tables.values():
        for rule in table.rules:
            if rule.action != DENY:
                continue
            for constraint in rule.constraints:
                if constraint.operator == "regex":
                    continue
                if constraint.field in ("command", "command_name", "subcommand",
                                        "subcommand_path", "target_path", "target",
                                        "mcp_tool", "file_path"):
                    pattern = constraint.pattern.strip()
                    if len(pattern) >= 2:
                        literals.append(pattern)
    # Stable order, no duplicates.
    return tuple(dict.fromkeys(literals))


def quick_deny_check(text: str, literals: Sequence[str]) -> str | None:
    """The literal that matched, or None. Cannot raise; used in an except block."""
    if not text or not literals:
        return None
    lowered = text.lower()
    for literal in literals:
        if literal.lower() in lowered:
            return literal
    return None


def fallback_decision(text: str) -> Decision:
    """
    The verdict when evaluate() itself failed. Never raises. Fails closed:

        guardrails known to be off          no opinion
        text holds a `deny` rule's literal  deny, by the plain substring check
        anything else                       ask (Decision.evaluation_failed)

    `text` is everything the call acts on, joined by the adapter. Not knowing
    whether guardrails are on counts as on.
    """
    try:
        from src.guardrails.workspaces import any_workspace_policies

        profile = load_profile()
        if profile.source == "kill_switch":
            return Decision.no_opinion(source=SOURCE_ERROR)
        # A workspace policy may be on even when the global one is off; the
        # failed call's workspace is unknown here, so any file counts.
        if not profile.enabled and not any_workspace_policies():
            return Decision.no_opinion(source=SOURCE_ERROR)
        hit = quick_deny_check(text, deny_tier_literals(profile)) if profile.enabled else None
    except Exception:
        return Decision.evaluation_failed()
    if hit:
        return Decision(
            action=DENY,
            alert_level=CRITICAL,
            source=SOURCE_ERROR,
            reason=(f"this matches a deny rule ({hit!r}) and the policy engine could not "
                    f"evaluate it, so it is blocked"),
        )
    return Decision.evaluation_failed()
