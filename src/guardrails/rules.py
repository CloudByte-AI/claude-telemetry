"""
The rule engine - ordered tables, first match wins.

Five operators, positive only: equals, starts_with, ends_with, contains, regex.
The FIELD's type decides the semantics: on `subcommand_path` (a token list)
`contains` means a contiguous token run, so `{contains: "s3 rm"}` finds
`aws --profile p s3 rm x` regardless of where the flag sits.

Negation is deliberately absent, because ordering already is negation, and
positive constraints keep rules analysable:

    - { command_name: aws, subcommand_path: {starts_with: "s3 ls"}, decision: allow }
    - { command_name: aws, decision: ask }

Evaluation is PER FRAME. A frame pairs the call with at most one matcher hit
and at most one parsed command, and a rule matches when ANY frame satisfies all
of its constraints. That stops `git status && aws s3 rm x` from satisfying a
rule through two different commands at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Sequence

from src.common import safe_regex
from src.guardrails.decision import (
    ACTIONS,
    ALERT_LEVELS,
    ALLOW,
    Decision,
    INFO,
    SOURCE_FALLTHROUGH,
    SOURCE_MATCHER,
    SOURCE_POLICY,
)
from src.guardrails.matchers.base import MatchResult
from src.guardrails.parser import Command
from src.guardrails.toolcall import ToolCall

OPERATORS = ("equals", "starts_with", "ends_with", "contains", "regex")

# Longest text any regex is applied to. `regex` conditions run on RE2 through
# src.common.safe_regex, not on Python's `re`. RE2 never backtracks, so match
# time is linear in the input and this cap bounds the time any one match can
# take, whatever the pattern.
MAX_REGEX_INPUT = 4096

# Keys that are part of the rule's OUTCOME rather than its conditions. Nothing
# presentational belongs here: an editor keeps its own data in its own
# top-level block in the profile, keyed by `id`.
_OUTCOME_KEYS = frozenset({"decision", "alert", "reason", "audit", "id"})

# Fields resolved from the parsed command, so they are matched per command
# rather than across the whole call.
_COMMAND_FIELDS = frozenset({
    "command_name", "subcommand", "subcommand_path", "flags",
    "has_force_flag", "has_recursive_flag", "is_destructive", "is_network_call",
})

# Fields resolved from the call itself.
_CALL_FIELDS = frozenset({
    "operation", "target", "target_path", "evidence", "command",
    "tool", "tool_name", "kind", "extension", "file_path", "file_paths",
    "mcp_server", "mcp_tool", "permission_mode", "platform",
    "host", "hosts", "url", "urls", "content", "agent_type", "is_subagent",
    "dialect",
})

KNOWN_FIELDS = _COMMAND_FIELDS | _CALL_FIELDS


def known_fields() -> frozenset[str]:
    """
    Every field a rule may name: the built-ins plus every matcher's declared
    FACTS.

    Facts are validated so a typo like `with_leese: true` is a load-time error
    rather than a rule that silently never matches.
    """
    global _KNOWN_FIELDS_CACHE
    if _KNOWN_FIELDS_CACHE is None:
        facts: set[str] = set()
        try:
            import src.guardrails.matchers  # noqa: F401
            from src.guardrails.registry import MatcherRegistry

            for matcher_cls in MatcherRegistry.all_matchers():
                facts.update(getattr(matcher_cls, "FACTS", ()) or ())
        except Exception:
            # Validation must never be the thing that breaks loading a profile.
            facts = set()
        _KNOWN_FIELDS_CACHE = frozenset(KNOWN_FIELDS | facts)
    return _KNOWN_FIELDS_CACHE


_KNOWN_FIELDS_CACHE: frozenset[str] | None = None
_KNOWN_OPERATIONS_CACHE: frozenset[str] | None = None


def known_operations() -> frozenset[str]:
    """
    Every taxonomy name a rule may reference - implemented AND reserved.

    Reserved names are deliberately valid: a rule naming one is simply inert
    until its matcher lands.
    """
    global _KNOWN_OPERATIONS_CACHE
    if _KNOWN_OPERATIONS_CACHE is None:
        try:
            import src.guardrails.matchers  # noqa: F401
            from src.guardrails.registry import MatcherRegistry

            _KNOWN_OPERATIONS_CACHE = frozenset(MatcherRegistry.all_operations())
        except Exception:
            # Validation must never be the thing that breaks loading a profile.
            _KNOWN_OPERATIONS_CACHE = frozenset()
    return _KNOWN_OPERATIONS_CACHE

# Fields whose value is a list of tokens rather than a string. `contains` means
# a contiguous run for ordered ones and membership for unordered ones.
_TOKEN_LIST_FIELDS = frozenset({"subcommand_path"})
_COLLECTION_FIELDS = frozenset({"flags", "hosts", "host", "urls", "url", "file_paths"})
_BOOLEAN_FIELDS = frozenset({
    "has_force_flag", "has_recursive_flag", "is_destructive",
    "is_network_call", "is_subagent",
})


class ProfileError(ValueError):
    """A structural problem with a profile - reported, never raised at runtime."""


# ── Frames ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Frame:
    """One (call, matcher hit, command) combination a rule may match against."""

    call: ToolCall
    match: MatchResult | None = None
    command: Command | None = None


def build_frames(call: ToolCall, matches: Sequence[MatchResult]) -> list[Frame]:
    """
    Every combination a rule could legitimately match.

    One frame per matcher hit (paired with the command that hit, so
    `{operation: ..., has_force_flag: true}` reads the right command), one per
    parsed command (for pure `command_name` rules with no matcher involved),
    and one bare frame so call-only rules and the fall-through still evaluate.
    """
    frames: list[Frame] = []
    by_name = {c.command_name: c for c in call.commands}

    for match in matches:
        command = by_name.get(match.command_name) if match.command_name else None
        frames.append(Frame(call=call, match=match, command=command))

    for command in call.commands:
        frames.append(Frame(call=call, command=command))

    frames.append(Frame(call=call))
    return frames


def resolve_field(name: str, frame: Frame) -> Any:
    """
    Read one field from a frame.

    `target_path` prefers the matcher's own resolved target over the call-level
    heuristic, which just takes the first operand that looks like a path.
    """
    call, match, command = frame.call, frame.match, frame.command

    if name == "operation":
        return match.operation if match else None
    if name in ("target", "target_path"):
        if match and match.target:
            return match.target
        return call.target_path
    if name == "evidence":
        return match.evidence if match else None

    if name == "command_name":
        if command:
            return command.command_name
        return match.command_name if match else None
    if name == "subcommand":
        return command.subcommand if command else None
    if name == "subcommand_path":
        return command.subcommand_path if command else ()
    if name == "flags":
        return tuple(command.flags) if command else ()

    if name in _BOOLEAN_FIELDS:
        if name == "is_subagent":
            return call.is_subagent
        if command is not None:
            return getattr(command, name, False)
        return getattr(call, name, False)

    if name == "command":
        return call.command
    if name in ("tool", "tool_name"):
        return call.tool_name
    if name == "kind":
        return call.kind
    if name == "extension":
        return call.extension
    if name == "file_path":
        return call.file_paths[0] if call.file_paths else None
    if name == "file_paths":
        return call.file_paths
    if name in ("host", "hosts"):
        return call.hosts
    if name in ("url", "urls"):
        return call.urls
    if name == "mcp_server":
        return call.mcp_server
    if name == "mcp_tool":
        return call.mcp_tool
    if name == "permission_mode":
        return call.permission_mode
    if name == "platform":
        return call.platform
    if name == "agent_type":
        return call.agent_type
    if name == "content":
        return call.content
    if name == "dialect":
        return call.dialect

    # Anything else is a matcher fact. Only names a matcher declared in FACTS
    # reach here - parse_rule rejects the rest at load.
    if match is not None:
        return match.facts.get(name)
    return None


# ── Operators ─────────────────────────────────────────────────────────────────


def _token_run(tokens: Sequence[str], pattern: str) -> bool:
    """`contains` on an ordered token list: a contiguous run."""
    want = pattern.split()
    if not want or len(want) > len(tokens):
        return False
    return any(
        list(tokens[i:i + len(want)]) == want
        for i in range(len(tokens) - len(want) + 1)
    )


def apply_operator(
    operator: str,
    pattern: str,
    value: Any,
    field_name: str,
    compiled: safe_regex.Pattern | None = None,
) -> bool:
    """
    Evaluate one operator against one field value.

    `compiled` is the regex compiled when the profile was loaded; when omitted
    the pattern is compiled (and cached) on demand.

    Never raises: a pattern that cannot be compiled simply does not match.
    """
    if value is None:
        return False

    try:
        if operator == "regex" and compiled is None:
            compiled = safe_regex.compile(pattern)

        # ── ordered token list: subcommand_path ───────────────────────────
        if field_name in _TOKEN_LIST_FIELDS:
            tokens = tuple(value)
            want = pattern.split()
            if operator == "equals":
                return list(tokens) == want
            if operator == "starts_with":
                return list(tokens[:len(want)]) == want
            if operator == "ends_with":
                return list(tokens[-len(want):]) == want if want else False
            if operator == "contains":
                return _token_run(tokens, pattern)
            if operator == "regex":
                return compiled.search(" ".join(tokens)[:MAX_REGEX_INPUT])
            return False

        # ── unordered collection: flags, hosts, urls, file_paths ──────────
        if field_name in _COLLECTION_FIELDS:
            items = [str(v) for v in (value or ())]
            if operator == "equals":
                return pattern in items
            if operator == "starts_with":
                return any(i.startswith(pattern) for i in items)
            if operator == "ends_with":
                return any(i.endswith(pattern) for i in items)
            if operator == "contains":
                return any(pattern in i for i in items)
            if operator == "regex":
                return any(compiled.search(i[:MAX_REGEX_INPUT]) for i in items)
            return False

        # ── plain string ──────────────────────────────────────────────────
        text = str(value)
        if operator == "equals":
            return text == pattern
        if operator == "starts_with":
            return text.startswith(pattern)
        if operator == "ends_with":
            return text.endswith(pattern)
        if operator == "contains":
            return pattern in text
        if operator == "regex":
            return compiled.search(text[:MAX_REGEX_INPUT])
    except Exception:
        return False
    return False


# ── Rules ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Constraint:
    field: str
    operator: str
    pattern: str
    # A `regex` constraint's pattern, compiled once when the profile is loaded
    # rather than on every evaluation. Not part of equality: two constraints
    # with the same pattern are the same constraint.
    compiled: safe_regex.Pattern | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Rule:
    """One row of a decision table."""

    rule_id: str
    rank: int
    action: str = ALLOW
    alert: str = INFO
    reason: str = ""
    audit: bool = False
    constraints: tuple[Constraint, ...] = ()
    booleans: tuple[tuple[str, bool], ...] = ()

    @property
    def is_fallthrough(self) -> bool:
        """A row with no conditions at all - the table's explicit default."""
        return not self.constraints and not self.booleans

    def matches(self, frames: Iterable[Frame]) -> Frame | None:
        """The first frame satisfying every constraint, or None."""
        for frame in frames:
            if self._matches_frame(frame):
                return frame
        return None

    def _matches_frame(self, frame: Frame) -> bool:
        for name, expected in self.booleans:
            if bool(resolve_field(name, frame)) is not expected:
                return False
        for constraint in self.constraints:
            value = resolve_field(constraint.field, frame)
            if not apply_operator(constraint.operator, constraint.pattern, value,
                                  constraint.field, constraint.compiled):
                return False
        return True


