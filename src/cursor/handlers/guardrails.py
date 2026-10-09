"""
Cursor guardrails adapter - four hooks, one adapter.

Cursor splits what Claude Code does in one hook across four. The per-hook
differences are data (`HOOKS`), not branching.

    hook                    surface         payload tool_name   matcher
    beforeShellExecution    shell           absent (-> Shell)   none (see below)
    beforeMCPExecution      mcp             the MCP tool        not accepted
    beforeReadFile          file read       absent (-> Read)    tool type
    preToolUse              writes/deletes  present             tool type

Cursor ignores `ask` on every hook (no approval prompt) but enforces `deny`.
So an `ask` is answered with an explicit allow plus a warning to the agent,
and recorded as `ask` with user_decision `not_prompted`.

- `beforeShellExecution`'s matcher filters the command text, not a tool name,
  so it registers with no matcher; a pattern there could silently stop a
  customer's own rule from ever firing.
- Cursor hooks fail open by default, but invalid JSON or a response that does
  not match the hook's schema blocks the action regardless of `failClosed`.
  So every answer is a complete object built before anything is printed, and
  a failed check is answered like an ask (decide_payload): deny for the
  profile's `deny` literals, otherwise allow with a warning, recorded.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace

from src.guardrails.decision import ALLOW, ASK, DENY, MESSAGE_PREFIX, SOURCE_ERROR, Decision
from src.guardrails.hook_input import HookInputError, read_stdin_payload
from src.guardrails.toolcall import (
    KIND_AGENT,
    KIND_DELETE,
    KIND_EDIT,
    KIND_MCP,
    KIND_OTHER,
    KIND_READ,
    KIND_SEARCH,
    KIND_SHELL,
    KIND_WEB,
    KIND_WRITE,
    ToolCall,
)

PLATFORM = "cursor"

# Cursor tool name -> operation kind. `Delete` has no Claude Code equivalent
# (deletion there goes through Bash), and the subagent tool is `Task`, not `Agent`.
TOOL_KINDS: dict[str, str] = {
    "Shell": KIND_SHELL,
    "Read": KIND_READ,
    "Write": KIND_WRITE,
    "StrReplace": KIND_EDIT,
    "EditNotebook": KIND_EDIT,
    "Delete": KIND_DELETE,
    "Glob": KIND_SEARCH,
    "Grep": KIND_SEARCH,
    "WebFetch": KIND_WEB,
    "WebSearch": KIND_WEB,
    "Task": KIND_AGENT,
}


@dataclass(frozen=True)
class HookSpec:
    """What one Cursor hook can do. The per-hook differences, as data."""

    name: str
    # Fixed kind for hooks that only ever see one surface; None means read it
    # from tool_name.
    kind: str | None
    # The Cursor tool this hook stands for when its payload has no tool_name,
    # so the audit row names the tool the way Cursor's TOOL rows do.
    default_tool: str = ""


HOOKS: dict[str, HookSpec] = {
    "before_shell_execution": HookSpec("beforeShellExecution", KIND_SHELL, "Shell"),
    "before_mcp_execution": HookSpec("beforeMCPExecution", KIND_MCP),
    "before_read_file": HookSpec("beforeReadFile", KIND_READ, "Read"),
    "pre_tool_use": HookSpec("preToolUse", None),
}


def kind_for(tool_name: str) -> str:
    if not tool_name:
        return KIND_OTHER
    if tool_name.startswith("mcp__") or tool_name.startswith("MCP:"):
        return KIND_MCP
    return TOOL_KINDS.get(tool_name, KIND_OTHER)


def _parse_tool_input(raw) -> dict:
    """
    Cursor sends MCP tool_input as a JSON STRING, and other tools as an object.
    Both shapes normalise to a dict here.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def normalise(payload: dict, hook: HookSpec) -> ToolCall:
    """
    A Cursor payload -> ToolCall. Pure; never raises.

    Cursor's Shell tool runs the native shell (PowerShell on Windows). The
    dialect is sniffed from the command text, not the host platform, so a
    payload parses the same everywhere.
    """
    from src.guardrails.parser import POSIX, sniff_dialect
    from src.guardrails.toolcall import split_mcp_tool_name

    tool_input = _parse_tool_input(payload.get("tool_input"))

    tool_name = str(payload.get("tool_name") or hook.default_tool)
    kind = hook.kind if hook.kind is not None else kind_for(tool_name)

    # beforeShellExecution carries the command at the top level; preToolUse
    # nests it under tool_input.
    command = payload.get("command") or tool_input.get("command") or ""
    # A stdio MCP server's launch command is NOT something the agent is
    # running, so it must not be parsed as one.
    if kind == KIND_MCP:
        command = ""

    dialect = sniff_dialect(str(command), POSIX) if command else POSIX

    # Prefer `mcp_server_name`; fall back to splitting the tool name.
    mcp_server = payload.get("mcp_server_name")
    mcp_tool = None
    if kind == KIND_MCP:
        split_server, split_tool = split_mcp_tool_name(tool_name)
        mcp_server = mcp_server or split_server
        mcp_tool = split_tool or (tool_name.split(":", 1)[-1] if ":" in tool_name else tool_name)

    paths = []
    for field in ("file_path", "notebook_path", "path", "target_file"):
        value = payload.get(field) or tool_input.get(field)
        if value:
            paths.append(str(value))

    urls = []
    for field in ("url",):
        value = tool_input.get(field)
        if value:
            urls.append(str(value))

    roots = [_normalise_cwd(root) for root in (payload.get("workspace_roots") or []) if root]
    cwd = _normalise_cwd(payload.get("cwd") or tool_input.get("working_directory"))
    if not cwd:
        cwd = roots[0] if roots else None

    return ToolCall.build(
        platform=PLATFORM,
        tool_name=tool_name,
        kind=kind,
        hook_event=hook.name,
        raw_input=tool_input,
        cwd=cwd,
        workspace_root=workspace_root_for(cwd, roots),
        session_id=payload.get("conversation_id"),
        # Cursor's generation_id changes with every user message and is what
        # this plugin stores as USER_PROMPT.prompt_id for Cursor.
        prompt_id=payload.get("generation_id"),
        tool_use_id=payload.get("tool_use_id"),
        permission_mode="default",
        command=str(command),
        dialect=dialect,
        file_paths=tuple(paths),
        content=tool_input.get("content"),
        mcp_server=mcp_server,
        mcp_tool=mcp_tool,
        urls=tuple(urls),
    )


