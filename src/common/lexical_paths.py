"""
Lexical path resolution - shared by guardrails and security scanning.

Purely lexical on purpose (no filesystem access): the path often does not
exist yet, and a stat on a network drive can stall a hook's hot path.
Stdlib only, no logger: hook entry points import this before anything else.
"""

from __future__ import annotations

import os
import posixpath
import re

# A drive letter followed by a separator or nothing: `C:/...`, `C:\...`, `C:`.
# `C:foo` (relative to drive C's current directory) is deliberately not matched
# and stays relative - a lexical resolver cannot know that directory.
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:(?=[\\/]|$)")


def split_drive(text: str) -> tuple[str, str]:
    """
    (`C:`, `/rest`) for a drive path, (``, text) otherwise.

    The rest always starts with `/`, so normpath treats a bare `C:` as a root.
    """
    if not WINDOWS_DRIVE.match(text):
        return "", text
    rest = text[2:]
    return text[:2], rest if rest.startswith("/") else "/" + rest


def resolve_path(path: str | None, cwd: str | None) -> str | None:
    """
    Resolve a path for comparison, without touching the filesystem.

    Separators are normalised to `/` so one profile rule works on every
    platform. A drive root resolves to `C:/`, the way the POSIX root resolves
    to `/`, so `C:/..` cannot climb above it.
    """
    if not path:
        return None
    text = str(path).strip().strip('"').strip("'")
    if not text:
        return None

    text = os.path.expanduser(text) if text.startswith("~") else text
    text = text.replace("\\", "/")

    drive, rest = split_drive(text)
    if not drive and not rest.startswith("/") and cwd:
        base = str(cwd).replace("\\", "/").rstrip("/") or "/"
        drive, rest = split_drive(posixpath.join(base, rest))

    # With the drive split off the rest is rooted, so `..` cannot climb above
    # the drive root.
    return drive + posixpath.normpath(rest)