@dataclass
class Table:
    """An ordered list of rules for one surface (bash, file, mcp, web)."""

    name: str
    rules: list[Rule] = field(default_factory=list)

    def evaluate(self, frames: list[Frame]) -> tuple[Rule, Frame] | None:
        """First match wins, literally top to bottom."""
        for rule in self.rules:
            frame = rule.matches(frames)
            if frame is not None:
                return rule, frame
        return None


# ── Rule ids ──────────────────────────────────────────────────────────────────
#
# Every audit row names the rule that decided it. An `id:` names a rule
# permanently; a rule without one is named by its position (`bash#3`), which
# changes as soon as a rule is added above it. Every shipped preset rule carries
# a `cb.` id, and a shipped id is never renamed or reused. A position name
# contains `#`, which an id cannot, so the two can never collide.

RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
BUILTIN_ID_PREFIX = "cb."


def _rule_id(raw: dict, table_name: str, rank: int, errors: list[str]) -> str:
    """The row's `id:` when it is valid, else its position name. Never drops the rule."""
    position = f"{table_name}#{rank}"
    value = raw.get("id")
    if value is None or value == "":
        return position
    text = str(value).strip()
    if RULE_ID_PATTERN.match(text):
        return text
    errors.append(
        f"{position}: id '{value}' is not valid - use lowercase letters, digits, '.', '-' or "
        f"'_', starting with a letter, at most 64 characters. The rule still applies, "
        f"named {position}"
    )
    return position


