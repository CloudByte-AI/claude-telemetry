"""
Lean entry point for the pre-execution hooks (the hot path).

Not inside src/guardrails/, which must never import a platform adapter or
branch on a platform, and not in src/main.py, whose module-level imports are
too heavy for a hook that fires on every governed tool call. Imports nothing
but stdlib until it knows which platform is calling, and fails closed if the
guardrails code cannot be imported (_check_failed).

    python -m src.hook_entry claude
    python -m src.hook_entry claude post_tool_use
    python -m src.hook_entry cursor before_shell_execution

`claude post_tool_use` is the one post-execution hook here: it opens the window
of a remembered approval once an asked-about tool has run (approvals.py), and
never fails closed, since it decides nothing.
"""

import os
import sys

USAGE = "Usage: python -m src.hook_entry <claude|cursor> [hook_name]"
# `claude post_tool_use`: shown asks that ran (src/guardrails/asks.py) and
# remembered approvals (src/guardrails/approvals.py).
POST_TOOL_USE = "post_tool_use"

# Duplicated from src.guardrails.config, which would pull in the engine;
# test_contracts.py asserts the copies stay equal.
_KILL_SWITCH_ENV = "CLOUDBYTE_GUARDRAILS_DISABLED"
_GUARDRAILS_RELATIVE_DIR = (".cloudbyte", "guardrails")
# The global profile (config.PROFILE_FILENAME), then its legacy name.
_PROFILE_FILENAMES = ("global_profile.yaml", "guardrails_profile.yaml")
_WORKSPACES_DIRNAME = "workspaces"
# approvals.py's pending notes, under the guardrails directory; asserted equal
# to approvals.py's own names in test_approvals.py.
_PENDING_APPROVALS = ("approvals", "pending")
# asks.py's markers of shown asks, under the guardrails directory; asserted
# equal to asks.py's own names in test_asks.py.
_SHOWN_ASKS = ("asks",)
_SHOWN_SUFFIX = ".shown"
_TRUTHY = ("1", "true", "yes", "on")

# "No opinion" per platform. Claude Code takes `{}` and applies the user's
# normal permission flow. Cursor blocks on a response that does not match the
# hook schema whatever failClosed says, so it gets an explicit allow.
_NO_OPINION = {"claude": "{}", "cursor": '{"permission": "allow"}'}

# The answer when the guardrails code cannot be imported: what the adapters
# render for Decision.evaluation_failed(), kept here because this path runs
# exactly when those modules are unusable. test_contracts.py asserts the copies
# stay equal.
_CHECK_FAILED_DETAIL = (
    "the check could not run because of an internal error - details are in the CloudByte log"
)
_CHECK_FAILED = {
    "claude": {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": f"CloudByte guardrails: {_CHECK_FAILED_DETAIL}",
        }
    },
    "cursor": {
        "permission": "allow",
        "agent_message": (
            f"CloudByte guardrails: this action needs approval ({_CHECK_FAILED_DETAIL}), but "
            f"Cursor cannot show an approval prompt for it, so it runs without review. "
            f"Tell the user what ran."
        ),
    },
}


def _is_inactive() -> bool:
    """
    True when guardrails cannot possibly have an opinion, decided using nothing
    but `os`. The feature ships disabled (no profile), so this is the usual path
    and it avoids importing the engine.
    """
    if os.environ.get(_KILL_SWITCH_ENV, "").strip().lower() in _TRUTHY:
        return True
    directory = os.path.join(os.path.expanduser("~"), *_GUARDRAILS_RELATIVE_DIR)
    if any(os.path.exists(os.path.join(directory, name)) for name in _PROFILE_FILENAMES):
        return False
    # A workspace policy can be on with no global profile at all.
    return not os.path.isdir(os.path.join(directory, _WORKSPACES_DIRNAME))


def main(argv: list[str]) -> int:
    if not argv:
        print(USAGE, file=sys.stderr)
        return 1

    platform = argv[0].lower()
    if platform == "claude_code":
        platform = "claude"

    if platform not in _NO_OPINION:
        print(f"Unknown platform '{platform}'. {USAGE}", file=sys.stderr)
        return 1

    if platform == "claude" and len(argv) > 1 and argv[1] == POST_TOOL_USE:
        return _post_tool_use()

    if _is_inactive():
        print(_NO_OPINION[platform])
        return 0

    try:
        if platform == "claude":
            from src.handlers.pre_tool_use import handle_pre_tool_use
        else:
            from src.common.install_owner import CURSOR, foreign_owner, stand_down_notice
            from src.cursor.handlers.guardrails import dispatch
    except Exception as exc:
        return _check_failed(platform, exc)

    if platform == "claude":
        return handle_pre_tool_use()

    # A copy that is another client's install (Cursor also runs our hooks from
    # the Claude Code install) still decides - its answer can only add a deny,
    # never cancel one - but leaves the audit row to Cursor's own install, so
    # protection holds even where only the Claude Code install exists.
    owner = foreign_owner(CURSOR)
    if owner is not None:
        print(stand_down_notice(owner, CURSOR) + " (guardrails still checked, not recorded)",
              file=sys.stderr)

    return dispatch(argv[1] if len(argv) > 1 else "", record=owner is None)


def _has_entries(parts: tuple, suffix: str) -> bool:
    directory = os.path.join(os.path.expanduser("~"), *_GUARDRAILS_RELATIVE_DIR, *parts)
    try:
        with os.scandir(directory) as entries:
            return any(entry.name.endswith(suffix) for entry in entries)
    except OSError:
        return False


def _has_pending_approvals() -> bool:
    """
    Whether any ask is waiting to be remembered (approvals.py's pending notes),
    using nothing but `os`. PostToolUse fires for every governed tool call and
    almost never has a note to act on, so this keeps that path to a directory
    listing.
    """
    return _has_entries(_PENDING_APPROVALS, ".json")


def _has_shown_asks() -> bool:
    """Whether any shown ask is waiting for its tool to run (asks.py), with `os` alone."""
    return _has_entries(_SHOWN_ASKS, _SHOWN_SUFFIX)


def _drain_stdin() -> None:
    """Read and discard the hook input, so the writer never blocks on a full pipe."""
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass


def _post_tool_use() -> int:
    """
    Claude Code PostToolUse: record that an asked-about call ran, and turn an
    approved ask into an approval window (src/handlers/post_tool_use.py). It
    decides nothing, so unlike PreToolUse it never fails closed: whatever goes
    wrong, it prints nothing and exits 0.
    """
    if _is_inactive() or not (_has_shown_asks() or _has_pending_approvals()):
        _drain_stdin()
        return 0
    try:
        from src.handlers.post_tool_use import handle_post_tool_use
    except Exception:
        _drain_stdin()
        return 0
    return handle_post_tool_use()


def _check_failed(platform: str, exc: Exception) -> int:
    """
    The guardrails code could not be imported. Fail closed with the platform's
    "could not check" answer, which needs nothing that just failed to load.
    """
    import json

    try:
        sys.stdin.buffer.read()     # a writer blocked on a full pipe must not wait on us
    except Exception:
        pass
    print(f"CloudByte guardrails: could not load the guardrails code, so this call was "
          f"not checked ({exc!r})", file=sys.stderr)
    print(json.dumps(_CHECK_FAILED[platform]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