def workspace_root_for(cwd: str | None, roots: list) -> str | None:
    """
    Which of Cursor's workspace roots this call belongs to: the deepest root
    that holds `cwd` (a multi-root workspace has several), else the first
    root. Picks the workspace policy - the root, not the cwd, is the
    workspace, as Claude Code's project directory is. Never raises.
    """
    try:
        from src.guardrails.workspaces import normalise_root

        here = normalise_root(cwd)
        best, best_length = None, -1
        for root in roots:
            candidate = normalise_root(root)
            if not candidate or not here:
                continue
            inside = here == candidate or here.startswith(candidate.rstrip("/") + "/")
            if inside and len(candidate) > best_length:
                best, best_length = root, len(candidate)
        return best or (roots[0] if roots else None)
    except Exception:
        return roots[0] if roots else None


def _normalise_cwd(cwd) -> str | None:
    """Cursor reports Windows paths as `/c:/...`; strip the leading slash."""
    if not cwd:
        return None
    text = str(cwd)
    if len(text) > 2 and text[0] == "/" and text[2] == ":":
        return text[1:]
    return text


def render(decision: Decision) -> tuple[dict, Decision]:
    """
    Decision -> Cursor's permission object. Pure.

    Returns the output and the decision as it applies on Cursor, for the audit
    row. `allow` is explicit, never `{}`: Cursor blocks on a response that does
    not match the hook schema. An `ask` becomes allow with a warning to the
    agent, since Cursor ignores `ask`.
    """
    if decision.action == ASK:
        return (
            {
                "permission": ALLOW,
                "agent_message": (
                    f"{MESSAGE_PREFIX}: this action needs approval ({decision.detail}), but "
                    f"Cursor cannot show an approval prompt for it, so it runs without review. "
                    f"Tell the user what ran."
                ),
            },
            replace(decision, prompted=False),
        )

    output: dict = {"permission": decision.action}
    if decision.action == DENY:
        # `Decision.message` is shared with the Claude Code adapter, so both
        # platforms word the same decision identically.
        output["user_message"] = decision.message
        output["agent_message"] = decision.message
    return output, decision


def evaluate_payload(payload: dict, hook: HookSpec) -> tuple[dict, Decision]:
    """Payload to rendered output. Raises if the engine does."""
    from src.guardrails.engine import evaluate

    call = normalise(payload, hook)
    return render(evaluate(call))


def decide_payload(payload: dict, hook: HookSpec) -> tuple[dict, Decision]:
    """
    evaluate_payload(), failing closed as far as Cursor allows. Never raises;
    shared by the command hook and the HTTP route.

    When the check raises, the answer is engine.fallback_decision(), or
    Decision.evaluation_failed() if even the engine cannot be imported. Cursor
    cannot prompt, so that ask runs with a warning to the agent.
    """
    try:
        return evaluate_payload(payload, hook)
    except Exception as exc:
        try:
            from src.common.logging import get_logger
            get_logger(__name__).error(
                f"guardrails: cursor evaluation failed, failing closed: {exc}", exc_info=True)
        except Exception:
            pass
    try:
        from src.guardrails.engine import fallback_decision
        decision = fallback_decision(_failure_text(payload))
    except Exception:
        decision = Decision.evaluation_failed()
    return render(decision)