def ensure_unique_ids(tables: dict[str, "Table"], errors: list[str]) -> None:
    """
    Make every rule name in a profile unique, across all of its tables.

    On a clash the later rule keeps working under its position name, because
    an id problem must never remove protection, and the clash is reported so
    validate_profile() refuses the save.
    """
    first_seen: dict[str, str] = {}
    for table in tables.values():
        for index, rule in enumerate(table.rules):
            if "#" in rule.rule_id:
                continue
            position = f"{table.name}#{rule.rank}"
            if rule.rule_id in first_seen:
                errors.append(
                    f"{position}: id '{rule.rule_id}' is already used by "
                    f"{first_seen[rule.rule_id]} - ids must be unique in a profile. The rule "
                    f"still applies, named {position}"
                )
                table.rules[index] = replace(rule, rule_id=position)
            else:
                first_seen[rule.rule_id] = position


# ── Parsing a table from profile YAML ─────────────────────────────────────────


def parse_rule(raw: dict, table_name: str, rank: int, errors: list[str]) -> Rule | None:
    """
    Build one Rule from one YAML row.

    An unusable row is dropped with an error recorded, never raised: one bad
    row must not take the rest of the profile with it.
    """
    if not isinstance(raw, dict):
        errors.append(f"{table_name}#{rank}: rule is not a mapping")
        return None

    action = str(raw.get("decision", ALLOW)).lower()
    if action not in ACTIONS:
        errors.append(f"{table_name}#{rank}: unknown decision '{action}'")
        return None

    alert = str(raw.get("alert", INFO)).lower()
    if alert not in ALERT_LEVELS:
        errors.append(f"{table_name}#{rank}: unknown alert '{alert}', using info")
        alert = INFO

    constraints: list[Constraint] = []
    booleans: list[tuple[str, bool]] = []
    fields = known_fields()

    for key, value in raw.items():
        if key in _OUTCOME_KEYS:
            continue
        if key not in fields:
            errors.append(f"{table_name}#{rank}: unknown field '{key}'")
            return None

        if isinstance(value, bool):
            # A boolean built-in, or a matcher fact that happens to be boolean
            # (`with_lease: true`). Both compare the same way.
            booleans.append((key, value))
            continue

        if isinstance(value, dict):
            if len(value) != 1:
                errors.append(
                    f"{table_name}#{rank}: field '{key}' needs exactly one operator, got {list(value)}"
                )
                return None
            operator, pattern = next(iter(value.items()))
            operator = str(operator).lower()
            if operator not in OPERATORS:
                errors.append(f"{table_name}#{rank}: unknown operator '{operator}' on '{key}'")
                return None
            pattern = str(pattern)
            compiled = None
            if operator == "regex":
                # The same compile an editor's pre-save check runs, so a pattern
                # that passes there cannot fail here. The engine is imported
                # only now, when a profile actually contains a regex.
                try:
                    compiled = safe_regex.compile(pattern)
                except safe_regex.RegexError as exc:
                    errors.append(f"{table_name}#{rank}: invalid regex on '{key}': {exc}")
                    return None
            constraints.append(Constraint(key, operator, pattern, compiled))
            continue

        # A bare scalar is shorthand for `equals`.
        constraints.append(Constraint(key, "equals", str(value)))

    # An exact value that can never match is a silent no-op, so exact
    # `operation` and `kind` values are checked against the vocabulary. Only
    # `equals` is checked: a pattern operator (`{starts_with: "fs."}`) is a
    # legitimate prefix match over names that may not exist yet.
    for constraint in constraints:
        if constraint.operator != "equals":
            continue
        if constraint.field == "operation":
            valid = known_operations()
            if valid and constraint.pattern not in valid:
                errors.append(
                    f"{table_name}#{rank}: unknown operation '{constraint.pattern}' - "
                    f"it is not in the taxonomy, so this rule could never match"
                )
                return None
        elif constraint.field == "kind":
            from src.guardrails.toolcall import ALL_KINDS
            if constraint.pattern not in ALL_KINDS:
                errors.append(
                    f"{table_name}#{rank}: unknown kind '{constraint.pattern}' "
                    f"(known: {', '.join(sorted(ALL_KINDS))})"
                )
                return None

    return Rule(
        rule_id=_rule_id(raw, table_name, rank, errors),
        rank=rank,
        action=action,
        alert=alert,
        reason=str(raw.get("reason", "")),
        audit=bool(raw.get("audit", False)),
        constraints=tuple(constraints),
        booleans=tuple(booleans),
    )


