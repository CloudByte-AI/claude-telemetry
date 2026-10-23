"""
Version-control matchers.

These destroy work that is not recoverable from the working tree.

All three read `subcommand_path`, never the raw string, so
`git --no-pager push --force` and `git push origin main --force` are the same
operation - flag position must not decide whether a guardrail fires.
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall

_GIT = frozenset({"git", "hub", "gh"})
_FORCE_FLAGS = frozenset({"-f", "--force", "--force-with-lease", "--force-if-includes"})
# git's global options that take a value, which the parser cannot tell from the
# subcommand: `git -C /repo push` must still be a push, and `git -c x=y rebase`
# a rebase.
_GIT_VALUE_OPTIONS = frozenset({
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
    "-R", "--repo",
})


@register_matcher
class ForcePushMatcher(BaseMatcher):
    OPERATION = "vcs.force_push"
    DOMAIN = "vcs"
    DESCRIPTION = "Force-pushes, overwriting remote history"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("with_lease", "remote")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, _GIT):
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
            if not self.starts_with_run(sub, ("push",)):
                continue
            lowered = {f.lower() for f in command.flags}
            # A `+refspec` is a force push of that ref.
            plus_ref = any(o.startswith("+") and len(o) > 1 for o in sub[1:])
            if not (lowered & _FORCE_FLAGS or plus_ref):
                continue
            # --force-with-lease is the safe form: it refuses when the remote
            # moved. Reported as a fact so a profile can allow it while still
            # asking about a bare --force.
            leased = "--force-with-lease" in lowered and not ({"-f", "--force"} & lowered or plus_ref)
            remote = sub[1] if len(sub) > 1 else None
            return self.hit(
                target=remote,
                command=command,
                with_lease=leased,
                remote=remote,
            )
        return None


@register_matcher
class HistoryRewriteMatcher(BaseMatcher):
    OPERATION = "vcs.history.rewrite"
    DOMAIN = "vcs"
    DESCRIPTION = "Rewrites version-control history, discarding commits"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("operation_kind",)

    # (subcommand, whether a flag is also required to make it destructive).
    # `restore --staged` only unstages, so it is not here; `--source` throws
    # the working copy away for an older version.
    _REWRITES: tuple[tuple[tuple[str, ...], frozenset[str] | None], ...] = (
        (("reset",), frozenset({"--hard"})),
        (("rebase",), None),
        (("filter-branch",), None),
        (("filter-repo",), None),
        (("commit",), frozenset({"--amend"})),
        (("checkout",), frozenset({"--force", "-f"})),
        (("restore",), frozenset({"--source", "-s"})),
    )
    # Steering a rebase already under way rewrites nothing new.
    _REBASE_CONTROL = frozenset({"--abort", "--quit", "--continue", "--skip", "--edit-todo", "--show-current-patch"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, _GIT):
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
            lowered = {f.lower() for f in command.flags}
            for run, required_flags in self._REWRITES:
                if not self.starts_with_run(sub, run):
                    continue
                if required_flags and not (lowered & required_flags):
                    continue
                if run == ("rebase",) and lowered & self._REBASE_CONTROL:
                    continue
                return self.hit(
                    target=run[0],
                    command=command,
                    operation_kind=run[0],
                )
        return None


@register_matcher
class BranchDeleteMatcher(BaseMatcher):
    OPERATION = "vcs.branch.delete"
    DOMAIN = "vcs"
    DESCRIPTION = "Deletes a branch, locally or on a remote"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    FACTS = ("remote", "branch")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, _GIT):
            lowered = {f.lower() for f in command.flags}
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)

            # git branch -d / -D  (note: -D is force, and the parser preserves
            # case on POSIX, so both spellings are checked explicitly)
            if self.starts_with_run(sub, ("branch",)) and (
                {"-d", "-D", "--delete"} & (set(command.flags) | lowered)
            ):
                branch = sub[1] if len(sub) > 1 else None
                return self.hit(target=branch, command=command, remote=False, branch=branch)

            # git push origin --delete <branch>   /   git push origin :<branch>
            if self.starts_with_run(sub, ("push",)):
                if "--delete" in lowered or "-d" in command.flags:
                    branch = sub[-1] if len(sub) > 2 else None
                    return self.hit(target=branch, command=command, remote=True, branch=branch)
                colon_ref = next((o for o in sub[1:] if o.startswith(":") and len(o) > 1), None)
                if colon_ref:
                    return self.hit(
                        target=colon_ref[1:], command=command, remote=True, branch=colon_ref[1:]
                    )
        return None


@register_matcher
class CleanMatcher(BaseMatcher):
    OPERATION = "vcs.clean"
    DOMAIN = "vcs"
    DESCRIPTION = "Discards untracked files or uncommitted changes from the working tree"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    # scope: `untracked` (git clean) or `changes` (checkout/restore over local
    # edits). ignored: git clean -x/-X also removes ignored files (.env, builds).
    FACTS = ("scope", "ignored")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, ("git",)):
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
            flags = set(command.flags)
            lowered = {f.lower() for f in flags}

            # git refuses to clean without -f (clean.requireForce), and -n is a dry run.
            if self.starts_with_run(sub, ("clean",)):
                if {"-n", "--dry-run"} & lowered:
                    continue
                if not ({"-f", "--force", "-i", "--interactive"} & lowered):
                    continue
                return self.hit(target=sub[1] if len(sub) > 1 else ".", command=command,
                                scope="untracked", ignored=bool({"-x", "-X"} & flags))

            # git checkout -- <path> / git checkout . : overwrite local edits.
            # (git checkout <branch> switches branches; -b creates one;
            # --ours/--theirs pick a side of a merge conflict.)
            if self.starts_with_run(sub, ("checkout",)) and not (
                {"-b", "-B", "--orphan", "-p", "--patch", "--ours", "--theirs", "--merge", "-m"} & flags
            ):
                if "--" in command.argv or "." in sub[1:]:
                    paths = sub[1:] if "--" not in command.argv else command.argv[command.argv.index("--") + 1:]
                    return self.hit(target=paths[-1] if paths else ".", command=command,
                                    scope="changes", ignored=False)

            # git restore <path>: the working tree, unless only --staged.
            if self.starts_with_run(sub, ("restore",)) and len(sub) > 1:
                staged_only = bool({"--staged", "-S"} & flags) and not ({"--worktree", "-W"} & flags)
                if staged_only or {"-p", "--patch"} & lowered:
                    continue
                return self.hit(target=sub[-1], command=command, scope="changes", ignored=False)
        return None


# `credential.helper`, `credential.https://host.helper`, `credential.<url>.helper`.
_CREDENTIAL_HELPER_KEY = re.compile(r"^credential\.(?:.+\.)?helper$", re.IGNORECASE)
# git config options that read or remove a value instead of setting one.
_CONFIG_READS = frozenset({
    "--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l", "--unset",
    "--unset-all", "--show-origin", "--show-scope", "--name-only", "--remove-section",
    "--rename-section", "-e", "--edit",
})
_CONFIG_READ_ACTIONS = frozenset({"get", "list", "unset", "edit", "rename-section", "remove-section"})
# A URL carrying a password: `https://user:token@host/...`.
_URL_WITH_SECRET = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@")


@register_matcher
class CredentialStoreMatcher(BaseMatcher):
    OPERATION = "vcs.credential.store"
    DOMAIN = "vcs"
    DESCRIPTION = "Stores version-control credentials on disk"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # method: `helper` (sets a credential helper), `approve` (hands git a
    # credential to keep), `url` (a password inside a remote URL, which git
    # writes to .git/config). helper: the helper named, when there is one.
    FACTS = ("method", "helper")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, ("git",)):
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
            if self.starts_with_run(sub, ("config",)):
                hit = self._config_helper(command, sub)
                if hit:
                    return hit
            if self.starts_with_run(sub, ("credential", "approve")) or (
                sub and sub[0].startswith("credential-") and "store" in sub[1:]
            ):
                return self.hit(target=sub[0], command=command, method="approve", helper=None)
            if (self.starts_with_run(sub, ("remote", "add")) or self.starts_with_run(sub, ("remote", "set-url"))
                    or self.starts_with_run(sub, ("clone",))):
                url = next((o for o in sub[1:] if _URL_WITH_SECRET.match(o)), None)
                if url:
                    return self.hit(target=_URL_WITH_SECRET.match(url).group(0).rsplit(":", 1)[0] + ":***@",
                                    command=command, method="url", helper=None)
        # `gh auth setup-git` makes gh git's credential helper.
        for command in self.commands_named(call, ("gh",)):
            if self.starts_with_run(command.subcommand_path, ("auth", "setup-git")):
                return self.hit(target="gh", command=command, method="helper", helper="gh")
        return None

    def _config_helper(self, command, sub) -> MatchResult | None:
        if {f.lower() for f in command.flags} & _CONFIG_READS:
            return None
        rest = list(sub[1:])
        if rest and rest[0] == "set":                     # git config set <key> <value>
            rest = rest[1:]
        elif rest and rest[0] in _CONFIG_READ_ACTIONS:
            return None
        if len(rest) < 2 or not _CREDENTIAL_HELPER_KEY.match(rest[0]):
            return None                                   # `git config credential.helper` reads it
        helper = rest[1].strip()
        if not helper:
            return None                                   # an empty helper turns storing off
        return self.hit(target=rest[0], command=command, method="helper", helper=helper.split()[0])
