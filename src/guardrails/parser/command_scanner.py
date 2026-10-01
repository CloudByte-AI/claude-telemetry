"""
Command chain scanner - one tokeniser, two dialects.

Turns one shell string into the individual commands it actually runs, so that
matchers see `rm` with a force flag rather than a blob of text. Handled forms:

    echo ok && rm -rf /x          chained
    FOO=bar rm -rf /x             leading assignment
    echo $(rm -rf /x)             substitution
    /bin/rm -rf /x                absolute path
    rm -r -f /x                   split flags
    sudo rm -rf /x                privilege wrapper
    bash -c "rm -rf /x"           nested interpreter

Limit: this stops an agent that is not trying to evade. It does not stop
`python -c "import shutil; shutil.rmtree(...)"` or a base64-piped payload.
Guardrails are a policy / audit / approval layer, not a sandbox.

The dialect is passed in, never read from the host, so the same payload parses
identically on every machine.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Iterable

# ── Dialects ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Dialect:
    """
    The five token rules that differ between shells, plus name normalisation.

    Everything else is shared, so a parsing fix lands for both shells at once.
    """

    name: str
    # Character that escapes the next character. POSIX uses backslash;
    # PowerShell uses a backtick (which is why backticks do NOT substitute there).
    escape_char: str
    # Whether `cmd` runs a command substitution. True on POSIX, False on
    # PowerShell where the same character is the escape.
    backtick_substitutes: bool
    # Whether a leading NAME=value token is an environment assignment to strip.
    # PowerShell has no inline form (`$env:FOO="bar"; cmd`), so False there.
    allows_env_prefix: bool
    # Command separators, longest first so `&&` is never read as two `&`.
    operators: tuple[str, ...]
    # Prefixes that introduce a substitution whose contents are their own chain.
    substitution_prefixes: tuple[str, ...]
    # Flags are matched case-insensitively and normalised to lower case.
    case_insensitive_flags: bool
    # Command names are matched case-insensitively (Windows filesystems).
    case_insensitive_names: bool
    # Executable suffixes stripped when resolving a command name.
    executable_suffixes: tuple[str, ...]
    # cmdlet / alias -> canonical POSIX command name.
    aliases: dict[str, str] = field(default_factory=dict)


POSIX = Dialect(
    name="posix",
    escape_char="\\",
    backtick_substitutes=True,
    allows_env_prefix=True,
    # `;;` before `;` and `&&`/`||` before `&`/`|`: longest match wins.
    operators=("&&", "||", ";;", ";", "|&", "|", "&", "\n"),
    substitution_prefixes=("$(",),
    case_insensitive_flags=False,
    case_insensitive_names=False,
    executable_suffixes=(),
)

# Other dialects (PowerShell) live in their own modules and call
# register_dialect(), so the scanner has no knowledge of any particular shell.

_DIALECT_REGISTRY: dict[str, Dialect] = {POSIX.name: POSIX}


def register_dialect(dialect: Dialect) -> Dialect:
    """Make a dialect resolvable by name. Called once per dialect at import."""
    _DIALECT_REGISTRY[dialect.name] = dialect
    return dialect


def get_dialect(name: str | None) -> Dialect:
    """Look up a dialect by name, defaulting to POSIX for anything unknown."""
    return _DIALECT_REGISTRY.get((name or "").lower(), POSIX)


# ── Derived-fact tables ───────────────────────────────────────────────────────
#
# Coarse booleans the decision tables compare against. Matchers layer
# operation-specific detection on top of these.

_FORCE_FLAGS = frozenset({"-f", "--force", "-force", "--hard", "-hard", "--yes", "-y"})
_RECURSIVE_FLAGS = frozenset({"-r", "-R", "--recursive", "-recurse", "-recursive", "--recurse"})

_DESTRUCTIVE_COMMANDS = frozenset({
    "rm", "rmdir", "shred", "srm", "unlink", "wipe",
    "mkfs", "dd", "fdisk", "parted", "diskpart", "format",
    "truncate", "shutdown", "reboot",
})

_NETWORK_COMMANDS = frozenset({
    "curl", "wget", "nc", "ncat", "netcat", "socat",
    "scp", "sftp", "rsync", "ssh", "telnet", "ftp",
    "aria2c", "httpie", "http",
})

# Commands that run another command. Both the wrapper and the wrapped command
# are reported, so `sudo rm -rf /` is both a privilege escalation and a delete.
_WRAPPERS: dict[str, int] = {
    # name -> number of positional operands to skip before the wrapped command
    "sudo": 0, "doas": 0, "su": 0,
    "env": 0, "nohup": 0, "setsid": 0, "eval": 0,
    "time": 0, "nice": 0, "ionice": 0, "stdbuf": 0,
    "command": 0, "builtin": 0, "exec": 0,
    "timeout": 1,     # timeout 30 <cmd>
    "xargs": 0,
    "watch": 0,
}

# Interpreters whose `-c` argument is itself a command chain.
_NESTED_SHELLS: dict[str, tuple[str, ...]] = {
    "bash": ("-c",), "sh": ("-c",), "zsh": ("-c",), "dash": ("-c",), "ksh": ("-c",),
    "powershell": ("-command", "-c", "-encodedcommand"),
    "pwsh": ("-command", "-c", "-encodedcommand"),
    "cmd": ("/c", "/k"),
}

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT = re.compile(r"^(?:\d*)(?:>>|>&|>|<<<|<<|<)$")

# Recursion cap for substitutions and nested shells; hostile input can nest
# arbitrarily.
_MAX_DEPTH = 4


# ── Parsed command ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Command:
    """
    One command from a chain, with the fields rules and matchers compare against.

    `subcommand_path` is the flag-stripped operand list and is what rules should
    prefer: `{contains: "s3 rm"}` finds that contiguous run regardless of where
    `--profile prod` sits, which a raw-string match cannot do.
    """

    raw: str                            # the source text of this command
    command_name: str                   # resolved basename: aws, git, rm
    argv: tuple[str, ...]               # every token after the command name
    flags: frozenset[str]               # {"-r", "-f", "--force"} - bundles expanded
    subcommand_path: tuple[str, ...]    # flag-stripped operands: (s3, rm, s3://b/x)
    redirects: tuple[str, ...]          # targets of > >> etc.
    dialect: str
    # True when this command was produced by unwrapping (sudo X) or by parsing a
    # nested `sh -c "..."`, rather than appearing literally in the chain.
    derived: bool = False

    @property
    def subcommand(self) -> str:
        """Raw remainder after the command name - `--profile prod s3 rm ...`."""
        return " ".join(self.argv)

    @property
    def has_force_flag(self) -> bool:
        return bool(self.flags & _FORCE_FLAGS)

    @property
    def has_recursive_flag(self) -> bool:
        return bool(self.flags & _RECURSIVE_FLAGS)

    @property
    def is_destructive(self) -> bool:
        return self.command_name in _DESTRUCTIVE_COMMANDS

    @property
    def is_network_call(self) -> bool:
        return self.command_name in _NETWORK_COMMANDS


# ── Scanner ───────────────────────────────────────────────────────────────────


@dataclass
class _Token:
    kind: str       # "word" | "op"
    value: str
    start: int
    end: int


def _balanced(text: str, open_at: int, opener: str, closer: str) -> tuple[str, int]:
    """
    Return (inner_text, index_after_close) for a bracket opening at `open_at`.

    Skips over quoted regions so `$(echo ")")` does not close early. On an
    unbalanced input the rest of the string is returned, which is the
    conservative choice: more text gets parsed, not less.
    """
    depth = 0
    i = open_at
    quote: str | None = None
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i], i + 1
        i += 1
    return text[open_at + 1:], n


def _scan(text: str, dialect: Dialect) -> tuple[list[_Token], list[str]]:
    """
    Split `text` into word and operator tokens, collecting substitution bodies.

    Substitution bodies are returned separately rather than inlined, because
    `echo $(rm -rf /x)` must be seen as two commands - the outer `echo` and the
    inner `rm` - not as one `echo` with a funny-looking argument.
    """
    tokens: list[_Token] = []
    substitutions: list[str] = []
    buf: list[str] = []
    buf_start = 0
    quote: str | None = None
    i = 0
    n = len(text)

    def flush(end: int) -> None:
        if buf:
            tokens.append(_Token("word", "".join(buf), buf_start, end))
            buf.clear()

    def take_substitution(paren_at: int) -> int:
        """Consume a substitution whose opening paren sits at `paren_at`."""
        inner, after = _balanced(text, paren_at, "(", ")")
        substitutions.append(inner)
        return after

    while i < n:
        ch = text[i]

        # ── inside single quotes: everything is literal ────────────────────
        if quote == "'":
            if ch == "'":
                quote = None
            else:
                buf.append(ch)
            i += 1
            continue

        # ── inside double quotes: escapes and substitutions still apply ────
        if quote == '"':
            if ch == dialect.escape_char and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                quote = None
                i += 1
                continue
            if text.startswith("$(", i):
                i = take_substitution(i + 1)
                continue
            if ch == "`" and dialect.backtick_substitutes:
                inner, after = _until(text, i + 1, "`")
                substitutions.append(inner)
                i = after
                continue
            buf.append(ch)
            i += 1
            continue

        # ── unquoted ───────────────────────────────────────────────────────
        if ch == dialect.escape_char and i + 1 < n:
            # A line continuation joins two lines; anything else is a literal.
            if text[i + 1] != "\n":
                if not buf:
                    buf_start = i
                buf.append(text[i + 1])
            i += 2
            continue

        if ch in ("'", '"'):
            if not buf:
                buf_start = i
            quote = ch
            i += 1
            continue

        sub_prefix = next((p for p in dialect.substitution_prefixes if text.startswith(p, i)), None)
        if sub_prefix:
            i = take_substitution(i + len(sub_prefix) - 1)
            continue

        if ch == "`" and dialect.backtick_substitutes:
            inner, after = _until(text, i + 1, "`")
            substitutions.append(inner)
            i = after
            continue

        if ch in ("(", ")"):
            # Grouping / subshell. Treated as a separator: the contents are
            # commands in their own right and are scanned in the same pass.
            flush(i)
            tokens.append(_Token("op", ch, i, i + 1))
            i += 1
            continue

        if ch.isspace() and ch != "\n":
            flush(i)
            i += 1
            continue

        op = next((o for o in dialect.operators if text.startswith(o, i)), None)
        if op:
            flush(i)
            tokens.append(_Token("op", op, i, i + len(op)))
            i += len(op)
            continue

        if not buf:
            buf_start = i
        buf.append(ch)
        i += 1

    flush(n)
    return tokens, substitutions


def _until(text: str, start: int, closer: str) -> tuple[str, int]:
    """Consume up to the next unescaped `closer`. Used for POSIX backticks."""
    i = start
    n = len(text)
    while i < n:
        if text[i] == "\\" and i + 1 < n:
            i += 2
            continue
        if text[i] == closer:
            return text[start:i], i + 1
        i += 1
    return text[start:], n


# ── Command construction ──────────────────────────────────────────────────────


def _resolve_name(token: str, dialect: Dialect) -> str:
    """
    `/bin/rm` -> `rm`, `C:\\Windows\\System32\\cmd.exe` -> `cmd`,
    `Remove-Item` -> `rm`.

    Path separators of both kinds are stripped, because a Windows agent can
    write either and a rule author should not have to care.
    """
    name = token.replace("\\", "/")
    name = posixpath.basename(name) or name
    if dialect.case_insensitive_names:
        name = name.lower()
    for suffix in dialect.executable_suffixes:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return dialect.aliases.get(name.lower(), name) if dialect.aliases else name


def _expand_flag(token: str, dialect: Dialect) -> set[str]:
    """
    Normalise one flag token into every form a rule might name.

    `-rf` yields {-rf, -r, -f} so that `rm -rf x`, `rm -r -f x` and
    `rm -fr x` all satisfy the same `has_force_flag` check. A PowerShell
    `-Recurse` is lower-cased, since PowerShell flags are case-insensitive.
    """
    flag = token.lower() if dialect.case_insensitive_flags else token
    out = {flag}
    # Long flags and PowerShell-style whole-word flags never bundle.
    if flag.startswith("--") or dialect.case_insensitive_flags:
        return out
    if len(flag) > 2 and flag[0] == "-" and flag[1] != "-" and flag[1:].isalpha():
        out.update(f"-{c}" for c in flag[1:])
    return out


def _build_command(
    words: list[str],
    dialect: Dialect,
    depth: int,
    derived: bool,
    raw: str,
) -> list[Command]:
    """Build the Command for one segment, plus any commands it wraps."""
    if not words:
        return []

    # Strip leading environment assignments: `FOO=bar rm -rf x`.
    idx = 0
    if dialect.allows_env_prefix:
        while idx < len(words) and _ASSIGNMENT.match(words[idx]):
            idx += 1
    if idx >= len(words):
        return []

    name = _resolve_name(words[idx], dialect)
    argv = tuple(words[idx + 1:])

    flags: set[str] = set()
    operands: list[str] = []
    # argv index of each operand, so an unwrapped inner command can be rebuilt
    # from the original token order (see the wrapper handling below).
    operand_positions: list[int] = []
    redirects: list[str] = []
    end_of_flags = False
    skip_next = False

    for position, token in enumerate(argv):
        if skip_next:
            skip_next = False
            continue
        if _REDIRECT.match(token):
            skip_next = True
            following = argv[position + 1] if position + 1 < len(argv) else ""
            if following:
                redirects.append(following)
            continue
        # `cmd >file` with no space still names a redirect target.
        if not end_of_flags and token.startswith(">") and len(token) > 1:
            redirects.append(token.lstrip(">&"))
            continue
        if token == "--":
            end_of_flags = True
            continue
        if not end_of_flags and token.startswith("-") and len(token) > 1:
            flag, _, inline_value = token.partition("=")
            flags.update(_expand_flag(flag, dialect))
            if inline_value:
                operands.append(inline_value)
                operand_positions.append(position)
            continue
        operands.append(token)
        operand_positions.append(position)

    command = Command(
        raw=raw,
        command_name=name,
        argv=argv,
        flags=frozenset(flags),
        subcommand_path=tuple(operands),
        redirects=tuple(redirects),
        dialect=dialect.name,
        derived=derived,
    )
    commands = [command]

    if depth >= _MAX_DEPTH:
        return commands

    # `sudo rm -rf /` is both a privilege escalation and a delete. Report both.
    # The inner command is rebuilt from the argv slice, NOT the operand list:
    # `-rf` was consumed as sudo's flag and would otherwise be lost.
    skip = _WRAPPERS.get(name)
    if skip is not None and len(operand_positions) > skip:
        inner_start = operand_positions[skip]
        inner_words = list(argv[inner_start:])
        if inner_words and inner_words != words[idx:]:
            commands += _build_command(
                inner_words, dialect, depth + 1, True, " ".join(inner_words)
            )

    # `bash -c "rm -rf /"` hides a whole chain inside one operand.
    nested_flags = _NESTED_SHELLS.get(name)
    if nested_flags and operands:
        lowered = {f.lower() for f in flags}
        if lowered & set(nested_flags):
            for operand in operands:
                commands += parse_chain(operand, dialect, _depth=depth + 1)

    return commands


def parse_chain(
    command: str,
    dialect: Dialect | str | None = POSIX,
    *,
    _depth: int = 0,
) -> tuple[Command, ...]:
    """
    Parse a shell string into the commands it runs.

    Never raises. Input that cannot be understood yields whatever was
    recognisable, because this runs in front of every governed tool call.
    """
    if not command or not command.strip():
        return ()
    if _depth > _MAX_DEPTH:
        return ()

    resolved = dialect if isinstance(dialect, Dialect) else get_dialect(dialect)

    try:
        tokens, substitutions = _scan(command, resolved)
    except Exception:
        return ()

    commands: list[Command] = []
    segment: list[str] = []
    seg_start: int | None = None
    seg_end = 0

    def close_segment() -> None:
        nonlocal segment, seg_start
        if segment and seg_start is not None:
            raw = command[seg_start:seg_end]
            commands.extend(
                _build_command(segment, resolved, _depth, False, raw.strip())
            )
        segment = []
        seg_start = None

    for token in tokens:
        if token.kind == "op":
            close_segment()
            continue
        if seg_start is None:
            seg_start = token.start
        seg_end = token.end
        segment.append(token.value)
    close_segment()

    # Substitution bodies are commands too, and are the classic way to hide one.
    for body in substitutions:
        commands.extend(parse_chain(body, resolved, _depth=_depth + 1))

    return tuple(commands)


def command_names(commands: Iterable[Command]) -> tuple[str, ...]:
    """Every distinct command name in a chain, in order of first appearance."""
    seen: dict[str, None] = {}
    for command in commands:
        seen.setdefault(command.command_name, None)
    return tuple(seen)
