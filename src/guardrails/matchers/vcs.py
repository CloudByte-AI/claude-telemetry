"""
Version-control matchers.

These destroy work that is not recoverable from the working tree.

All three read `subcommand_path`, never the raw string, so
`git --no-pager push --force` and `git push origin main --force` are the same
operation - flag position must not decide whether a guardrail fires.
"""

from __future__ import annotations

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
