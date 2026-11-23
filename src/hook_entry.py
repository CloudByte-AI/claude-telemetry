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

`claude` (PreToolUse) also takes a shell call's before-snapshot (src/capture/)
before the check; it prints nothing and cannot change the answer.

`claude post_tool_use` is the post-execution hook: it records what a tool call
changed in files (src/capture/), opens the window of a remembered approval once
an asked-about tool has run (approvals.py), and never fails closed, since it
decides nothing. `claude post_tool_use_failure` records what a failed shell call
changed (hooks/capture-hooks.json).

`claude model_switch | model_switched | agent_model` are the model-access hooks
(hooks/model-hooks.json, src/handlers/model_access.py). They fail open: a
model check that cannot run allows the model.
"""

import os
import sys

USAGE = "Usage: python -m src.hook_entry <claude|cursor> [hook_name]"
# `claude post_tool_use`: shown asks that ran (src/guardrails/asks.py) and
# remembered approvals (src/guardrails/approvals.py).
POST_TOOL_USE = "post_tool_use"
# `claude post_tool_use_failure`: a failed shell call's after-snapshot (src/capture/).
POST_TOOL_USE_FAILURE = "post_tool_use_failure"
# Cursor guardrails hooks that also take edit evidence's "before" (src/capture/cursor.py).
_CURSOR_CAPTURE_HOOKS = ("pre_tool_use", "before_shell_execution")
# Model-access hooks -> what each prints when it has no opinion. PostModelSwitch
# prints nothing: its stdout would be added to Claude's context.
_MODEL_HOOKS = {"model_switch": "{}", "model_switched": None, "agent_model": "{}"}

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

    if platform == "claude" and len(argv) > 1 and argv[1] == POST_TOOL_USE_FAILURE:
        return _post_tool_use_failure()

    if platform == "claude" and len(argv) > 1 and argv[1] in _MODEL_HOOKS:
        return _model_hook(argv[1])

    # Claude Code PreToolUse: a shell call's before-snapshot first (src/capture/), with the
    # input read once here and handed to the guardrails check below.
    raw = None
    if platform == "claude" and len(argv) == 1:
        raw = _read_stdin()
        _capture_shell_before(raw)

    # Cursor preToolUse (file tools) and beforeShellExecution: the same, for Cursor.
    if platform == "cursor" and len(argv) > 1 and argv[1] in _CURSOR_CAPTURE_HOOKS:
        raw = _read_stdin()
        _capture_cursor_before(raw, argv[1])

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
        return handle_pre_tool_use(raw)

    # A copy that is another client's install (Cursor also runs our hooks from
    # the Claude Code install) still decides - its answer can only add a deny,
    # never cancel one - but leaves the audit row to Cursor's own install, so
    # protection holds even where only the Claude Code install exists.
    owner = foreign_owner(CURSOR)
    if owner is not None:
        print(stand_down_notice(owner, CURSOR) + " (guardrails still checked, not recorded)",
              file=sys.stderr)

    return dispatch(argv[1] if len(argv) > 1 else "", record=owner is None, raw=raw)


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


def _read_stdin() -> bytes:
    """The whole hook input as bytes; empty when it cannot be read. Like
    src.guardrails.hook_input: the byte stream when there is one (a BOM survives for the
    parser to strip), else the text stream, encoded."""
    stream = sys.stdin
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            return buffer.read()
        text = stream.read()
        return text.encode("utf-8") if isinstance(text, str) else bytes(text or b"")
    except Exception:
        return b""


def _capture_edit(raw: bytes, failed: bool = False) -> None:
    """Edit evidence (src/capture/): only calls that changed files do any work. Never raises."""
    try:
        from src.capture.hook import capture_post_tool_use
        capture_post_tool_use(raw, "claude", failed=failed)
    except Exception:
        pass


def _capture_shell_before(raw: bytes) -> None:
    """A shell call's before-snapshot (src/capture/snapshot.py). Never raises, never prints,
    so it cannot change the guardrails answer that follows."""
    try:
        from src.capture.hook import capture_pre_tool_use
        capture_pre_tool_use(raw, "claude")
    except Exception:
        pass


def _capture_cursor_before(raw: bytes, hook: str) -> None:
    """Cursor: copy the file a write is about to change, or snapshot the repo a shell command
    starts in (src/capture/cursor.py). Only Cursor's own install records, so a copy that is
    another client's install skips it. Never raises, never prints."""
    try:
        from src.common.install_owner import CURSOR, foreign_owner
        if foreign_owner(CURSOR) is None:
            from src.capture.cursor import capture_before
            capture_before(raw, hook)
    except Exception:
        pass


def _post_tool_use_failure() -> int:
    """
    Claude Code PostToolUseFailure (hooks/capture-hooks.json): a shell command that failed
    can still have changed files, so its after-snapshot is taken here. Prints nothing,
    exits 0.
    """
    _capture_edit(_read_stdin(), failed=True)
    return 0


def _post_tool_use() -> int:
    """
    Claude Code PostToolUse: record what an edit tool changed (src/capture/),
    then record that an asked-about call ran and turn an approved ask into an
    approval window (src/handlers/post_tool_use.py). It decides nothing, so
    unlike PreToolUse it never fails closed: whatever goes wrong, it prints
    nothing and exits 0.
    """
    raw = _read_stdin()
    _capture_edit(raw)
    if _is_inactive() or not (_has_shown_asks() or _has_pending_approvals()):
        return 0
    try:
        from src.handlers.post_tool_use import handle_post_tool_use
    except Exception:
        return 0
    return handle_post_tool_use(raw)


def _model_hook(command: str) -> int:
    """
    A model-access hook. With guardrails off, or when the handler cannot be
    imported, it answers with no opinion and exits 0: these hooks never fail
    closed, and a PreModelSwitch hook that errors or hangs would block the switch.
    """
    neutral = _MODEL_HOOKS[command]
    if not _is_inactive():
        try:
            from src.handlers.model_access import HANDLERS
        except Exception as exc:
            print(f"CloudByte guardrails: could not load the model check, so it was skipped "
                  f"({exc!r})", file=sys.stderr)
        else:
            return HANDLERS[command]()
    _drain_stdin()
    if neutral is not None:
        print(neutral)
    return 0


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
