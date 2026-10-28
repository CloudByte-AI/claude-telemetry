"""
Filesystem matchers.

Matchers take a normalised ToolCall, not a command string: on Claude Code a
delete is `Bash` + `rm`, on Cursor a structured `Delete` tool call with no
shell string. One matcher covers both, so one profile rule covers both platforms.
"""

from __future__ import annotations

import posixpath
import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult, summary_path, summary_value
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import (
    KIND_DELETE,
    KIND_EDIT,
    KIND_READ,
    KIND_SHELL,
    KIND_WRITE,
    WRITE_KINDS,
    ToolCall,
    resolve_path,
)

# Commands that remove things, in either dialect (PowerShell's Remove-Item and
# its aliases already normalise to `rm` in the parser).
_DELETE_COMMANDS = frozenset({"rm", "rmdir", "shred", "srm", "unlink", "wipe"})

# Filename shapes that carry credentials or keys. Matched on the basename or on
# a path segment, never as a bare substring: `environment.ts` must not look like
# a `.env`.
_SENSITIVE_BASENAMES = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".htpasswd",
    "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "known_hosts", "authorized_keys", "secrets.yaml", "secrets.yml",
    "secrets.json", "terraform.tfvars", ".dockercfg", "kubeconfig",
})
_SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".asc", ".ppk",
    # Terraform variable files hold provider credentials (`prod.tfvars`).
    ".tfvars", ".tfvars.json",
)
# `.env.production`, `.env.local`... A service-account KEY is a JSON file
# (`service-account-prod.json`); `ServiceAccountController.ts` and a Helm
# `serviceaccount.yaml` template hold no secret.
_ENV_PREFIX = ".env."
_SERVICE_ACCOUNT_PREFIXES = ("service-account", "serviceaccount", "service_account")
# Committed templates that document a secret file's shape, never its values.
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".default", ".tmpl", ".tpl")
# Directory segments whose contents are sensitive regardless of filename.
_SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure"})
# The public half of a key pair carries no secret (`~/.ssh/id_ed25519.pub`).
_PUBLIC_SUFFIXES = (".pub",)

# Shell commands whose operands are text, not files (`echo ".env" >> .gitignore`
# writes .gitignore; its redirect is still checked).
_TEXT_COMMANDS = frozenset({"echo", "printf", "print", "logger", "say", "notify-send", "write-output", "write-host"})
# Commands whose first operand is a pattern or program, and the flags that
# move it out of the operands (`grep -e PAT file`, `sed -f script file`).
_PATTERN_FIRST: dict[str, frozenset[str]] = {
    "grep": frozenset({"-e", "--regexp", "-f", "--file"}),
    "egrep": frozenset({"-e", "--regexp", "-f", "--file"}),
    "fgrep": frozenset({"-e", "--regexp", "-f", "--file"}),
    "rg": frozenset({"-e", "--regexp", "-f", "--file"}),
    "ag": frozenset(),
    "ack": frozenset(),
    "sed": frozenset({"-e", "--expression", "-f", "--file"}),
    "awk": frozenset({"-f"}),
    "gawk": frozenset({"-f"}),
    "jq": frozenset({"-f", "--from-file"}),
    "yq": frozenset(),
}
# Commands that only look at a path's name or metadata, never its contents.
_METADATA_COMMANDS = frozenset({
    "ls", "dir", "stat", "test", "[", "[[", "file", "du", "find", "realpath", "readlink",
    "basename", "dirname", "which", "type", "command", "where", "wc",
    # shell keywords the parser reads as commands: `for f in .env .env.local`
    "for", "in", "case", "if", "then", "elif", "while", "until", "select", "do", "done", "esac", "fi",
    # PowerShell path cmdlets (compared lower-case): they build or test a path string
    "test-path", "join-path", "split-path", "resolve-path", "get-item", "get-childitem",
})
# git subcommands that look a path up in the index or ignore rules, never its contents.
_GIT_PATH_LOOKUPS = frozenset({"check-ignore", "ls-files", "status"})
_GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
_GIT_PRINTS_REV_PATH = frozenset({"show", "cat-file"})


