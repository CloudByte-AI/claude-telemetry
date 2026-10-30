"""
Version-control matchers.

These destroy work that is not recoverable from the working tree.

All three read `subcommand_path`, never the raw string, so
`git --no-pager push --force` and `git push origin main --force` are the same
operation - flag position must not decide whether a guardrail fires.
"""

from __future__ import annotations

import re
from dataclasses import replace

from src.guardrails.matchers.base import (
    BaseMatcher,
    MatchResult,
    asks_for_help,
    is_sensitive_path,
    option_of,
    summary_path,
    summary_value,
    without_redirects,
)
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_MCP, KIND_SHELL, ToolCall

_GIT = frozenset({"git", "hub", "gh"})
_FORCE_FLAGS = frozenset({"-f", "--force", "--force-with-lease", "--force-if-includes"})
# `--mirror` force-updates and deletes remote refs to match the local ones.
_OVERWRITE_FLAGS = _FORCE_FLAGS | {"--mirror"}
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

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        remote = result.facts.get("remote") or "the remote"
        lease = " (with lease)" if result.facts.get("with_lease") else ""
        branch = None
        if result.command is not None:
            sub = cls.operands_from_subcommand(result.command, _GIT_VALUE_OPTIONS)
            refspecs = PushMatcher._positional(result.command, sub[1:])[1:]
            branch = next((b for b in (_destination(r) for r in refspecs) if b), None)
        return f"force-push to {branch} on {remote}{lease}" if branch else f"force-push to {remote}{lease}"

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, _GIT):
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
            if not self.starts_with_run(sub, ("push",)):
                continue
            lowered = {f.lower() for f in command.flags}
            # A `+refspec` is a force push of that ref.
            plus_ref = any(o.startswith("+") and len(o) > 1 for o in sub[1:])
            if not (lowered & _OVERWRITE_FLAGS or plus_ref):
                continue
            # --force-with-lease is the safe form: it refuses when the remote
            # moved. Reported as a fact so a profile can allow it while still
            # asking about a bare --force.
            leased = "--force-with-lease" in lowered and not ({"-f", "--force"} & lowered or plus_ref)
            positional = PushMatcher._positional(command, sub[1:])
            remote = positional[0] if positional else None
            return self.hit(
                target=remote,
                command=command,
                with_lease=leased,
                remote=remote,
            )
        return None


# `git push` options that take a value, whose value is not a remote or refspec.
_PUSH_VALUE_OPTIONS = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
# Tools on a git-hosting MCP server that change a branch on the remote.
_MCP_PUSH_TOOLS = frozenset({
    "merge_pull_request", "merge_pr", "merge_merge_request", "push_files", "create_or_update_file",
})


def _destination(refspec: str) -> str | None:
    """The branch a refspec writes: `src:dst` -> dst, `+main` -> main, `refs/heads/x` -> x."""
    ref = refspec.lstrip("+")
    if ":" in ref:
        ref = ref.rsplit(":", 1)[1]
    if not ref or ref == "HEAD":
        return None
    return ref.removeprefix("refs/heads/")


