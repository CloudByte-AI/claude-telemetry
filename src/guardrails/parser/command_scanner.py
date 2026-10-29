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
    # `<<WORD` here-documents. Their body is the command's standard input, not
    # commands: a commit message line reading "sudo is no longer needed" must
    # not look like a sudo (POSIX only; PowerShell's @'...'@ is a quoted string).
    heredocs: bool = False
    # `#` at the start of a word comments out the rest of the line (both shells;
    # PowerShell also has `<# ... #>` blocks).
    line_comments: bool = True
    block_comments: tuple[str, str] | None = None


POSIX = Dialect(
    name="posix",
    escape_char="\\",
    backtick_substitutes=True,
    allows_env_prefix=True,
    # `;;` before `;` and `&&`/`||` before `&`/`|`: longest match wins.
    operators=("&&", "||", ";;", ";", "|&", "|", "&", "\n"),
    # `<(cmd)` / `>(cmd)` are process substitutions: commands, not redirects.
    substitution_prefixes=("$(", "<(", ">("),
    case_insensitive_flags=False,
    case_insensitive_names=False,
    executable_suffixes=(),
    heredocs=True,
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

# Package runners: they run a tool from a package, so the tool is the command
# (`npx prisma migrate deploy` runs prisma). name -> (the subcommand runs that
# run something - empty when the runner itself does - and the runner's options
# that take a value, so a value is not mistaken for the tool).
_PACKAGE_RUNNERS: dict[str, tuple[tuple[tuple[str, ...], ...], frozenset[str]]] = {
    "npx": ((), frozenset({"-p", "--package"})),
    "pnpx": ((), frozenset({"-p", "--package"})),
    "bunx": ((), frozenset({"-p", "--package"})),
    "uvx": ((), frozenset({"--from", "--with", "--with-requirements", "--python", "-p", "--index", "--index-url"})),
    "npm": ((("exec",), ("x",)), frozenset({"-p", "--package", "-w", "--workspace"})),
    "pnpm": ((("exec",), ("dlx",)), frozenset({"-C", "--dir", "-F", "--filter", "--package"})),
    "yarn": ((("dlx",), ("exec",)), frozenset({"-p", "--package"})),
    "bun": ((("x",),), frozenset({"-p", "--package"})),
    "uv": ((("run",), ("tool", "run")), frozenset({
        "--with", "--with-requirements", "--python", "-p", "--project", "--directory", "--env-file",
        "--extra", "--group", "--only-group", "--package", "--index", "--index-url", "--from",
    })),
    "pipx": ((("run",),), frozenset({"--spec", "--python", "--pip-args", "--index-url"})),
    "poetry": ((("run",),), frozenset({"-C", "--directory", "-P", "--project"})),
    "pdm": ((("run",),), frozenset({"-p", "--project"})),
    "pipenv": ((("run",),), frozenset()),
    "bundle": ((("exec",),), frozenset({"--gemfile"})),
    "conda": ((("run",),), frozenset({"-n", "--name", "-p", "--prefix", "--cwd"})),
    "mamba": ((("run",),), frozenset({"-n", "--name", "-p", "--prefix", "--cwd"})),
    "dotnet": ((("tool", "run"),), frozenset()),
}
# Runner options whose value is itself a command line: `npx -c "eslint ."`.
_RUNNER_CALL_FLAGS = frozenset({"-c", "--call"})


def _runner_inner(name: str, argv: tuple[str, ...]) -> tuple[int | None, str | None]:
    """
    For a package runner: (argv index where the tool it runs starts, or None;
    the command line given to -c/--call, or None).
    """
    runs, value_flags = _PACKAGE_RUNNERS[name]
    i = 0

    def skip_options(i: int) -> tuple[int, str | None]:
        while i < len(argv):
            token = argv[i]
            if token == "--":
                return i + 1, None
            if not token.startswith("-") or len(token) == 1:
                return i, None
            option = token.partition("=")[0]
            if option in _RUNNER_CALL_FLAGS and name in ("npx", "pnpx", "npm", "pnpm"):
                if "=" in token:
                    return len(argv), token.partition("=")[2]
                return len(argv), argv[i + 1] if i + 1 < len(argv) else None
            i += 2 if option in value_flags and "=" not in token else 1
        return i, None

    if runs:
        i, call = skip_options(i)
        if call is not None:
            return None, call
        run = next((r for r in runs if tuple(argv[i:i + len(r)]) == r), None)
        if run is None:
            return None, None
        i += len(run)
    i, call = skip_options(i)
    if call is not None:
        return None, call
    return (i if i < len(argv) else None), None


def _package_tool(word: str) -> str:
    """The tool a package spec names: `prisma@5.2` -> prisma, `@scope/cli@1` -> @scope/cli."""
    if word.startswith("@"):
        scope, _, rest = word[1:].partition("/")
        return "@" + scope + "/" + rest.partition("@")[0] if rest else word
    return word.partition("@")[0] or word


# `command -v x` / `command -V x` only LOOK UP x; they do not run it.
_LOOKUP_ONLY_FLAGS = frozenset({"-v", "-V"})

# POSIX reserved words that precede the command they introduce, and those that
# only close a block. (`for`/`case`/`select` headers are not commands either,
# but their words are a list, not a command, so they stay as they are.)
POSIX_NAME = "posix"
_LEADING_KEYWORDS = frozenset({"if", "then", "else", "elif", "do", "while", "until", "!", "{"})
_CLOSING_KEYWORDS = frozenset({"fi", "done", "esac", "}"})

# Interpreters whose code argument is itself a command chain. Only the
# argument right after the flag is code for a POSIX shell (later ones are $0,
# $1...); PowerShell and cmd join everything after it into one command line.
_NESTED_SHELLS: dict[str, tuple[str, ...]] = {
    "bash": ("-c",), "sh": ("-c",), "zsh": ("-c",), "dash": ("-c",), "ksh": ("-c",),
    "powershell": ("-command", "-c"),
    "pwsh": ("-command", "-c"),
    "cmd": ("/c", "/k"),
}
_JOINED_CODE = frozenset({"powershell", "pwsh", "cmd"})

# PowerShell and cmd keep their own flag rules even when started from a POSIX
# shell (Git Bash runs `powershell -NoProfile -Command ...` all the time):
# `-NoProfile` is ONE case-insensitive flag, not the bundle -N -o -P -r -f -i -l -e.
_WINDOWS_HOSTS = frozenset({"powershell", "pwsh", "cmd"})

# Shells that run a here-document fed to them as commands, when they are given
# no script to run instead. Any other consumer (cat, python, psql, git...)
# receives the body as data.
_HEREDOC_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# PowerShell assignment: `$x = cmd`, `$env:FOO = cmd`, glued `$x=cmd`.
_PS_ASSIGNMENT = re.compile(r"^\$[A-Za-z_][\w:.]*$")
_PS_ASSIGNMENT_GLUED = re.compile(r"^\$[A-Za-z_][\w:.]*=$")
# A PowerShell hashtable entry key (`Name =`, `'Content-Type' =`) at the start
# of an entry; the key goes and the value stays.
_HASHTABLE_KEY = re.compile(r"(^|[;\n{])[ \t\r]*(?:'[^'\n]*'|\"[^\"\n]*\"|[\w.-]+)[ \t]*=(?!=)")
# An entry value that runs nothing: a quoted string without `$(`, a number or
# a variable reference (`'json'`, "Bearer $t", 42, $true, $k.name).
_HASHTABLE_LITERAL = re.compile(
    r"(^|[;\n{])[ \t\r]*(?:'[^'\n]*'|\"(?:[^\"\n$]|\$(?!\())*\"|-?\d[\w.]*|\$[\w:.]+)[ \t\r]*(?=[;\n}]|$)"
)
_REDIRECT = re.compile(r"^(?:\d*|&|\*)(?:>>|>&|>\||>|<<<|<<|<&|<>|<)$")
# Where a substitution sat inside a word: a private mark while scanning, shown
# as SUBSTITUTION_PLACEHOLDER in the word (`"$(pwd)/build"` -> `$(...)/build`).
_SUBSTITUTED = "\x00"
SUBSTITUTION_PLACEHOLDER = "$(...)"
# The redirection operator at a position (longest first).
_REDIRECT_OP = re.compile(r"&>>|&>|>>|>&|>\||>|<<<|<<|<&|<>|<")

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
    # Here-document text fed to this command's standard input (`psql <<EOF`).
    # Data, not commands - a matcher may read it (SQL for a database client).
    stdin: str = ""
    # Names of the commands earlier in the same pipeline, whose output this
    # command reads: `curl x | sh` gives sh piped_from=("curl",).
    piped_from: tuple[str, ...] = ()

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


# `<<WORD`, `<<-WORD`, `<<'WORD'`, `<<"WORD"`, `<<\WORD` (not the `<<<` here-string).
_HEREDOC_START = re.compile(r"<<(-?)[ \t]*(?:'([^'\n]*)'|\"([^\"\n]*)\"|\\?([A-Za-z0-9_.@:+-]+))")


def _heredoc_spec(text: str, at: int) -> tuple[str, bool, int] | None:
    """
    At a `<<` that opens a here-document: (delimiter, strip_leading_tabs,
    index just after the delimiter word). None for `<<<` or no delimiter.
    """
    if not text.startswith("<<", at) or text.startswith("<<<", at):
        return None
    found = _HEREDOC_START.match(text, at)
    if not found:
        return None
    delimiter = next((g for g in found.groups()[1:] if g is not None), None)
    if not delimiter:
        return None
    return delimiter, bool(found.group(1)), found.end()


def _heredoc_body(text: str, start: int, delimiter: str, strip_tabs: bool) -> tuple[str, int]:
    """
    The body lines from `start` (the line after the `<<` line) up to the line
    that is exactly the delimiter. Returns (body, index after that line). A
    body that is never closed runs to the end of the text, as bash reads it.
    """
    lines: list[str] = []
    i = start
    n = len(text)
    while i < n:
        newline = text.find("\n", i)
        end = n if newline == -1 else newline
        line = text[i:end]
        check = (line.lstrip("\t") if strip_tabs else line).rstrip("\r")
        if check == delimiter:
            return "\n".join(lines), (n if newline == -1 else newline + 1)
        lines.append(line)
        if newline == -1:
            break
        i = newline + 1
    return "\n".join(lines), n


def _balanced(text: str, open_at: int, opener: str, closer: str) -> tuple[str, int]:
    """
    Return (inner_text, index_after_close) for a bracket opening at `open_at`.

    Skips over quoted regions so `$(echo ")")` does not close early, and over
    here-document bodies, so the usual commit form
    `-m "$(cat <<'EOF' ... it's ... EOF)"` is not derailed by an apostrophe in
    the message. On an unbalanced input the rest of the string is returned,
    which is the conservative choice: more text gets parsed, not less.
    """
    depth = 0
    i = open_at
    quote: str | None = None
    pending: list[tuple[str, bool]] = []
    n = len(text)
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch == "<":
            spec = _heredoc_spec(text, i)
            if spec:
                pending.append((spec[0], spec[1]))
                i = spec[2]
                continue
        if ch == "\n" and pending:
            i += 1
            for delimiter, strip_tabs in pending:
                _, i = _heredoc_body(text, i, delimiter, strip_tabs)
            pending.clear()
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


def _scan(text: str, dialect: Dialect) -> tuple[list[_Token], list[str], list[str]]:
    """
    Split `text` into word and operator tokens, collecting substitution bodies
    and here-document bodies.

    Substitution bodies are returned separately rather than inlined, because
    `echo $(rm -rf /x)` must be seen as two commands - the outer `echo` and the
    inner `rm` - not as one `echo` with a funny-looking argument.

    A here-document is the opposite: its body is DATA on the command's standard
    input. It becomes a `heredoc` token (an index into the returned bodies) at
    the `<<`, and its lines are skipped rather than read as commands. Comments
    are skipped too, so `# then: curl x | sh` runs nothing.
    """
    tokens: list[_Token] = []
    substitutions: list[str] = []
    heredoc_bodies: list[str] = []
    pending: list[tuple[int, str, bool]] = []
    buf: list[str] = []
    buf_start = 0
    quote: str | None = None
    i = 0
    n = len(text)

    def flush(end: int) -> None:
        if buf:
            word = "".join(buf)
            # A word that was only a substitution (`eval "$(curl ...)"`) has no
            # text of its own. One that also has text keeps a placeholder where
            # the substitution was: `"$(pwd)/build"` is `$(...)/build`, not
            # `/build`, and `"$(cat f)":refs/heads/main` is not `:main`.
            if word.strip(_SUBSTITUTED):
                tokens.append(_Token("word", word.replace(_SUBSTITUTED, SUBSTITUTION_PLACEHOLDER), buf_start, end))
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
                buf.append(_SUBSTITUTED)
                continue
            if ch == "`" and dialect.backtick_substitutes:
                inner, after = _until(text, i + 1, "`")
                substitutions.append(inner)
                buf.append(_SUBSTITUTED)
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

        # A PowerShell hashtable literal `@{ Name = $x.name; Size = Get-Size }`: the
        # keys are names, never commands, and a literal value runs nothing,
        # while any other value is a pipeline PowerShell runs, so those values
        # are scanned like a substitution.
        if dialect.name != POSIX_NAME and text.startswith("@{", i):
            if not buf:
                buf_start = i
            inner, i = _balanced(text, i + 1, "{", "}")
            values = _HASHTABLE_LITERAL.sub(r"\1", _HASHTABLE_KEY.sub(r"\1", inner))
            if values.strip(" \t\r\n;"):
                substitutions.append(values)
            buf.append(_SUBSTITUTED)
            continue

        sub_prefix = next((p for p in dialect.substitution_prefixes if text.startswith(p, i)), None)
        if sub_prefix:
            if not buf:
                buf_start = i
            i = take_substitution(i + len(sub_prefix) - 1)
            buf.append(_SUBSTITUTED)
            continue

        if ch == "`" and dialect.backtick_substitutes:
            if not buf:
                buf_start = i
            inner, after = _until(text, i + 1, "`")
            substitutions.append(inner)
            buf.append(_SUBSTITUTED)
            i = after
            continue

        # A comment starts only at the beginning of a word: `a#b` and `$#` are text.
        if not buf and dialect.block_comments and text.startswith(dialect.block_comments[0], i):
            close = text.find(dialect.block_comments[1], i + len(dialect.block_comments[0]))
            i = n if close == -1 else close + len(dialect.block_comments[1])
            continue
        if ch == "#" and not buf and dialect.line_comments:
            newline = text.find("\n", i)
            i = n if newline == -1 else newline
            continue

        if ch == "<" and dialect.heredocs:
            spec = _heredoc_spec(text, i)
            if spec:
                flush(i)
                tokens.append(_Token("heredoc", str(len(heredoc_bodies)), i, spec[2]))
                pending.append((len(heredoc_bodies), spec[0], spec[1]))
                heredoc_bodies.append("")
                i = spec[2]
                continue

        # A redirection is its own token even when glued to its neighbours:
        # `2>/dev/null`, `2>&1`, `&>log`. The file descriptor in front (`2`,
        # or PowerShell's `*`) belongs to it, and so does a duplicated one
        # (`&1`), which must not read as a background `&` and a command `1`.
        if ch in "<>" or text.startswith("&>", i):
            prefix = "".join(buf)
            fd = prefix if prefix.isdigit() or (prefix == "*" and dialect.name != POSIX_NAME) else ""
            if fd:
                buf.clear()
            else:
                flush(i)
            start = i - len(fd)
            op = _REDIRECT_OP.match(text, i).group(0)
            i += len(op)
            if op.endswith("&"):
                k = i
                while k < n and (text[k].isdigit() or text[k] == "-"):
                    k += 1
                if k > i:
                    tokens.append(_Token("word", fd + op, start, i))
                    tokens.append(_Token("word", text[i:k], i, k))
                    i = k
                    continue
            tokens.append(_Token("word", fd + op, start, i))
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
            # The line that opened here-documents is over: their bodies follow.
            if op == "\n" and pending:
                for index, delimiter, strip_tabs in pending:
                    heredoc_bodies[index], i = _heredoc_body(text, i, delimiter, strip_tabs)
                pending.clear()
            continue

        if not buf:
            buf_start = i
        buf.append(ch)
        i += 1

    flush(n)
    return tokens, substitutions, heredoc_bodies


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


# Tools whose single-dash options are whole words, never bundles of letters:
# find's `-regex` is not -r -e -g -e -x, terraform's `-auto-approve` is one option.
_SINGLE_DASH_WORDS = frozenset({"find", "java", "javac", "terraform", "tofu"})


def _expand_flag(token: str, dialect: Dialect, whole_word: bool = False, bundles: bool = True) -> set[str]:
    """
    Normalise one flag token into every form a rule might name.

    `-rf` yields {-rf, -r, -f} so that `rm -rf x`, `rm -r -f x` and
    `rm -fr x` all satisfy the same `has_force_flag` check. A PowerShell
    `-Recurse` is lower-cased, since PowerShell flags are case-insensitive.
    `whole_word` applies PowerShell's rules inside a POSIX chain, for
    `powershell -NoProfile ...` started from Git Bash.
    """
    case_insensitive = dialect.case_insensitive_flags or whole_word
    flag = token.lower() if case_insensitive else token
    out = {flag}
    # Long flags and PowerShell-style whole-word flags never bundle.
    if flag.startswith("--") or case_insensitive or not bundles:
        return out
    if len(flag) > 2 and flag[0] == "-" and flag[1] != "-" and flag[1:].isalpha():
        out.update(f"-{c}" for c in flag[1:])
    return out


def _code_position(argv: tuple[str, ...], code_flags: tuple[str, ...], whole_word: bool) -> int | None:
    """
    Index in `argv` of the flag that introduces inline code: `-c`, a bundle
    containing it (`-lc`), or for PowerShell any prefix of -Command (`-Comm`).
    """
    for position, token in enumerate(argv):
        if token == "--":
            return None
        flag = (token.lower() if whole_word else token).partition("=")[0]
        if flag in code_flags:
            return position
        if whole_word and "-command" in code_flags and len(flag) >= 2 and "-command".startswith(flag):
            return position
        if (not whole_word and "-c" in code_flags and flag.startswith("-")
                and not flag.startswith("--") and flag[1:].isalpha() and "c" in flag[1:]):
            return position
    return None


def _build_command(
    words: list[str],
    dialect: Dialect,
    depth: int,
    derived: bool,
    raw: str,
    stdin: str = "",
    piped_from: tuple[str, ...] = (),
) -> list[Command]:
    """Build the Command for one segment, plus any commands it wraps."""
    if not words:
        return []

    # Strip leading environment assignments (`FOO=bar rm -rf x`) and the shell
    # words that only introduce a command (`then rm -rf x`, `do rm -rf $d`,
    # `! grep -q x`), which would otherwise be read as the command's name.
    idx = 0
    while idx < len(words):
        if dialect.allows_env_prefix and _ASSIGNMENT.match(words[idx]):
            idx += 1
        elif dialect.name == POSIX_NAME and words[idx] in _LEADING_KEYWORDS:
            idx += 1
        # PowerShell `$pass = aws ecr get-login-password`: the command is aws.
        elif (dialect.name != POSIX_NAME and _PS_ASSIGNMENT.match(words[idx])
              and idx + 1 < len(words) and words[idx + 1] == "="):
            idx += 2
        elif dialect.name != POSIX_NAME and _PS_ASSIGNMENT_GLUED.match(words[idx]):
            idx += 1
        else:
            break
    if idx >= len(words) or (dialect.name == POSIX_NAME and words[idx] in _CLOSING_KEYWORDS
                             and len(words) == idx + 1):
        return []

    name = _resolve_name(words[idx], dialect)
    argv = tuple(words[idx + 1:])

    # powershell.exe / cmd.exe from a POSIX shell: one name, Windows flag rules.
    host = name.lower()[:-4] if name.lower().endswith(".exe") else name.lower()
    windows_host = host in _WINDOWS_HOSTS
    if windows_host:
        name = host

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
        # cmd's switches are /c, /k, /q, /s...
        if not end_of_flags and name == "cmd" and len(token) == 2 and token[0] == "/" and token[1].isalpha():
            flags.add(token.lower())
            continue
        if not end_of_flags and token.startswith("-") and len(token) > 1:
            flag, _, inline_value = token.partition("=")
            flags.update(_expand_flag(flag, dialect, whole_word=windows_host,
                                      bundles=name not in _SINGLE_DASH_WORDS))
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
        stdin=stdin,
        piped_from=piped_from,
    )
    commands = [command]

    if depth >= _MAX_DEPTH:
        return commands

    # `sudo rm -rf /` is both a privilege escalation and a delete. Report both.
    # The inner command is rebuilt from the argv slice, NOT the operand list:
    # `-rf` was consumed as sudo's flag and would otherwise be lost. The inner
    # command is the one that reads the pipe and the here-document.
    # `command -v sudo` only looks sudo up, so it wraps nothing.
    skip = _WRAPPERS.get(name)
    looks_up_only = name == "command" and bool(flags & _LOOKUP_ONLY_FLAGS)
    if skip is not None and not looks_up_only and len(operand_positions) > skip:
        inner_start = operand_positions[skip]
        inner_words = list(argv[inner_start:])
        if inner_words and inner_words != words[idx:]:
            commands += _build_command(
                inner_words, dialect, depth + 1, True, " ".join(inner_words),
                stdin=stdin, piped_from=piped_from,
            )

    # `npx prisma migrate deploy`, `pnpm exec eslint .`, `uv run pytest`: the
    # tool the runner starts is a command in its own right.
    if name in _PACKAGE_RUNNERS and not windows_host:
        start, call = _runner_inner(name, argv)
        if call:
            commands += parse_chain(call, dialect, _depth=depth + 1)
        elif start is not None:
            inner_words = [_package_tool(argv[start])] + list(argv[start + 1:])
            commands += _build_command(
                inner_words, dialect, depth + 1, True, " ".join(argv[start:]),
                stdin=stdin, piped_from=piped_from,
            )

    # `bash -c "rm -rf /"` hides a whole chain inside one operand. Only the
    # code itself is parsed: `-ExecutionPolicy Bypass` before it is not code.
    nested_flags = _NESTED_SHELLS.get(name)
    if nested_flags:
        position = _code_position(argv, nested_flags, whole_word=windows_host)
        if position is not None and position + 1 < len(argv):
            code = " ".join(argv[position + 1:]) if name in _JOINED_CODE else argv[position + 1]
            # cmd's `del` / `rd` are the same Windows names PowerShell aliases to rm.
            nested_dialect = get_dialect("powershell") if name in _JOINED_CODE else dialect
            commands += parse_chain(code, nested_dialect, _depth=depth + 1)

    # `bash <<'EOF' ... EOF` runs its here-document; given a script or -c, the
    # shell runs that instead and the here-document is the script's input.
    if (stdin and name in _HEREDOC_SHELLS and "-c" not in flags
            and (not operands or operands[0] == "-" or "-s" in flags)):
        commands += parse_chain(stdin, dialect, _depth=depth + 1)

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
        tokens, substitutions, heredoc_bodies = _scan(command, resolved)
    except Exception:
        return ()

    commands: list[Command] = []
    segment: list[str] = []
    segment_stdin: list[str] = []
    seg_start: int | None = None
    seg_end = 0
    # Pipeline bookkeeping: the names of the commands whose output flows into
    # the next segment, so `curl x | tee f | sh` gives sh piped_from=(curl, tee).
    pipeline: list[str] = []
    last_names: list[str] = []
    piped_from: tuple[str, ...] = ()

    def close_segment() -> None:
        nonlocal segment, segment_stdin, seg_start, last_names
        if segment and seg_start is not None:
            raw = command[seg_start:seg_end]
            built = _build_command(
                segment, resolved, _depth, False, raw.strip(),
                stdin="\n".join(segment_stdin), piped_from=piped_from,
            )
            commands.extend(built)
            last_names = [c.command_name for c in built]
        segment = []
        segment_stdin = []
        seg_start = None

    for token in tokens:
        if token.kind == "heredoc":
            segment_stdin.append(heredoc_bodies[int(token.value)])
            if seg_start is not None:
                seg_end = token.end
            continue
        if token.kind == "op":
            close_segment()
            if token.value in ("|", "|&"):
                pipeline.extend(last_names)
                piped_from = tuple(dict.fromkeys(pipeline))
            elif token.value not in ("(", ")"):
                # Grouping keeps the pipeline; any other separator ends it.
                pipeline, last_names, piped_from = [], [], ()
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
