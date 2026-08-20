"""
Git helpers shared by the Claude Code and Cursor hook paths.

Neither client's hook payload carries the current branch - Claude Code's docs
mention the branch only as an example of context a hook author is expected to
compute - so we derive it ourselves.

This reads .git/HEAD directly instead of shelling out to
`git rev-parse --abbrev-ref HEAD`. Measured on this repo, the file read is
~0.4ms against ~26ms for the subprocess, which matters because UserPromptSubmit
blocks model processing until every hook returns. It is also more useful on a
detached HEAD, where `rev-parse --abbrev-ref` returns the literal string "HEAD"
and this returns the short commit sha.
"""

import os
from pathlib import Path
from typing import Optional

from src.common.logging import get_logger


logger = get_logger(__name__)

_REF_PREFIX = "ref: refs/heads/"


def _read_head(git_path: Path) -> Optional[str]:
    """
    Return the raw contents of HEAD for a .git directory or .git file.

    A .git *file* appears in worktrees and submodules and contains
    "gitdir: <path>". That path is absolute for worktrees but relative for
    submodules, so a relative one is resolved against the directory the .git
    file lives in - resolving it against the process cwd instead would silently
    look in the wrong place.
    """
    try:
        if git_path.is_dir():
            return (git_path / "HEAD").read_text(encoding="utf-8").strip()

        if git_path.is_file():
            content = git_path.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir:"):
                return None
            raw = content[len("gitdir:"):].strip()
            gitdir = Path(raw)
            if not gitdir.is_absolute():
                gitdir = (git_path.parent / gitdir).resolve()
            return (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None

    return None


def get_git_branch(cwd: Optional[str]) -> Optional[str]:
    """
    Best-effort current branch for cwd. Returns None when cwd is not in a
    git repository, or on any read error - the branch is telemetry metadata
    and must never break a hook.

    Walks upward from cwd so a session working inside a subdirectory still
    resolves the enclosing repository, matching what git itself does.

    Returns:
        Branch name on a normal checkout, the 12-character short sha when HEAD
        is detached, or None.
    """
    if not cwd:
        return None

    try:
        # GIT_DIR wins over directory discovery when it is set, same as git.
        env_git_dir = os.environ.get("GIT_DIR")
        if env_git_dir:
            head = _read_head(Path(env_git_dir))
        else:
            head = None
            start = Path(cwd).resolve()
            for parent in (start, *start.parents):
                head = _read_head(parent / ".git")
                if head:
                    break

        if not head:
            return None

        if head.startswith(_REF_PREFIX):
            branch = head[len(_REF_PREFIX):].strip()
            return branch or None

        # Detached HEAD - HEAD holds a raw sha. Short form keeps the column
        # readable and still identifies the commit.
        if len(head) >= 12 and all(c in "0123456789abcdef" for c in head.lower()):
            return head[:12]

        return None

    except Exception as e:
        logger.debug(f"git branch lookup failed for {cwd}: {e}")
        return None