def _rev_path(operand: str) -> str:
    """`HEAD:.env` -> `.env`; a plain path, a URL or a drive (`C:/x`) unchanged."""
    rev, sep, path = operand.partition(":")
    if not sep or "://" in operand or (len(rev) == 1 and rev.isalpha()):
        return operand
    return path
# An operand that is code, a query or a remote script rather than a path:
# `node -e "...('.env.prod')"`, `--scripts "grep x .env"` (quotes left inside
# after the shell removed its own), `--query "Contents[0].Key"`,
# `custom-columns=KEY:.spec.taints[*].key` (an index). Parentheses, braces and
# plain brackets stay: `Program Files (x86)`, `app/(auth)`, `app/[id]` are paths.
_NOT_A_PATH = frozenset("\n'\"`;|<>")
_INDEX = re.compile(r"\[(?:-?\d+|\*)\]")


def _looks_like_path(operand: str) -> bool:
    return not ("://" in operand or _NOT_A_PATH.intersection(operand) or _INDEX.search(operand))


def _is_sensitive_path(path: str | None) -> bool:
    if not path:
        return False
    normalised = str(path).replace("\\", "/").rstrip("/")
    # A glob is judged by what it can match: `.env*` covers `.env`, `*.pem` a key.
    basename = posixpath.basename(normalised).lower().replace("*", "").replace("?", "")
    segments = {segment.lower() for segment in normalised.split("/")}

    if basename.endswith(_TEMPLATE_SUFFIXES) or basename.endswith(_PUBLIC_SUFFIXES):
        return False
    if basename in _SENSITIVE_BASENAMES:
        return True
    if basename.endswith(_SENSITIVE_SUFFIXES):
        return True
    if basename.startswith(_ENV_PREFIX):
        return True
    if basename.startswith(_SERVICE_ACCOUNT_PREFIXES) and basename.endswith(".json"):
        return True
    return bool(segments & _SENSITIVE_DIRS)


def _file_operands(command) -> list[str]:
    """The operands of a shell command that name files it opens."""
    name = command.command_name
    if name.lower() in _TEXT_COMMANDS or name.lower() in _METADATA_COMMANDS:
        return []
    if name == "git":
        sub = BaseMatcher.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
        if sub and sub[0] in _GIT_PATH_LOOKUPS:
            return []
        # `git hash-object FILE` prints a hash; `-w` also stores the content.
        if sub and sub[0] == "hash-object" and "-w" not in command.flags:
            return []
        # `git show HEAD:.env` / `git cat-file -p main:config/secrets.yml` print
        # the file at that revision: the path is after the colon.
        if sub and sub[0] in _GIT_PRINTS_REV_PATH:
            # `cat-file -e/-t/-s` test existence or print the type or size, not the content.
            if sub[0] == "cat-file" and command.flags & {"-e", "-t", "-s"}:
                return []
            return [_rev_path(o) for o in sub[1:] if _looks_like_path(_rev_path(o))]
    operands = [o for o in command.subcommand_path if not o.startswith("-")]
    pattern_flags = _PATTERN_FIRST.get(name)
    if pattern_flags is not None and operands and not (command.flags & pattern_flags):
        operands = operands[1:]
    return [o for o in operands if _looks_like_path(o)]


def _candidate_paths(call: ToolCall) -> list[tuple[str, str, object]]:
    """(resolved_path, evidence, command) for everything this call opens."""
    found: list[tuple[str, str, object]] = []
    for path in call.file_paths:
        found.append((path, path, None))
    for command in call.commands:
        for operand in _file_operands(command):
            resolved = resolve_path(operand, call.cwd)
            if resolved:
                found.append((resolved, command.raw, command))
        for target in command.redirects:
            resolved = resolve_path(target, call.cwd)
            if resolved:
                found.append((resolved, command.raw, command))
    return found


# `find` tests that narrow what it matches: with one, `-delete` removes the
# matching files; without any, everything under the start paths.
_FIND_FILTERS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename", "-regex", "-iregex",
    "-type", "-newer", "-mtime", "-mmin", "-atime", "-amin", "-ctime", "-cmin", "-size",
    "-empty", "-user", "-group", "-perm", "-lname", "-ilname", "-links", "-inum", "-samefile",
})
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})