@register_matcher
class PushMatcher(BaseMatcher):
    OPERATION = "vcs.push"
    DOMAIN = "vcs"
    DESCRIPTION = "Pushes commits to a remote, or merges into a remote branch"
    APPLIES_TO = (KIND_SHELL, KIND_MCP)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    # Every push, force and delete pushes included, so one rule can cover "any
    # push to main"; vcs.force_push and vcs.branch.delete still fire as well.
    # branch: the destination of the first refspec; branch_implicit: no branch
    # is named, so git pushes the current one (or a merge goes to the PR's
    # base). via: push, pr_merge (gh pr merge, glab mr merge) or mcp.
    FACTS = ("remote", "branch", "branch_implicit", "refspec_count", "set_upstream", "tags",
             "all_refs", "force", "delete", "dry_run", "via", "admin")

    def match(self, call: ToolCall) -> MatchResult | None:
        if call.mcp_tool:
            if call.mcp_tool.lower() in _MCP_PUSH_TOOLS:
                return self.hit(target=call.tool_name, evidence=call.tool_name, remote=None, branch=None,
                                branch_implicit=True, refspec_count=0, set_upstream=False, tags=False,
                                all_refs=False, force=False, delete=False, dry_run=False, via="mcp", admin=False)
            return None
        for command in self.commands_named(call, _GIT):
            if command.command_name == "gh":
                found = self._pr_merge(command)
                if found:
                    return found
                continue
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
            if not self.starts_with_run(sub, ("push",)):
                continue
            positional = self._positional(command, sub[1:])
            remote = positional[0] if positional else None
            refspecs = positional[1:]
            lowered = {f.lower() for f in command.flags}
            branch = next((b for b in (_destination(r) for r in refspecs) if b), None)
            tags = bool({"--tags", "--follow-tags"} & lowered) or any(r.startswith("refs/tags/") for r in refspecs)
            delete = "--delete" in lowered or "-d" in command.flags or any(
                r.startswith(":") and len(r) > 1 for r in refspecs)
            if delete and branch is None and refspecs:
                branch = refspecs[-1].lstrip(":")
            return self.hit(
                target=branch or remote,
                command=command,
                remote=remote,
                branch=branch,
                branch_implicit=branch is None and not tags,
                refspec_count=len(refspecs),
                set_upstream=bool({"-u", "--set-upstream"} & lowered),
                tags=tags,
                all_refs=bool({"--all", "--mirror", "--branches"} & lowered),
                force=bool(lowered & _OVERWRITE_FLAGS) or any(r.startswith("+") and len(r) > 1 for r in refspecs),
                delete=delete,
                dry_run=bool({"-n", "--dry-run"} & lowered),
                via="push",
                admin=False,
            )
        for command in self.commands_named(call, ("glab",)):
            found = self._pr_merge(command)
            if found:
                return found
        return None

    @staticmethod
    def _positional(command, operands) -> list[str]:
        """
        Remote and refspecs: the operands after `push`, minus the values of
        push's own options, separate or glued (`--force-with-lease=main:abc`).
        A `--repo` value is the remote.
        """
        values: list[str] = []
        repo = None
        argv = command.argv
        for i, token in enumerate(argv):
            option, glued, value = token.partition("=")
            if token in _PUSH_VALUE_OPTIONS and i + 1 < len(argv):
                values.append(argv[i + 1])
                repo = argv[i + 1] if token == "--repo" else repo
            elif token.startswith("--") and glued:
                values.append(value)
                repo = value if option == "--repo" else repo
        positional = list(operands)
        for value in values:
            if value in positional:
                positional.remove(value)
        return ([repo] if repo else []) + positional

    def _pr_merge(self, command) -> MatchResult | None:
        """`gh pr merge 12`, `glab mr merge 7`: the remote merges into the base branch."""
        sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
        noun = "pr" if command.command_name == "gh" else "mr"
        if not self.starts_with_run(sub, (noun, "merge")):
            return None
        lowered = {f.lower() for f in command.flags}
        if asks_for_help(command):
            return None
        number = sub[2] if len(sub) > 2 else None
        return self.hit(target=number or "pull request", command=command, remote=None, branch=None,
                        branch_implicit=True, refspec_count=0, set_upstream=False, tags=False, all_refs=False,
                        force=False, delete=False, dry_run=False, via="pr_merge", admin="--admin" in lowered)

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        via = facts.get("via")
        if via == "pr_merge":
            number = result.target if result.target and result.target != "pull request" else ""
            return f"merge pull request {number}".rstrip() + (" (bypassing branch protection)" if facts.get("admin") else "")
        if via == "mcp":
            return f"merge or push through {str(result.target or 'an MCP tool').rsplit('__', 1)[-1]}"
        remote = facts.get("remote") or "the remote"
        branch = facts.get("branch")
        if facts.get("delete"):
            return f"delete branch {branch} on {remote}" if branch else f"delete a branch on {remote}"
        verb = "force-push" if facts.get("force") else "push"
        if facts.get("all_refs"):
            return f"{verb} every branch to {remote}"
        if facts.get("tags") and not branch:
            return f"{verb} tags to {remote}"
        if branch:
            return f"{verb} to {branch} on {remote}"
        return f"{verb} the current branch to {remote}"


