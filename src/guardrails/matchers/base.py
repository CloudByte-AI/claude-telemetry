"""
Matcher contract.

A matcher DETECTS. It never decides:

    matcher (code, shipped)   "this call is a recursive delete"
    rule    (YAML, user)      "recursive deletes need approval"

so a new matcher can never start blocking anyone's work on upgrade.

`OPERATION` is a public interface: it appears in user profiles, taxonomy.json
and audit rows, so renaming one is a breaking change.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from src.guardrails.parser import Command
from src.guardrails.toolcall import ToolCall

# Cap on stored evidence. A matched fragment can contain a secret, so evidence
# is masked before storage AND capped here, so a long command cannot smuggle a
# payload into the audit log.
MAX_EVIDENCE = 256


@dataclass(frozen=True)
class MatchResult:
    """
    What a matcher found.

    `target` is what rules scope on (`target_path: {contains: "/prod"}`).
    `evidence` is what makes an audit row explainable.
    """

    operation: str
    target: str | None = None
    evidence: str = ""
    command_name: str | None = None
    facts: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.evidence and len(self.evidence) > MAX_EVIDENCE:
            object.__setattr__(self, "evidence", self.evidence[:MAX_EVIDENCE] + "...")


class BaseMatcher(ABC):
    """
    Abstract base for every matcher.

    Subclasses MUST implement `match()` and declare:
        OPERATION       taxonomy name - PUBLIC INTERFACE, renaming is breaking
        DOMAIN          fs, exec, vcs, mcp, net, db, infra, sec, sys, devflow
        DESCRIPTION     one line, shown in taxonomy.json and used as a reason fallback
        APPLIES_TO      operation KINDS this can fire for (never platform tool names)
        DEFAULT_ACTION  guidance published in taxonomy.json, NOT a fallback:
                        the profile's tables decide every call
        DEFAULT_ALERT   critical | warn | info
        FACTS           names this matcher puts in MatchResult.facts, for rules
                        to scope on. Declared so a profile typo fails at load
    """

    OPERATION: str = ""
    DOMAIN: str = ""
    DESCRIPTION: str = ""
    APPLIES_TO: tuple[str, ...] = ()
    DEFAULT_ACTION: str = "allow"
    DEFAULT_ALERT: str = "info"
    FACTS: tuple[str, ...] = ()

    @abstractmethod
    def match(self, call: ToolCall) -> MatchResult | None:
        """Return a MatchResult if this operation is present, else None."""
        ...

    # ── helpers available to every matcher ────────────────────────────────

    def hit(
        self,
        *,
        target: str | None = None,
        evidence: str = "",
        command: Command | None = None,
        **facts,
    ) -> MatchResult:
        """Build a MatchResult for this matcher's operation."""
        return MatchResult(
            operation=self.OPERATION,
            target=target,
            evidence=evidence or (command.raw if command else ""),
            command_name=command.command_name if command else None,
            facts=facts,
        )

    @staticmethod
    def commands_named(call: ToolCall, names: Iterable[str]) -> list[Command]:
        """Every parsed command whose resolved name is one of `names`."""
        wanted = frozenset(names)
        return [c for c in call.commands if c.command_name in wanted]

    @staticmethod
    def has_token_run(path: Sequence[str], run: Sequence[str]) -> bool:
        """
        Whether `run` appears as a CONTIGUOUS token run in `path`.

        The same semantics the `contains` operator gives a token-list field, so
        a matcher and a rule agree on what "contains" means. Robust to flag
        position: `aws --profile p s3 rm x` still contains the run (s3, rm).
        """
        run = list(run)
        if not run or len(run) > len(path):
            return False
        return any(list(path[i:i + len(run)]) == run for i in range(len(path) - len(run) + 1))

    @classmethod
    def subcommand_is(cls, command: Command, *run: str) -> bool:
        """`subcommand_is(c, "push")` / `subcommand_is(c, "s3", "rm")`."""
        return cls.has_token_run(command.subcommand_path, run)

    @staticmethod
    def compile_any(patterns: Iterable[str], flags: int = re.IGNORECASE) -> re.Pattern:
        """
        One alternation instead of N patterns. Call it at class-definition
        time, never inside match(): matching runs before every governed call.
        """
        return re.compile("|".join(f"(?:{p})" for p in patterns), flags)