def _failure_text(payload: dict) -> str:
    """What the call acts on, as one string, for the fallback deny check. Never raises."""
    try:
        parts = [payload.get("command"), payload.get("file_path")]
        parts.extend(_parse_tool_input(payload.get("tool_input")).values())
        return " ".join(str(part) for part in parts if isinstance(part, (str, int)) and part != "")
    except Exception:
        return ""


def tool_subject_hash(tool_name: str, tool_input) -> str | None:
    """
    The tool_input_hash a TOOL row's call was spooled with. Cursor's before-hooks
    carry no tool_use_id, so db_writer links an event to its TOOL row by prompt
    and this hash. Never raises.

    TOOL rows come from postToolUse, which names tools like preToolUse does.
    """
    try:
        payload = {"tool_name": tool_name, "tool_input": tool_input}
        return normalise(payload, HOOKS["pre_tool_use"]).subject_hash
    except Exception:
        return None


def drain_audit() -> int:
    """
    Drain the audit spool from a Cursor hook, linking Cursor events to their
    TOOL rows on the way: a deny to the record postToolUseFailure wrote for the
    blocked call, anything else to the record of the call that ran. The Cursor
    hooks call this rather than db_writer.drain directly. Never raises (drain
    doesn't).
    """
    from src.cursor.utils.tool_failures import is_blocked_output
    from src.guardrails.db_writer import drain

    return drain(tool_hashers={PLATFORM: tool_subject_hash},
                 blocked_records={PLATFORM: is_blocked_output})


_log_ready = False


def _cursor_log():
    """
    The plugin's Cursor log, set up on first use and only in the hook process.
    Setting it up costs more than a decision, so the common allow path never
    pays for it, and the HTTP route never calls this: it would take over the
    dashboard's own logging.
    """
    global _log_ready
    from src.common.logging import get_logger, setup_logging

    if not _log_ready:
        from src.cursor.utils.paths import get_cursor_logs_dir
        setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
        _log_ready = True
    return get_logger(__name__)


def _log_decision(hook: HookSpec, decision: Decision, payload: dict) -> None:
    """
    One line in the plugin's Cursor log for an ask, a deny or a check that
    failed. It names the rule and the call, never the command or file content,
    which can hold secrets. Never raises.
    """
    try:
        call = normalise(payload, hook)
        ran = "" if decision.prompted else ", ran without review"
        tool_use_id = str(call.tool_use_id or "-").replace("\n", " ")
        _cursor_log().info(
            f"guardrails: {hook.name} {call.tool_name or '-'} -> {decision.action}{ran} "
            f"(rule {decision.rule_id or '-'}, operation {decision.operation or '-'}, "
            f"alert {decision.alert_level}, source {decision.source}, {decision.eval_ms} ms) "
            f"target={decision.target or '-'} generation={call.prompt_id or '-'} "
            f"tool_use_id={tool_use_id}"
        )
    except Exception:
        pass


def dispatch(hook_name: str, record: bool = True) -> int:
    """
    Entry point for all four Cursor hooks. Always returns 0: deny is expressed
    in the permission object, not with exit code 2.

    `record=False`, for a copy that is another client's install, still decides
    and answers but writes no audit event and shows no notification
    (src/common/install_owner.py).
    """
    hook = HOOKS.get(hook_name)
    if hook is None:
        # Unknown hook name: say nothing at all rather than risk a response
        # that does not match whatever schema this hook expects.
        print(f"guardrails: unknown cursor hook '{hook_name}'", file=sys.stderr)
        return 0

    try:
        # Bytes, with the BOM Cursor prefixes stripped - see hook_input.
        payload = read_stdin_payload()
    except HookInputError as exc:
        # Unreadable input. Emit a valid allow rather than nothing, so a
        # failClosed:true configuration does not block on our parse failure,
        # and say so on stderr so the skipped check is not silent.
        print(f"{MESSAGE_PREFIX}: could not read the hook input, so this call was "
              f"not checked - {exc}", file=sys.stderr)
        print(json.dumps({"permission": ALLOW}))
        return 0

    output, decision = decide_payload(payload, hook)
    print(json.dumps(output))

    if record and decision.should_audit:
        try:
            from src.guardrails.spool import append_event
            append_event(decision, normalise(payload, hook))
        except Exception:
            pass

    # Last, once Cursor has its answer; never waited for. Like the audit event,
    # only Cursor's own install sends it.
    if record and decision.is_opinion:
        try:
            sys.stdout.flush()
            from src.guardrails.notify import notify_decision
            notify_decision(decision, normalise(payload, hook))
        except Exception:
            pass

    if record and (decision.is_opinion or decision.source == SOURCE_ERROR):
        _log_decision(hook, decision, payload)

    return 0


def before_shell_execution() -> int:
    return dispatch("before_shell_execution")


def before_mcp_execution() -> int:
    return dispatch("before_mcp_execution")


def before_read_file() -> int:
    return dispatch("before_read_file")


def pre_tool_use() -> int:
    return dispatch("pre_tool_use")