@register_matcher
class HistoryRewriteMatcher(BaseMatcher):
    OPERATION = "vcs.history.rewrite"
    DOMAIN = "vcs"
    DESCRIPTION = "Rewrites version-control history, discarding commits"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("operation_kind",)

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        kind = result.facts.get("operation_kind")
        operands = cls.operands_from_subcommand(result.command, _GIT_VALUE_OPTIONS) if result.command else ()
        ref = summary_value(next((o for o in operands[1:] if not o.startswith("-") and o != "--"), None))
        if kind == "reset":
            if ref and ref.upper() != "HEAD":
                return f"hard-reset to {ref}, discarding later commits and uncommitted changes"
            return "discard all uncommitted changes (git reset --hard)"
        if kind == "rebase":
            return f"rebase onto {ref}, rewriting this branch's commits" if ref else "rebase, rewriting this branch's commits"
        if kind == "commit":
            return "amend the last commit"
        if kind in ("filter-branch", "filter-repo"):
            return f"rewrite the whole repository history (git {kind})"
        if kind == "checkout":
            return f"force-checkout {ref}, discarding local changes" if ref else "force-checkout, discarding local changes"
        if kind == "restore":
            source = option_of(result.command, "--source", "-s")
            return f"restore files from {source}, discarding local changes" if source \
                else "restore files, discarding local changes"
        return f"rewrite git history (git {kind})"

    # (subcommand, whether a flag is also required to make it destructive).
    # `restore --staged` only unstages, so it is not here; `--source` throws
    # the working copy away for an older version.
    _REWRITES: tuple[tuple[tuple[str, ...], frozenset[str] | None], ...] = (
        (("reset",), frozenset({"--hard"})),
        (("rebase",), None),
        (("filter-branch",), None),
        (("filter-repo",), None),
        (("commit",), frozenset({"--amend"})),
        # `checkout -f -B <branch> <start>` resets an existing branch to another
        # commit (see match). `restore --source` and a bare `checkout -f` change
        # only the working tree: those are vcs.clean.
        (("checkout",), None),
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
                # Forced reset of a branch: -f with -B (case matters: -b creates).
                if run == ("checkout",) and not ("-B" in command.flags and lowered & {"-f", "--force"}):
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

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        branch = result.facts.get("branch") or "a branch"
        return f"delete branch {branch}" + (" on the remote" if result.facts.get("remote") else "")

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

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        if result.facts.get("scope") == "untracked":
            return "delete untracked files" + (" including ignored ones" if result.facts.get("ignored") else "")
        return f"discard uncommitted changes in {result.target or 'the working tree'}"

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
                    argv = without_redirects(command.argv)
                    paths = sub[1:] if "--" not in argv else argv[argv.index("--") + 1:]
                    return self.hit(target=paths[-1] if paths else ".", command=command,
                                    scope="changes", ignored=False)
                # `checkout -f <branch>` switches and throws away local edits.
                if {"-f", "--force"} & lowered:
                    return self.hit(target=".", command=command, scope="changes", ignored=False)

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


# `git add` arguments that stage every change in the tree, not chosen files.
_STAGE_ALL_FLAGS = frozenset({"-A", "--all", "-u", "--update"})
_STAGE_ALL_PATHS = frozenset({".", ":/", "*", "./", ":/."})


@register_matcher
class CommitMatcher(BaseMatcher):
    OPERATION = "vcs.commit"
    DOMAIN = "vcs"
    DESCRIPTION = "Stages or commits changes"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    # action: stage (git add) or commit. stages_all: every change in the tree is
    # included, not chosen files (`git add -A`, `git add .`, `git commit -a`),
    # which is how unrelated or teammates' files end up in a commit. amend:
    # `commit --amend`. no_verify: the commit skips the repository's hooks.
    # stages_sensitive: `git add` names a credential or key file (`.env`,
    # `id_rsa`, `*.pem`), so its secrets are about to enter the history.
    FACTS = ("action", "stages_all", "amend", "no_verify", "stages_sensitive")

    def match(self, call: ToolCall) -> MatchResult | None:
        hits = []
        for command in self.commands_named(call, ("git",)):
            if asks_for_help(command):
                continue
            sub = self.operands_from_subcommand(command, _GIT_VALUE_OPTIONS)
            flags = set(command.flags)
            if self.starts_with_run(sub, ("add",)):
                if flags & {"-n", "--dry-run"}:
                    continue
                # `-A` / `-u` take in the whole tree only without paths: `git add -A
                # -- src/a.ts` stages the changes under the paths it names.
                paths = [o for o in sub[1:] if o != "--"]
                chosen = [o for o in paths if o not in _STAGE_ALL_PATHS]
                # -N / --intent-to-add records that paths exist but stages no content.
                stages_all = not (flags & {"-N", "--intent-to-add"}) and (
                    (bool(flags & _STAGE_ALL_FLAGS) and not chosen) or any(o in _STAGE_ALL_PATHS for o in paths))
                sensitive = next((o for o in chosen if is_sensitive_path(o)), None)
                hits.append(self.hit(target=sensitive or ("all changes" if stages_all else (sub[1] if len(sub) > 1 else None)),
                                     command=command, action="stage", stages_all=stages_all, amend=False,
                                     no_verify=False, stages_sensitive=sensitive is not None))
            elif self.starts_with_run(sub, ("commit",)):
                if "--dry-run" in flags:
                    continue
                hits.append(self.hit(target="commit", command=command, action="commit",
                                     stages_all=bool(flags & {"-a", "--all"}), amend="--amend" in flags,
                                     no_verify=bool(flags & {"-n", "--no-verify"}), stages_sensitive=False))
        if not hits:
            return None
        # One call can stage and commit (`git add .env && git commit -m x`): report
        # the step that matters most (a credential file, then every change), and
        # carry both facts for the whole call, so a rule on either fires.
        reported = next((h for h in hits if h.facts["stages_sensitive"]),
                        next((h for h in hits if h.facts["stages_all"]), hits[-1]))
        return replace(reported, facts={
            **reported.facts,
            "stages_all": any(h.facts["stages_all"] for h in hits),
            "stages_sensitive": any(h.facts["stages_sensitive"] for h in hits),
        })

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        if facts.get("action") == "stage":
            if facts.get("stages_sensitive") and result.target:
                return f"stage the credential file {summary_path(result.target)}"
            if facts.get("stages_all"):
                return "stage every changed file"
            files = [o for o in cls.operands_from_subcommand(result.command, _GIT_VALUE_OPTIONS)[1:]
                     if o != "--"] if result.command is not None else []
            if not files:
                return "stage files"
            more = f" and {len(files) - 1} more" if len(files) > 1 else ""
            return f"stage {summary_value(files[0])}{more}"
        text = "amend the last commit" if facts.get("amend") else "commit staged changes"
        if facts.get("stages_all"):
            text = f"{text}, every changed file included"
        return f"{text}, skipping commit hooks" if facts.get("no_verify") else text
