"""
Which git repos a shell command works in, read from the command text. Used by snapshot.py to
choose the repos it snapshots before a shell call.

The client gives the folder a command STARTS in (the hook payload's `cwd`, which equals the
transcript's: checked live 24 Nov 2026). Assistants often start a command outside any repo and
`cd` into one. On one machine, 4,730 of 11,918 shell calls started outside a repo, 3,242 of them
with a `cd` into one, so snapshotting the start folder alone missed their changes. So the
command is read too:

  - each simple command (cut at ; & | && || and new lines) runs in the folder the `cd`s before
    it lead to: cd / chdir / pushd, and PowerShell's Set-Location / sl / Push-Location; a `cd`
    to a folder that does not exist is ignored
  - `git -C <path> ...` runs in that path
  - an argument that is a path (absolute, `~`, or holding a slash) names the repo it lies in

The start folder counts only when something runs there before any `cd`: `cd repo && make` is
about `repo` alone. A repo is a working-tree root holding .git (snapshot.find_repo).

Bounded: the first MAX_COMMAND_CHARS characters, MAX_PATH_TOKENS path arguments, MAX_REPOS
repos, BUDGET_SECONDS, and file system checks only (no process). On Windows a path on another
network share, or on a drive that is not a local disk, is never looked at: one such check can
block for seconds (measured: about 2.7 s per unknown host, 12 of 12,020 real commands, up to
10.8 s for one). The start folder's own drive or share is fine: it is looked at anyway. A
simple command whose words cannot be read
(unbalanced quotes) is taken to run where the command is at that point, as before. A heredoc's
or a here-string's body is data, so reading stops there. Variables other than the home folder,
globs and command substitutions are not expanded; such arguments are skipped. Never raises.
Stdlib only.
"""

from __future__ import annotations

import os
import re

MAX_REPOS = 3
MAX_COMMAND_CHARS = 20_000
MAX_PATH_TOKENS = 64
BUDGET_SECONDS = 0.5
_LOCAL_DRIVE_TYPES = (2, 3, 6)          # GetDriveTypeW: removable, fixed, RAM disk
_DRIVE_LOCAL: dict = {}

