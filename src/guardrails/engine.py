"""
The evaluator - normalised call in, platform-neutral verdict out.

    ToolCall -> matchers (detect) -> table (decide) -> Decision

Nothing here knows which platform is calling, so the same operation produces
the same Decision on Claude Code and Cursor. Adapters own the platform-specific
edges on either side.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Sequence

from src.guardrails.config import GuardrailsProfile, load_profile
from src.guardrails.decision import (
    CRITICAL,
    DENY,
    Decision,
    SOURCE_DISABLED,
    SOURCE_ERROR,
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


def evaluate(
    call: ToolCall,
    profile: GuardrailsProfile | None = None,
) -> Decision:
    """
    Decide what should happen to this tool call.

    Never raises. Returns a no-opinion Decision when guardrails are off, when
    no table governs this kind of call, or when the call is allowlisted.
    """
    started = time.perf_counter()
    profile = profile if profile is not None else load_profile()

    def elapsed() -> float:
        return round((time.perf_counter() - started) * 1000, 3)

    if not profile.enabled:
        return Decision.no_opinion(source=SOURCE_DISABLED, eval_ms=elapsed())

    # Exact-match allowlist, checked before any work (mirrors the scanner's).
    if profile.is_allowlisted(call.command, call.target_path, call.tool_name):
        return Decision.no_opinion(source=SOURCE_DISABLED, eval_ms=elapsed())

    table = profile.table_for(call.kind)
    if table is None:
        return Decision.no_opinion(source=SOURCE_DISABLED, eval_ms=elapsed())

    matches = run_matchers(call)
    decision = decide(table, call, matches)
    return replace(decision, profile_hash=profile.fingerprint, eval_ms=elapsed())


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
        profile = load_profile()
        if not profile.enabled:
            return Decision.no_opinion(source=SOURCE_ERROR)
        hit = quick_deny_check(text, deny_tier_literals(profile))
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