def _find_delete(command) -> tuple[list[str], bool, bool] | None:
    """
    For `find ... -delete` or `find ... -exec rm ...`: (start paths, recursive,
    forced). find's options are words (`-name`, `-delete`), so argv is read
    as written rather than as bundled single-letter flags.
    """
    if command.command_name != "find":
        return None
    argv = list(command.argv)
    starts: list[str] = []
    for token in argv:
        if token.startswith(("-", "(", "!", ")")) or token in ("\\(", "\\)"):
            break
        starts.append(token)
    filtered = any(t in _FIND_FILTERS for t in argv)
    if "-delete" in argv:
        return starts or ["."], not filtered, True
    for i, token in enumerate(argv):
        if token in _FIND_EXEC and i + 1 < len(argv) and posixpath.basename(argv[i + 1]) in _DELETE_COMMANDS:
            run = []
            for word in argv[i + 2:]:
                if word in (";", "\\;", "+"):
                    break
                run.append(word)
            short = "".join(w[1:] for w in run if w.startswith("-") and not w.startswith("--"))
            recursive = "r" in short.lower() or "--recursive" in run
            forced = "f" in short or "--force" in run
            return starts or ["."], recursive, forced
    return None


# Folders that are disposable build or dependency output.
_DISPOSABLE = frozenset({
    "node_modules", "dist", "build", ".next", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".turbo", ".cache", ".parcel-cache", ".nuxt", ".svelte-kit", "coverage",
})


def _is_disposable(operand: str) -> bool:
    """Whether a delete target, AS WRITTEN, is (inside) build or dependency output."""
    parts = [p for p in operand.replace("\\", "/").split("/") if p not in ("", ".")]
    return any(p in _DISPOSABLE for p in parts)


@register_matcher
class DeleteMatcher(BaseMatcher):
    OPERATION = "fs.delete"
    DOMAIN = "fs"
    DESCRIPTION = "Deletes a filesystem path"
    APPLIES_TO = (KIND_SHELL, KIND_DELETE)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"

    def match(self, call: ToolCall) -> MatchResult | None:
        # Cursor's first-class Delete tool: structured, no shell string.
        if call.kind == KIND_DELETE and call.file_paths:
            return self.hit(target=call.file_paths[0], evidence=call.file_paths[0])

        for command in self.commands_named(call, _DELETE_COMMANDS):
            target = next(
                (resolve_path(o, call.cwd) for o in command.subcommand_path if not o.startswith("-")),
                None,
            )
            return self.hit(target=target, command=command)
        for command in call.commands:
            found = _find_delete(command)
            if found:
                return self.hit(target=resolve_path(found[0][0], call.cwd), command=command)
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        return f"delete {summary_path(result.target)}" if result.target else None


@register_matcher
class RecursiveDeleteMatcher(BaseMatcher):
    OPERATION = "fs.delete.recursive"
    DOMAIN = "fs"
    DESCRIPTION = "Recursive or forced delete of a filesystem path"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    # disposable: every target, as written (not the folder the session runs
    # in), is build or dependency output - node_modules, dist, build, .next...
    FACTS = ("recursive", "forced", "target_count", "disposable")

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        target = summary_path(result.target) or "a folder"
        return f"delete {target} and everything in it" if result.facts.get("recursive") else f"force-delete {target}"

    def match(self, call: ToolCall) -> MatchResult | None:
        # (command, targets, recursive, forced) for every forced or recursive delete.
        deletes = [
            (c, [o for o in c.subcommand_path if not o.startswith("-")], c.has_recursive_flag, c.has_force_flag)
            for c in self.commands_named(call, _DELETE_COMMANDS)
            if c.has_recursive_flag or c.has_force_flag
        ]
        for command in call.commands:
            found = _find_delete(command)
            if found and (found[1] or found[2]):
                deletes.append((command, *found))
        # One hit per call, so it is the most severe delete: in
        # `rm -f a.tgz && rm -rf /tmp/pkg` that is the recursive one, which a
        # rule scoped on `recursive: true` must not miss.
        deletes.sort(key=lambda d: not d[2])
        for command, targets, recursive, forced in deletes:
            # The target a rule sees is the one worth reviewing: in
            # `rm -rf node_modules src` that is src, so an "allow node_modules
            # cleanup" rule cannot wave the whole command through.
            chosen = next((t for t in targets if not _is_disposable(t)), targets[0] if targets else None)
            resolved = resolve_path(chosen, call.cwd) if chosen else None
            return self.hit(
                target=resolved,
                command=command,
                recursive=recursive,
                forced=forced,
                target_count=len(targets),
                disposable=bool(targets) and all(_is_disposable(t) for t in targets),
            )
        return None