def parse_table(name: str, rows: Any, errors: list[str]) -> Table:
    """
    Build one table, validating that it ends with an explicit fall-through.

    When it does not, an implicit `allow` fall-through is appended and the
    omission is reported, rather than discarding the author's rules, which
    would remove protection to punish a typo.
    """
    table = Table(name=name)
    if not isinstance(rows, list):
        if rows is not None:
            errors.append(f"{name}: table must be a list of rules")
        rows = []

    for rank, raw in enumerate(rows):
        rule = parse_rule(raw, name, rank, errors)
        if rule is not None:
            table.rules.append(rule)

    if not table.rules or not table.rules[-1].is_fallthrough:
        errors.append(
            f"{name}: table does not end with a fall-through row "
            f"(a row with no conditions). An implicit 'allow' was appended."
        )
        table.rules.append(Rule(
            rule_id=f"{name}#implicit-default",
            rank=len(table.rules),
            action=ALLOW,
            alert=INFO,
            reason="Implicit default - no rule matched",
        ))

    return table


def decide(
    table: Table,
    call: ToolCall,
    matches: Sequence[MatchResult],
) -> Decision:
    """
    Run one table against one call and return the verdict.

    `source` records HOW the decision was reached: a matcher hit, a plain
    policy rule, or the table's default.
    """
    frames = build_frames(call, matches)
    result = table.evaluate(frames)
    if result is None:
        return Decision.no_opinion(source=SOURCE_FALLTHROUGH)

    rule, frame = result
    match = frame.match

    if rule.is_fallthrough:
        source = SOURCE_FALLTHROUGH
    elif match is not None and any(c.field == "operation" for c in rule.constraints):
        source = SOURCE_MATCHER
    else:
        source = SOURCE_POLICY

    command = frame.command
    return Decision(
        action=rule.action,
        reason=rule.reason,
        alert_level=rule.alert,
        operation=match.operation if match else None,
        matcher_id=match.facts.get("matcher_id") if match else None,
        rule_id=rule.rule_id,
        rule_rank=rule.rank,
        source=source,
        target=(match.target if match and match.target else call.target_path),
        evidence=(match.evidence if match else (command.raw if command else call.command))[:256] or None,
        command_name=(command.command_name if command else (match.command_name if match else None)),
        requires_audit=rule.audit,
    )
