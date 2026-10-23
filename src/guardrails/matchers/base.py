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

# Programs that run code they read on standard input (see reads_code_from_stdin).
STDIN_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
STDIN_INTERPRETERS = frozenset({"python", "python3", "py", "node", "ruby", "perl", "php"})

# Hosts that are this machine. Loopback only: a LAN address is another machine.
_LOOPBACK = re.compile(r"^(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|::1|\[::1\]|host\.docker\.internal)$", re.I)
_PRIVATE = re.compile(
    r"^(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|169\.254(?:\.\d{1,3}){2}|[\w-]+\.local|[\w-]+\.internal|[\w-]+\.lan)$",
    re.I,
)
# `host[:port][/path]` as curl accepts it without a scheme.
_BARE_URL = re.compile(r"^(localhost|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})(?::\d+)?(?:/|$)")
_SCHEME_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^@/\s]*@)?(\[[^\]]+\]|[^/:?#\s]+)")
_VAR_REF = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def url_host(operand: str, chain_text: str = "") -> str | None:
    """
    The host an operand names, if it is a URL: `https://x.io/a`, `localhost:3000/a`,
    or `$BASE/auth` where the same command line assigned `BASE=http://...`.
    """
    text = operand.strip("'\"")
    variable = _VAR_REF.match(text)
    if variable and chain_text:
        assigned = re.search(
            rf"(?:^|[\s;&|(]){re.escape(variable.group(1))}=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)",
            chain_text,
        )
        if assigned:
            text = assigned.group(1).strip("'\"") + text[variable.end():]
    found = _SCHEME_URL.match(text) or _BARE_URL.match(text)
    if not found:
        return None
    return found.group(1).strip("[]").lower()


def network_of(host: str | None) -> str:
    """`loopback` (this machine), `private` (a LAN address) or `public`."""
    if not host:
        return "public"
    if _LOOPBACK.match(host) or _LOOPBACK.match(f"[{host}]"):
        return "loopback"
    if _PRIVATE.match(host):
        return "private"
    return "public"


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
    # The exact command that hit, so a rule reads THAT command's flags: in
    # `rm -f a.txt; rm -rf build` a name lookup would pair a hit with the wrong rm.
    command: Command | None = field(default=None, compare=False, repr=False)

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
            command=command,
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
    def operands_from_subcommand(
        command: Command, value_flags: frozenset[str] | set[str] = frozenset(),
    ) -> tuple[str, ...]:
        """
        The operands starting AT the subcommand, so [0] is `push` in
        `git -C /repo push` and `delete` in `kubectl -n prod delete pod x`.

        The parser cannot know which options take a value, so a global
        option's value lands in `subcommand_path` ahead of the subcommand;
        `value_flags` names the global options that do, and their values are
        dropped. Anchoring on [0] is what keeps `git log --grep rebase`,
        `npm run publish` and `kubectl auth can-i delete pods` from reading as
        a rebase, a publish and a delete.
        """
        skipped = 0
        argv = command.argv
        i = 0
        while i < len(argv):
            token = argv[i]
            if token == "--" or not token.startswith("-") or len(token) < 2:
                break
            name, eq, value = token.partition("=")
            if eq:
                if value:
                    skipped += 1   # the parser kept the inline value as an operand
            elif name in value_flags and i + 1 < len(argv):
                skipped += 1
                i += 2
                continue
            i += 1
        return tuple(command.subcommand_path[skipped:])

    @staticmethod
    def starts_with_run(path: Sequence[str], run: Sequence[str]) -> bool:
        """Whether `path` begins with the token run `run`."""
        run = list(run)
        return bool(run) and list(path[:len(run)]) == run

    @staticmethod
    def reads_code_from_stdin(command: Command) -> bool:
        """
        Whether this command runs whatever arrives on its standard input as
        code: `sh`, `bash -s`, a bare `python`/`node`, `powershell -`, and
        PowerShell's `| iex`. `python -m json.tool`, `python -c ...` and
        `bash script.sh` read their input as data (or not at all).
        """
        name = command.command_name
        operands = command.subcommand_path
        flags = command.flags
        if name in STDIN_SHELLS:
            return "-c" not in flags and (not operands or operands[0] == "-" or "-s" in flags)
        if name in STDIN_INTERPRETERS:
            code_flags = {"-c", "-m", "-e", "-E", "--eval", "-p", "--print", "-r"}
            return not (flags & code_flags) and (not operands or operands[0] == "-")
        if name in ("powershell", "pwsh"):
            return "-file" not in flags and (not operands or operands[0] == "-")
        if name == "eval" and command.dialect == "powershell":
            return not operands   # `iwr url | iex`
        return False

    @staticmethod
    def compile_any(patterns: Iterable[str], flags: int = re.IGNORECASE) -> re.Pattern:
        """
        One alternation instead of N patterns. Call it at class-definition
        time, never inside match(): matching runs before every governed call.
        """
        return re.compile("|".join(f"(?:{p})" for p in patterns), flags)