# How a shell command touches a credential file, for summaries.
_SENSITIVE_VERBS = (
    ("read", frozenset({"cat", "type", "get-content", "gc", "less", "more", "head", "tail", "bat", "grep",
                        "source", ".", "strings", "xxd", "od"})),
    ("delete", frozenset({"rm", "del", "erase", "remove-item", "unlink", "shred"})),
    ("copy", frozenset({"cp", "copy", "copy-item", "scp", "rsync"})),
    ("move", frozenset({"mv", "move", "move-item", "rename-item"})),
    ("upload", frozenset({"curl", "wget", "http", "invoke-webrequest", "invoke-restmethod"})),
)


@register_matcher
class SensitiveFileMatcher(BaseMatcher):
    OPERATION = "fs.sensitive"
    DOMAIN = "fs"
    DESCRIPTION = "Touches a credential, key or other sensitive file"
    APPLIES_TO = (KIND_SHELL, KIND_READ, KIND_WRITE, KIND_EDIT, KIND_DELETE)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("sensitive_path",)

    def match(self, call: ToolCall) -> MatchResult | None:
        for path, evidence, command in _candidate_paths(call):
            if _is_sensitive_path(path):
                # `kind` distinguishes read from write, so ONE matcher serves
                # both and the rule decides which it cares about:
                #   { operation: fs.sensitive, kind: write, decision: ask }
                # The command is attached so `is_destructive` reads THAT one.
                return self.hit(target=path, evidence=evidence, command=command, sensitive_path=path)
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        # File tools (Read, Write, Edit) are already described by their own name and path.
        if result.command is None:
            return None
        name = summary_value(posixpath.basename(str(result.target or "").replace("\\", "/"))) or "a credential file"
        command_name = result.command.command_name.lower()
        verb = next((v for v, names in _SENSITIVE_VERBS if command_name in names), None)
        if command_name in ("sed", "perl") and any(f.startswith("-i") for f in result.command.flags):
            verb = "change"
        if verb is None:
            verb = "change" if result.command.is_destructive else "use"
        return f"{verb} credential file {name}"


# Files that control the agent itself, the CI pipeline or git's hooks.
# kind -> (paths relative to any folder, as `/`-joined lowercase suffixes or
# folder prefixes ending in `/`).
_CONTROL_FILES: dict[str, tuple[str, ...]] = {
    "agent_config": (
        ".claude/settings.json", ".claude/settings.local.json", ".claude/hooks/", ".claude.json",
        ".mcp.json", ".cursor/hooks.json", ".cursor/mcp.json", ".cursor/cli.json", ".cursor/hooks/",
    ),
    "ci_pipeline": (
        ".github/workflows/", ".gitlab-ci.yml", ".gitlab-ci.yaml", "jenkinsfile", "azure-pipelines.yml",
        "azure-pipelines.yaml", "bitbucket-pipelines.yml", ".circleci/", ".buildkite/",
    ),
    "git_hook": (".git/hooks/", ".husky/", ".pre-commit-config.yaml", "lefthook.yml", "lefthook.yaml"),
}
# Shell commands that change the files they name (PowerShell's Remove-Item,
# Copy-Item and Move-Item already resolve to rm, cp and mv).
_CHANGING_COMMANDS = frozenset({
    "rm", "rmdir", "unlink", "del", "mv", "cp", "tee", "touch", "truncate", "ln", "install",
    "set-content", "add-content", "out-file", "new-item", "clear-content",
})
_CONTROL_KIND_WORDS = {"agent_config": "agent settings", "ci_pipeline": "CI pipeline", "git_hook": "git hook"}


