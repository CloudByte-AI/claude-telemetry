"""
Git Branch Resolution

Single source of truth for the `USER_PROMPT.git_branch` value, shared by the
Claude Code path (JSONL sync in src/main.py, recovery in src/core/recovery.py)
and the Cursor path (src/cursor/handlers/before_submit_prompt.py).

Why this exists
---------------
Claude Code stamps a `gitBranch` field on every JSONL record, and we used to
store it verbatim. When the session cwd is NOT a git repository, Claude Code
emits the literal string "HEAD" - not null, not an empty string - so the
dashboard shows a branch named "HEAD" that does not exist anywhere. In this
machine's local DB that accounted for the single largest git_branch bucket,
and every one of those rows came from a non-repo cwd
(e.g. C:/Users/rajpa/PMS/claude-mem-central, whose actual git repo lives one
level down in claude-mem-central/claude-mem-central).

The same literal "HEAD" is also what `git rev-parse --abbrev-ref HEAD` prints
in a genuine detached-HEAD checkout, so the Cursor path - which derives the
branch itself - had the same latent hole. Both now go through resolve().

Resolution order (resolve_git_branch)
-------------------------------------
1. A reported value that is a real branch name is trusted as-is (the JSONL
   value is captured at prompt time, which is more accurate than anything we
   can measure later).
2. A missing / empty / literal-"HEAD" reported value is discarded and we
   derive the branch ourselves from cwd.
3. Derivation, in order:
     a. `git -C cwd rev-parse --abbrev-ref HEAD`  -> normal branch
     b. `git -C cwd symbolic-ref --short HEAD`    -> unborn branch (fresh repo
        with no commits, where (a) exits non-zero)
     c. detached HEAD -> `git describe --all --exact-match` (tag/ref name),
        else "detached@<short sha>" - never the bare string "HEAD"
     d. cwd is not a repo -> look one level down for exactly ONE immediate
        child directory that is a repo and use that. Exactly one, so an
        ambiguous multi-repo parent resolves to nothing rather than a guess.
     e. nothing found -> None (stored as NULL, which is honest)

Never raises: every failure path returns None so a prompt is never lost over
a branch lookup.
"""

import os
import subprocess
import time
from typing import Optional

from src.common.logging import get_logger


logger = get_logger(__name__)

# Values a caller may report that carry no branch information. "HEAD" is what
# Claude Code writes for a non-repo cwd and what git prints when detached.
UNUSABLE_REPORTED = {"", "head", "(no branch)", "unknown", "null", "none"}

GIT_TIMEOUT_SECONDS = 3

# Directories never worth descending into when looking for a nested repo.
_SKIP_CHILD_DIRS = {
    "node_modules", "venv", ".venv", "env", "__pycache__",
    "dist", "build", "target", "vendor", "site-packages",
}

# Process-level memo so a worker replaying hundreds of prompts for one session
# does not shell out to git hundreds of times. Short TTL so a branch switch
# during a live session is still picked up.
_CACHE_TTL_SECONDS = 10
_cache: dict[str, tuple[float, Optional[str]]] = {}


def _run_git(cwd: str, *args: str) -> Optional[str]:
    """Run a git command in cwd. Returns stripped stdout, or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except Exception as e:
        logger.debug(f"git {' '.join(args)} failed in {cwd}: {e}")
        return None

    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _branch_at(path: str) -> Optional[str]:
    """
    Resolve the branch of the repo containing `path` (git walks up on its own).

    Returns None if `path` is not inside a git repository. Never returns the
    bare string "HEAD" - a detached checkout resolves to a tag/ref name or to
    "detached@<short sha>".
    """
    branch = _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")

    if branch is None:
        # Either not a repo, or a repo with no commits yet (unborn HEAD), where
        # rev-parse exits non-zero but symbolic-ref still knows the branch name.
        unborn = _run_git(path, "symbolic-ref", "--short", "HEAD")
        if unborn:
            logger.debug(f"git branch for {path}: unborn branch {unborn!r}")
        return unborn

    if branch != "HEAD":
        return branch

    # Detached HEAD. Prefer a human-meaningful ref name, else pin the commit.
    described = _run_git(path, "describe", "--all", "--exact-match", "HEAD")
    if described:
        # describe --all prefixes with the ref namespace: "tags/v1.2",
        # "heads/main", "remotes/origin/main". Only "heads/" is noise.
        if described.startswith("heads/"):
            described = described[len("heads/"):]
        logger.debug(f"git branch for {path}: detached at ref {described!r}")
        return described

    sha = _run_git(path, "rev-parse", "--short", "HEAD")
    if sha:
        logger.debug(f"git branch for {path}: detached at {sha}")
        return f"detached@{sha}"

    return None


def _branch_in_single_child_repo(cwd: str) -> Optional[str]:
    """
    cwd is not a repo. If exactly one immediate child directory is a git repo,
    return its branch - this is the common "wrapper folder around the checkout"
    layout (PMS/claude-mem-central/claude-mem-central). Ambiguity returns None.
    """
    try:
        entries = sorted(os.scandir(cwd), key=lambda e: e.name)
    except Exception as e:
        logger.debug(f"cannot scan {cwd} for a nested repo: {e}")
        return None

    repo_children = []
    for entry in entries:
        try:
            if not entry.is_dir(follow_symlinks=False):
                continue
        except OSError:
            continue
        name = entry.name
        if name.startswith(".") or name.lower() in _SKIP_CHILD_DIRS:
            continue
        # .git is a directory in a normal clone, a file in a worktree/submodule.
        if os.path.exists(os.path.join(entry.path, ".git")):
            repo_children.append(entry.path)
            if len(repo_children) > 1:
                logger.debug(f"{cwd} has multiple child repos - branch left unresolved")
                return None

    if not repo_children:
        return None

    branch = _branch_at(repo_children[0])
    if branch:
        logger.debug(f"git branch for {cwd} resolved from child repo {repo_children[0]}: {branch!r}")
    return branch


def detect_git_branch(cwd: Optional[str], use_cache: bool = True) -> Optional[str]:
    """
    Derive the git branch for `cwd` from the filesystem. Returns None when no
    branch can be determined. Never returns the bare string "HEAD".
    """
    if not cwd:
        return None

    try:
        key = os.path.normcase(os.path.abspath(cwd))
    except Exception:
        key = cwd

    if use_cache:
        hit = _cache.get(key)
        if hit and (time.time() - hit[0]) < _CACHE_TTL_SECONDS:
            return hit[1]

    branch: Optional[str] = None
    if os.path.isdir(cwd):
        branch = _branch_at(cwd) or _branch_in_single_child_repo(cwd)
    else:
        logger.debug(f"git branch lookup skipped - not a directory: {cwd}")

    if use_cache:
        _cache[key] = (time.time(), branch)
    return branch


def resolve_git_branch(
    cwd: Optional[str],
    reported: Optional[str] = None,
    use_cache: bool = True,
) -> Optional[str]:
    """
    Return the branch to store on a USER_PROMPT row.

    `reported` is whatever the client handed us (Claude Code's JSONL
    `gitBranch`, or None for Cursor, which reports nothing). A usable reported
    value wins; "HEAD"/empty/None falls back to detecting from `cwd`.
    """
    if reported is not None:
        candidate = reported.strip()
        if candidate and candidate.lower() not in UNUSABLE_REPORTED:
            return candidate

    return detect_git_branch(cwd, use_cache=use_cache)


def clear_cache() -> None:
    """Drop the memoized branch lookups (tests, and long-lived worker restarts)."""
    _cache.clear()