_CD_WORDS = frozenset({"cd", "chdir", "pushd", "set-location", "sl", "push-location"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT = re.compile(r"^\d*[<>]+&?")
_MSYS_DRIVE = re.compile(r"^/([a-zA-Z])(?=/|$)")
_HOME_PREFIXES = ("$env:USERPROFILE", "$env:HOME", "${HOME}", "$HOME", "~")
_ABSOLUTE = re.compile(r"^(?:[a-zA-Z]:[\\/]|[\\/]|~(?:[\\/]|$)|\$HOME\b|\$\{HOME\}|\$env:(?:USERPROFILE|HOME)\b)",
                       re.IGNORECASE)
_PS_WORD = re.compile(r"'(?:[^']|'')*'|\"(?:`.|[^\"`])*\"|\S+")


def is_powershell(tool_name, nt: bool | None = None) -> bool:
    """Claude Code's PowerShell tool, or Cursor's Shell on Windows (PowerShell there)."""
    nt = os.name == "nt" if nt is None else nt
    return tool_name == "PowerShell" or (tool_name == "Shell" and nt)


def segments(command: str, powershell: bool) -> list[str]:
    """The simple commands: cut at unquoted ; & | and new lines. A heredoc (<<, not <<<) or a
    here-string (@' or @" at the end of a line) ends the read after the rest of its line."""
    escape = "`" if powershell else "\\"
    out: list[str] = []
    cur: list[str] = []
    quote = None
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if quote:
            cur.append(c)
            if c == escape and quote == '"' and i + 1 < n:
                cur.append(command[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == escape and i + 1 < n:
            cur.append(c + command[i + 1])
            i += 2
            continue
        heredoc = (not powershell and command.startswith("<<", i) and not command.startswith("<<<", i)
                   and not (i > 0 and command[i - 1] == "<"))
        herestring = (powershell and c == "@" and i + 1 < n and command[i + 1] in "'\""
                      and command[i + 2:i + 3] in ("", "\r", "\n"))
        if heredoc or herestring:
            end = command.find("\n", i)
            cur.append(command[i:] if end < 0 else command[i:end])
            out.append("".join(cur))
            return _clean(out)
        if c in "'\"":
            quote = c
            cur.append(c)
        elif c in ";&|\n":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return _clean(out)


def _clean(parts: list[str]) -> list[str]:
    out = []
    for part in parts:
        part = part.strip().lstrip("({").strip()             # a subshell or a group
        if part:
            out.append(part)
    return out


def words(segment: str, powershell: bool) -> list[str] | None:
    """The words of one simple command, quotes removed; None when they cannot be read."""
    if powershell:
        out = []
        for word in _PS_WORD.findall(segment):
            if len(word) > 1 and word[0] == word[-1] and word[0] in "'\"":
                word = word[1:-1].replace("''", "'") if word[0] == "'" else word[1:-1]
            out.append(word)
        return out
    import shlex
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None


def resolve(token: str, here: str, powershell: bool, home: str | None = None, nt: bool | None = None) -> str | None:
    """The absolute path a path argument names, from folder `here`; None when it cannot be told
    (a variable, a glob, a command substitution, a URL)."""
    nt = os.name == "nt" if nt is None else nt
    t = token.strip()
    if not t or len(t) > 1024 or "://" in t or "\0" in t:
        return None
    for prefix in _HOME_PREFIXES:
        low, rest = t.lower(), t[len(prefix):]
        if low == prefix.lower() or (low.startswith(prefix.lower()) and rest[:1] in ("/", "\\")):
            t = (home or os.path.expanduser("~")) + rest
            break
    if any(ch in t for ch in "$`*?<>|\"") or t == "-":
        return None
    if nt and not powershell:
        match = _MSYS_DRIVE.match(t)                         # Git Bash: /c/Users -> C:/Users
        if match:
            t = match.group(1).upper() + ":" + (t[2:] or "/")
    return os.path.normpath(os.path.join(here, t))


def reachable(path: str, start_drive: str, nt: bool | None = None) -> bool:
    """False on Windows for a path on another network share (\\\\host\\share) or on a drive that
    is not a local disk: looking there can block for seconds. The start folder's drive is fine."""
    nt = os.name == "nt" if nt is None else nt
    if not nt:
        return True
    drive = os.path.splitdrive(path)[0]
    if not drive or drive.lower() == start_drive.lower():
        return True
    if drive.startswith(("\\\\", "//")):
        return False
    letter = drive[:1].upper()
    if letter not in _DRIVE_LOCAL:
        try:
            import ctypes
            _DRIVE_LOCAL[letter] = ctypes.windll.kernel32.GetDriveTypeW(letter + ":\\") in _LOCAL_DRIVE_TYPES
        except Exception:
            _DRIVE_LOCAL[letter] = False
    return _DRIVE_LOCAL[letter]


def _path_like(token: str) -> bool:
    return bool(_ABSOLUTE.match(token)) or "/" in token or "\\" in token


def _cd_target(argv: list[str], powershell: bool):
    """The folder a cd-like word goes to, as written; None when it stays or cannot be told."""
    head, args = argv[0].lower(), argv[1:]
    for i, arg in enumerate(args):
        low = arg.lower()
        if low in ("-path", "-literalpath") and i + 1 < len(args):
            return args[i + 1]
        if low.startswith(("-path:", "-literalpath:")):
            return arg.split(":", 1)[1]
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        # bash: a bare `cd` goes home; PowerShell's Set-Location without a path stays
        return "~" if (not powershell and head in ("cd", "chdir") and not args) else None
    target = positional[0]
    return None if re.match(r"^[+-]\d+$", target) else target


def _git_folder(argv: list[str], here: str, powershell: bool, home, nt) -> str:
    """The folder a git command runs in: `here`, moved by each `-C <path>` before the subcommand."""
    i, folder = 1, here
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] == "-C" and i + 1 < len(argv):
            folder = resolve(argv[i + 1], folder, powershell, home, nt) or folder
            i += 2
        elif argv[i] == "-c":
            i += 2
        else:
            i += 1
    return folder


def command_repos(command: str, cwd: str, powershell: bool, find_repo, home: str | None = None,
                  nt: bool | None = None) -> list[str]:
    """The repos a shell command works in, in first-seen order, at most MAX_REPOS; [] when none.
    `find_repo(path)` gives the working-tree root holding `path`, or None."""
    import time
    repos: list[str] = []
    keys: set[str] = set()
    memo: dict[str, str | None] = {}
    start_drive = os.path.splitdrive(cwd)[0]
    deadline = time.perf_counter() + BUDGET_SECONDS

    def usable(path) -> bool:
        if path == cwd:
            return True                                  # the start folder: looked at anyway
        return (path is not None and time.perf_counter() < deadline
                and reachable(path, start_drive, nt))

    def add(path) -> None:
        if len(repos) >= MAX_REPOS or not usable(path):
            return
        if path not in memo:
            memo[path] = find_repo(path)
        root = memo[path]
        if root:
            key = os.path.normcase(os.path.abspath(root))
            if key not in keys:
                keys.add(key)
                repos.append(root)

    here = cwd
    paths_seen = 0
    for segment in segments(command[:MAX_COMMAND_CHARS], powershell):
        argv = words(segment, powershell)
        if argv is None:
            add(here)                                    # unreadable: it runs where we are
            continue
        while argv and _ASSIGNMENT.match(argv[0]):
            argv = argv[1:]                              # VAR=value before the command
        if not argv:
            continue
        head = argv[0].lower()
        if head.endswith(".exe"):
            head = head[:-4]
        if head in _CD_WORDS:
            target = _cd_target(argv, powershell)
            folder = resolve(target, here, powershell, home, nt) if target is not None else None
            if usable(folder) and os.path.isdir(folder):
                here = folder
            continue
        add(_git_folder(argv, here, powershell, home, nt) if head == "git" else here)
        for token in argv:
            if paths_seen >= MAX_PATH_TOKENS or len(repos) >= MAX_REPOS:
                break
            token = _REDIRECT.sub("", token)
            if token.startswith("-"):
                if "=" not in token:
                    continue
                token = token.split("=", 1)[1]           # --output=C:/x
            if not _path_like(token):
                continue
            paths_seen += 1
            add(resolve(token, here, powershell, home, nt))
    return repos
