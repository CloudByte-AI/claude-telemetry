"""
The verdict - platform-neutral.

A Decision says what should happen and why. It carries no platform vocabulary:
rendering it into Claude Code's `hookSpecificOutput` or Cursor's `permission`
object is the adapter's job, and is the only place those shapes appear.

Only three actions exist, deliberately: every extra action is one more thing
every adapter has to render correctly.
"""

from __future__ import annotations

from dataclasses import dataclass

# ── Actions ───────────────────────────────────────────────────────────────────

ALLOW = "allow"
ASK = "ask"
DENY = "deny"
ACTIONS = (ALLOW, ASK, DENY)

# Ordering used when a call needs the strongest of several verdicts, mirroring
# Claude Code's own multi-hook precedence (deny > ask > allow).
_SEVERITY = {ALLOW: 0, ASK: 1, DENY: 2}

# ── Alert levels ──────────────────────────────────────────────────────────────

CRITICAL = "critical"
WARN = "warn"
INFO = "info"
ALERT_LEVELS = (CRITICAL, WARN, INFO)

# ── Where a decision came from ────────────────────────────────────────────────

SOURCE_MATCHER = "matcher"          # a matcher fired and a rule scoped to it won
SOURCE_POLICY = "policy"            # a rule matched without any matcher involved
SOURCE_FALLTHROUGH = "fallthrough"  # the table's final catch-all row
SOURCE_DISABLED = "disabled"        # guardrails off, or no profile on disk
SOURCE_ERROR = "error"              # the evaluator failed

# ── Which policy file decided ─────────────────────────────────────────────────

SCOPE_GLOBAL = "global"             # global_profile.yaml - every session on the machine
SCOPE_WORKSPACE = "workspace"       # a workspaces/ file - sessions in that workspace
SCOPE_BUILTIN = "builtin"           # the plugin's own non-overridable checks

# ── Messages ──────────────────────────────────────────────────────────────────

# Every message a person or the agent reads starts with this. The platform's own
# wording ("Hook PreToolUse:Bash requires confirmation") does not say which
# plugin asked, and several plugins can register the same hook.
MESSAGE_PREFIX = "CloudByte guardrails"

# Used only when a rule gives no reason. Plain words, never an operation name.
_DEFAULT_REASON = {
    ASK: "This action needs your approval",
    DENY: "This action is blocked by policy",
}

# The reason given when the check itself could not run. The agent reads it on
# Cursor, so it never says how to switch guardrails off.
EVALUATION_FAILED_REASON = (
    "the check could not run because of an internal error - details are in the CloudByte log"
)


@dataclass(frozen=True)
class Decision:
    """
    What to do about one tool call, and the provenance to explain it later.

    Every field except `action` exists so an audit row can answer "why did THIS
    happen?" without re-running anything: which matcher detected it, which rule
    won, what text tripped it.
    """

    action: str = ALLOW
    reason: str = ""
    alert_level: str = INFO

    # provenance
    operation: str | None = None        # taxonomy name, e.g. fs.delete.recursive
    matcher_id: str | None = None       # the class that detected it
    rule_id: str | None = None          # e.g. "bash#2"
    rule_rank: int | None = None        # position in evaluation order
    # Which policy the rule belongs to (GuardrailsProfile.fingerprint). Rules
    # without an `id` are named by position, so the hash says which profile
    # "bash#9" meant.
    profile_hash: str | None = None
    source: str = SOURCE_DISABLED
    # Which policy file the deciding rule came from (SCOPE_*), and for a
    # workspace policy, that workspace's root folder. None for no opinion.
    policy_scope: str | None = None
    workspace_root: str | None = None

    # evidence
    target: str | None = None           # resolved path / host / mcp tool
    evidence: str | None = None         # the exact fragment that matched
    command_name: str | None = None     # tool family: aws | git | rm

    # bookkeeping
    requires_audit: bool = False
    eval_ms: float = 0.0
    # False when the verdict is `ask` but the platform shows no approval prompt
    # for it (every Cursor hook). The action stays `ask` and the audit row
    # records `user_decision = not_prompted`.
    prompted: bool = True

    @property
    def is_opinion(self) -> bool:
        """
        Whether this decision should be expressed to the platform at all.

        Only `ask` and `deny` are. An `allow` is deliberately NOT emitted: on
        Claude Code, `permissionDecision: "allow"` SKIPS the user's own
        permission prompt, and installing a guardrail must never remove one.
        An `allow` row emits `{}` and the user's permission settings decide.
        `should_audit` is independent of this.
        """
        return self.action in (ASK, DENY)

    @property
    def should_audit(self) -> bool:
        """
        Whether this decision earns a row in TOOL_GUARDRAIL_EVENT.

        Every `ask` and `deny` does. An `allow` does only when its rule says
        `audit: true`, because the TOOL table already records every tool call.
        """
        if self.action in (ASK, DENY):
            return True
        return self.requires_audit

    @property
    def reason_text(self) -> str:
        """The rule's reason, or the plain default for its action when the rule gives none."""
        return self.reason or _DEFAULT_REASON.get(self.action, "")

    @property
    def detail(self) -> str:
        """
        The reason and what it applies to, in plain words:
        `Recursive delete - review the target before approving (C:/repo/build)`.

        The operation name is left out on purpose: it means nothing to the
        person reading the prompt, and the audit row already records it.
        """
        text = self.reason_text
        if self.target:
            return f"{text} ({self.target})" if text else f"({self.target})"
        return text

    @property
    def message(self) -> str:
        """What the user (ask) or the agent (deny) reads: `CloudByte guardrails: <detail>`."""
        detail = self.detail
        return f"{MESSAGE_PREFIX}: {detail}" if detail else MESSAGE_PREFIX

    @classmethod
    def no_opinion(cls, source: str = SOURCE_DISABLED, eval_ms: float = 0.0) -> "Decision":
        """Guardrails have nothing to say - the normal permission flow applies."""
        return cls(action=ALLOW, source=source, eval_ms=eval_ms)

    @classmethod
    def evaluation_failed(cls) -> "Decision":
        """
        The verdict when the check itself could not run: an ask, audited and
        notified like any other. Failing closed to ask rather than deny keeps a
        bug in this plugin from stopping the user's work, while never letting
        an unchecked call through unseen.
        """
        return cls(action=ASK, reason=EVALUATION_FAILED_REASON, alert_level=WARN,
                   source=SOURCE_ERROR)


def strongest(decisions: list[Decision]) -> Decision | None:
    """The most restrictive of several decisions - deny beats ask beats allow."""
    if not decisions:
        return None
    return max(decisions, key=lambda d: _SEVERITY.get(d.action, 0))
