"""
Lean entry point for the pre-execution hooks (the hot path).

Not inside src/guardrails/, which must never import a platform adapter or
branch on a platform, and not in src/main.py, whose module-level imports are
too heavy for a hook that fires on every governed tool call. Imports nothing
but stdlib until it knows which platform is calling.

    python -m src.hook_entry claude
    python -m src.hook_entry cursor before_shell_execution
"""

import os
import sys

USAGE = "Usage: python -m src.hook_entry <claude|cursor> [hook_name]"

# Duplicated from src.guardrails.config, which would pull in the engine;
# test_contracts.py asserts the copies stay equal.
_KILL_SWITCH_ENV = "CLOUDBYTE_GUARDRAILS_DISABLED"
_PROFILE_RELATIVE_PATH = (".cloudbyte", "guardrails", "guardrails_profile.yaml")
_TRUTHY = ("1", "true", "yes", "on")

# "No opinion" per platform. Claude Code takes `{}` and applies the user's
# normal permission flow. Cursor blocks on a response that does not match the
# hook schema whatever failClosed says, so it gets an explicit allow.
_NO_OPINION = {"claude": "{}", "cursor": '{"permission": "allow"}'}


def _is_inactive() -> bool:
    """
    True when guardrails cannot possibly have an opinion, decided using nothing
    but `os`. The feature ships disabled (no profile), so this is the usual path
    and it avoids importing the engine.
    """
    if os.environ.get(_KILL_SWITCH_ENV, "").strip().lower() in _TRUTHY:
        return True
    home = os.path.expanduser("~")
    return not os.path.exists(os.path.join(home, *_PROFILE_RELATIVE_PATH))


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

    if _is_inactive():
        print(_NO_OPINION[platform])
        return 0

    if platform == "claude":
        from src.handlers.pre_tool_use import handle_pre_tool_use
        return handle_pre_tool_use()

    # A copy that is another client's install (Cursor also runs our hooks from
    # the Claude Code install) still decides - its answer can only add a deny,
    # never cancel one - but leaves the audit row to Cursor's own install, so
    # protection holds even where only the Claude Code install exists.
    from src.common.install_owner import CURSOR, foreign_owner, stand_down_notice
    owner = foreign_owner(CURSOR)
    if owner is not None:
        print(stand_down_notice(owner, CURSOR) + " (guardrails still checked, not recorded)",
              file=sys.stderr)

    from src.cursor.handlers.guardrails import dispatch
    return dispatch(argv[1] if len(argv) > 1 else "", record=owner is None)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