def _control_kind(path: str) -> str | None:
    """The kind of control file `path` is, or None."""
    normalised = "/" + str(path).replace("\\", "/").lower().lstrip("/")
    for kind, shapes in _CONTROL_FILES.items():
        for shape in shapes:
            if shape.endswith("/"):
                if f"/{shape}" in normalised + "/":
                    return kind
            elif normalised.endswith(f"/{shape}"):
                return kind
    return None


def _control_scope(path: str) -> str:
    """`home` for the user's own config (`~/.claude/...`), else `project`."""
    from src.common.paths import get_home_dir

    home = str(get_home_dir()).replace("\\", "/").rstrip("/").lower()
    normalised = str(path).replace("\\", "/").lower()
    if normalised.startswith(home + "/"):
        rest = normalised[len(home) + 1:]
        if rest.startswith((".claude/", ".cursor/")) or rest in (".claude.json", ".mcp.json"):
            return "home"
    return "project"


def _changed_paths(call: ToolCall) -> list[tuple[str, object]]:
    """(resolved_path, command) for every file this call writes, edits or deletes."""
    if call.kind in WRITE_KINDS:
        return [(path, None) for path in call.file_paths]
    found: list[tuple[str, object]] = []
    for command in call.commands:
        targets = list(command.redirects)
        name = command.command_name.lower()
        operands = [o for o in command.subcommand_path if not o.startswith("-") and _looks_like_path(o)]
        if name in ("cp", "mv", "install", "ln"):
            targets += operands[-1:]
        elif name in _CHANGING_COMMANDS:
            targets += operands
        elif name == "sed" and ({"-i", "--in-place"} & command.flags or any(a.startswith("-i") for a in command.argv)):
            targets += operands[1:]
        for target in targets:
            resolved = resolve_path(target, call.cwd)
            if resolved:
                found.append((resolved, command))
    return found


@register_matcher
class ControlFileMatcher(BaseMatcher):
    OPERATION = "fs.control_file"
    DOMAIN = "fs"
    DESCRIPTION = "Changes a file that controls the agent, the CI pipeline or git hooks"
    APPLIES_TO = (KIND_SHELL, KIND_WRITE, KIND_EDIT, KIND_DELETE)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # file_role: agent_config (the agent's settings, hooks and MCP servers),
    # ci_pipeline (workflow files) or git_hook. scope: home (the user's own
    # ~/.claude or ~/.cursor) or project (a repository's copy).
    FACTS = ("file_role", "scope")

    def match(self, call: ToolCall) -> MatchResult | None:
        for path, command in _changed_paths(call):
            kind = _control_kind(path)
            if kind:
                return self.hit(target=path, evidence=command.raw if command else path, command=command,
                                file_role=kind, scope=_control_scope(path))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        words = _CONTROL_KIND_WORDS.get(result.facts.get("file_role"), "control file")
        name = posixpath.basename(str(result.target or "").replace("\\", "/"))
        return f"change {words} {name}".rstrip()


@register_matcher
class PermissionChangeMatcher(BaseMatcher):
    OPERATION = "fs.permission"
    DOMAIN = "fs"
    DESCRIPTION = "Changes file permissions or ownership"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    FACTS = ("world_writable", "recursive")

    _COMMANDS = frozenset({"chmod", "chown", "chgrp", "setfacl", "icacls", "takeown", "attrib"})
    # World-writable or fully-open modes, the cases actually worth surfacing.
    _OPEN_MODES = frozenset({"777", "0777", "666", "0666", "a+w", "a+rwx", "o+w"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._COMMANDS):
            operands = list(command.subcommand_path)
            world_writable = any(o in self._OPEN_MODES for o in operands)
            target = next(
                (resolve_path(o, call.cwd) for o in operands if "/" in o or "\\" in o),
                None,
            )
            return self.hit(
                target=target,
                command=command,
                world_writable=world_writable,
                recursive=command.has_recursive_flag,
            )
        return None
