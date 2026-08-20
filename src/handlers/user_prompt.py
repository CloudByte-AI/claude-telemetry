"""
UserPromptSubmit Handler

Called when the user submits a prompt.
Stores the prompt in the USER_PROMPT and RAW_LOG tables.
Also reminds Claude to emit obs blocks in its response.

prompt_id comes straight from the hook payload and is used as the USER_PROMPT
primary key. Claude Code puts the same UUID on PreToolUse, PostToolUse and Stop
for this turn, so every downstream row links by exact id.

Two things this replaced:
  - the generated-UUID + fuzzy-text reverse match in stop(), and
  - the special-character deferral, which skipped the insert for any prompt
    containing non-ASCII text because the hook's text and the transcript's text
    had to be string-compared to find each other. Nothing compares text any
    more, so the hook's text is simply authoritative.
"""

import json
import os
import sys
from pathlib import Path

from ftfy import fix_text

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.git_utils import get_git_branch
from src.common.logging import get_logger, setup_logging
from src.common.obs_instructions import CLAUDE_OBS_REMINDER
from src.common.paths import get_claude_logs_dir
from src.common.time_utils import get_now_ist_iso
from src.core.event_processor import process_user_prompt
from src.integrations.claude.env import get_client_version, get_entrypoint


logger = get_logger(__name__)


# Track sessions that have been initialized (in-memory for this handler process)
_initialized_sessions = set()


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def ensure_session_initialized(session_id: str, cwd: str) -> bool:
    """
    Ensure the session and project exist in the database.
    Only does the check once per session (tracked in-memory).

    This handles the case where the plugin is started in an already-running
    session (so SessionStart hook was never called).

    Args:
        session_id: Session UUID
        cwd: Current working directory

    Returns:
        bool: True if session exists or was created successfully
    """
    global _initialized_sessions

    # Skip if already checked for this session
    if session_id in _initialized_sessions:
        return True

    from src.db.manager import get_db_connection
    from src.integrations.claude.extractor import extract_project_info
    from src.common.time_utils import get_now_ist_iso

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Check if session exists
        cursor.execute(
            "SELECT 1 FROM SESSION WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        if cursor.fetchone() is not None:
            _initialized_sessions.add(session_id)
            logger.debug(f"Session {session_id} already initialized")
            return True

        # Session doesn't exist - create project and session
        logger.info(
            f"Session {session_id} not found, initializing project and session records"
        )

        # Extract project info from cwd
        project_info = extract_project_info(cwd if cwd else "")
        project_id = project_info["project_id"]

        # Ensure project exists
        cursor.execute(
            "SELECT 1 FROM PROJECT WHERE project_id = ? LIMIT 1",
            (project_id,),
        )
        if cursor.fetchone() is None:
            cursor.execute(
                """
                INSERT INTO PROJECT (project_id, name, path, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    project_id,
                    project_info["name"],
                    project_info["path"],
                    project_info["created_at"],
                ),
            )
            logger.info(f"Created project: {project_id}")

        # Create session
        cursor.execute(
            """
            INSERT INTO SESSION
            (session_id, project_id, cwd, transcript_path, created_at, client)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                project_id,
                cwd,
                f"{project_info['name']}/{session_id}.jsonl",
                get_now_ist_iso(),
                "claude_code",
            ),
        )

        conn.commit()
        logger.info(f"Created session: {session_id}")

        _initialized_sessions.add(session_id)
        return True

    except Exception as e:
        logger.error(f"Error ensuring session initialized: {e}")
        return False


# ---------------------------------------------------------------------------
# OBS reminder constant
# ---------------------------------------------------------------------------

OBS_REMINDER = CLAUDE_OBS_REMINDER


# ---------------------------------------------------------------------------
# Text / content helpers
# ---------------------------------------------------------------------------

def filter_system_messages(content: str) -> str:
    """
    Filter out system/context messages from prompt content.

    Removes:
        - <ide_opened_file>…</ide_opened_file>
        - <ide_selection>…</ide_selection>
        - <system-reminder>…</system-reminder>
        - <user-prompt-submit-hook …>…</user-prompt-submit-hook>
        - <sessionstart-hook …>…</sessionstart-hook>
        - <task-notification>…</task-notification>

    Args:
        content: Raw prompt content (may include system messages)

    Returns:
        str: Cleaned prompt text with only the user's own words
    """
    if not content:
        return ""

    import re

    # Remove ANY <ide*> blocks (properly closed)
    content = re.sub(r"<ide[^>]*>[\s\S]*?</ide[^>]*>", "", content)
    # Remove unclosed <ide*> tags to end-of-string
    content = re.sub(r"<ide[^>]*>[\s\S]*?(?=<\w|$)", "", content)

    # Remove <system-reminder> blocks
    content = re.sub(
        r"<system-reminder>.*?</system-reminder>\s*", "", content, flags=re.DOTALL
    )

    # Remove <user-prompt-submit-hook …> blocks
    content = re.sub(
        r"<user-prompt-submit-hook.*?</user-prompt-submit-hook>\s*",
        "",
        content,
        flags=re.DOTALL,
    )

    # Remove <sessionstart-hook …> blocks
    content = re.sub(
        r"<sessionstart-hook.*?</sessionstart-hook>\s*", "", content, flags=re.DOTALL
    )
    content = re.sub(
        r"<sessionstart-hook-additional-context.*?</sessionstart-hook-additional-context>\s*",
        "",
        content,
        flags=re.DOTALL,
    )

    # Remove <task-notification> blocks (sub-agent completion notices, not real prompts)
    content = re.sub(
        r"<task-notification>.*?</task-notification>\s*", "", content, flags=re.DOTALL
    )

    return content.strip()


def extract_user_text_from_hook_data(hook_data: dict) -> str:
    """
    Extract the user's actual text from hook data.

    Handles two formats:
        1. Simple string field  →  hook_data["prompt"] or hook_data["content"]
        2. Message-content array  →  hook_data["message"]["content"][*]["text"]

    System tags are stripped in both cases.

    Args:
        hook_data: Raw hook data from Claude Code (stdin JSON)

    Returns:
        str: Cleaned user text only
    """
    # Format 1 - simple string
    if "prompt" in hook_data:
        return filter_system_messages(hook_data["prompt"])
    if "content" in hook_data and isinstance(hook_data["content"], str):
        return filter_system_messages(hook_data["content"])

    # Format 2 - message.content array
    message = hook_data.get("message", {})
    if isinstance(message, dict):
        content_array = message.get("content", [])
        if isinstance(content_array, list):
            user_texts = []
            for item in content_array:
                if isinstance(item, dict) and item.get("type") == "text":
                    filtered = filter_system_messages(item.get("text", ""))
                    if filtered:
                        user_texts.append(filtered)
            return " ".join(user_texts).strip()

    return ""


def read_stdin_data() -> dict:
    """
    Read hook data from stdin (JSON).

    Reads the raw bytes and decodes UTF-8 explicitly rather than using
    sys.stdin.read(). Claude Code always writes UTF-8, but sys.stdin on Windows
    decodes using the locale codepage - cp1252 here - so any prompt containing
    an emoji, an em-dash or a non-Latin character either raised
    UnicodeDecodeError or silently produced mojibake, and json.loads then failed
    and returned {}. That is the bug the old "defer prompts with special
    characters to the stop hook" branch was working around.

    errors="replace" keeps a partially-undecodable payload parseable instead of
    losing the whole hook invocation.

    Returns:
        dict: Parsed hook data, or {} on error
    """
    try:
        raw = sys.stdin.buffer.read()
        data = raw.decode("utf-8", errors="replace").strip()
        if data:
            return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")
    except Exception as e:
        logger.error(f"Error reading stdin: {e}")

    return {}


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle_user_prompt():
    """
    Handle the UserPromptSubmit hook.

    Expected stdin data (JSON):
    {
        "session_id"      : "uuid",
        "transcript_path" : "/path/to/session.jsonl",
        "cwd"             : "/path/to/project",
        "prompt_id"       : "b47ca807-f863-4e3e-b143-7251ede274d4",
        "permission_mode" : "default",
        "prompt"          : "user prompt here"
    }

    prompt_id and permission_mode are read directly off this payload - no
    transcript read, no retry loop, no reverse matching.
    """
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_claude_logs_dir())
    logger.info("=== UserPromptSubmit Handler ===")

    # Ensure background worker is running (fast port check)
    try:
        from src.workers.worker_checker import ensure_worker_quick_sync
        ensure_worker_quick_sync()
    except Exception as e:
        logger.debug(f"Worker check failed: {e}")

    try:
        # ── Read hook data ───────────────────────────────────────────────────
        hook_data = read_stdin_data()

        logger.info(f"hook_data keys: {list(hook_data.keys())}")
        for key, value in hook_data.items():
            if key != "prompt":
                logger.info(f"  {key}: {value}")
            else:
                logger.info(
                    f"  {key}: (length={len(value)}, preview={value[:100]})"
                )


        # ── Basic fields available directly in hook stdin ────────────────────
        session_id = (
            hook_data.get("session_id")
            or hook_data.get("sessionId")
            or os.environ.get("CLAUDE_SESSION_ID")
        )
        cwd = (
            hook_data.get("cwd")
            or hook_data.get("directory")
            or os.environ.get("PWD")
            or os.environ.get("cwd")
        )
        event_timestamp = hook_data.get("timestamp") or hook_data.get("time")

        # The turn's real id and permission mode, straight off the hook payload.
        prompt_id = hook_data.get("prompt_id")
        mode = hook_data.get("permission_mode")

        # Three fields no hook payload carries. Sourced without touching the
        # transcript: entrypoint and version from the environment Claude Code
        # gives the hook process, branch from .git/HEAD under cwd. All three are
        # best-effort and return None on any problem - stop() still back-fills
        # from the transcript for whatever comes back empty.
        entrypoint = get_entrypoint()
        client_version = get_client_version()
        git_branch = get_git_branch(cwd)

        # ── Heartbeat this session in the shared active-session registry ─────
        # Uses register() (not a separate "touch if exists" call) so a session
        # that resumed without SessionStart ever firing (e.g. Claude Code
        # started mid-session) still ends up tracked as active - register()
        # is a safe overwrite either way.
        if session_id:
            try:
                from src.common.session_registry import register
                register(session_id, "claude_code")
            except Exception as e:
                logger.debug(f"session_registry.register failed: {e}")

        # ── Fallback: recover any missed stop-hook pairs ─────────────────────
        # Covers mid-session tool denial / interrupt where stop hook didn't fire.
        # Runs before storing the new prompt so the previous turn is recovered first.
        if session_id and cwd:
            try:
                from src.core.recovery import process_missed_pairs
                _counts = process_missed_pairs(session_id, cwd)
                _total = _counts.get("pass1", 0) + _counts.get("pass2", 0)
                if _total > 0:
                    logger.info(
                        f"UserPromptSubmit recovery: pass1={_counts.get('pass1',0)} pass2={_counts.get('pass2',0)}"
                    )
            except Exception as _re:
                logger.warning(f"UserPromptSubmit missed-pair recovery failed: {_re}")

        # ── Extract clean prompt text (filter system messages) ───────────────
        prompt_text = fix_text(extract_user_text_from_hook_data(hook_data))

        if not prompt_text:
            logger.warning("No prompt content provided (after filtering system messages)")
            print(json.dumps({"status": "error", "message": "No prompt content"}))
            return

        # ── Ensure SESSION exists before any DB write (including security events) ──
        # SessionStart may never have fired (mid-session plugin attach). The
        # security-block path returns early and used to skip bootstrap, so
        # write_finding() hit a SESSION FK error and the audit row was lost.
        if session_id and cwd:
            ensure_session_initialized(session_id, cwd)

        # ── Security scan - runs before USER_PROMPT write ────────────────────
        # If scanning is enabled and a finding is detected, the prompt is
        # blocked immediately. process_user_prompt() is never called so the
        # raw secret is never written to USER_PROMPT. All findings are logged
        # to SECURITY_SCAN_EVENT with the masked version of the prompt.
        try:
            # Ensure SECURITY_SCAN_EVENT table exists before writing.
            # migrate_schema() only runs in stop() by default - run it here
            # too so existing installs don't miss the first scan events.
            try:
                from src.db.schema import migrate_schema
                from src.db.manager import get_db_manager
                migrate_schema(get_db_manager().get_connection())
            except Exception as _me:
                logger.debug(f"Security migration check skipped: {_me}")

            from src.security.config import load_security_config
            from src.security.scanner import scan_text
            from src.security.masker import mask_text
            from src.security.db_writer import write_finding

            _sec_cfg = load_security_config(cwd=cwd)
            if _sec_cfg.enabled and prompt_text:
                _sec_result = scan_text(prompt_text, _sec_cfg.prompt_config)
                if _sec_result.findings:
                    _masked = mask_text(prompt_text, _sec_result.findings)
                    _sec_result.masked_text = _masked
                    write_finding(
                        session_id=session_id,
                        scan_target="prompt",
                        result=_sec_result,
                        blocked=True,
                        masked_text=_masked,
                    )
                    _ms = _sec_result.scan_ms
                    _ms_str = f"{_ms:.2f}" if _ms < 1 else f"{int(_ms)}"
                    logger.info(
                        f"Security scan: {len(_sec_result.findings)} finding(s) - blocking prompt "
                        f"[{_sec_result.scan_strategy}, {_ms_str}ms]"
                    )
                    _finding_lines = "\n".join(
                        f"  • {f.category} - {f.type} [{f.severity}]"
                        for f in _sec_result.findings
                    )
                    _reason = (
                        f"⚠️  Sensitive data detected and masked automatically!\n\n"
                        f"Detected:\n{_finding_lines}\n\n"
                        f"📊 Scanned {_sec_result.line_count} lines in {_ms_str}ms"
                        f" [strategy: {_sec_result.scan_strategy}]\n\n"
                        f"✅ Your prompt has been sanitized. Copy the masked version below to resubmit:\n\n"
                        f"{_masked}\n\n"
                        f"─────────────────────────────────────────────────────\n"
                        f"💡 False positive? If a detected value is a test credential, documentation\n"
                        f"   example, or placeholder that is safe to ignore, you can add it to your\n"
                        f"   allowlist via the CloudByte dashboard:\n\n"
                        f"   http://localhost:4723/security\n\n"
                        f"   Once added, that value will never be detected or block your prompt again."
                    )
                    _system_msg = (
                        f"⚠️ {len(_sec_result.findings)} sensitive item(s) detected and blocked."
                        f" Scanned {_sec_result.line_count} lines in {_ms_str}ms."
                        f" Event logged to telemetry."
                        f" To suppress known-safe values, add them to allowlist in"
                        f" ~/.cloudbyte/security/security_profile.yaml."
                    )
                    print(json.dumps({
                        "decision": "block",
                        "suppressOriginalPrompt": True,
                        "reason": _reason,
                        "systemMessage": _system_msg,
                    }))

                    # ── CLI's yellow warning is easy to miss with multiple sessions
                    # open, so also fire a native desktop popup - same content as
                    # the block reason above, including the masked prompt (copyable
                    # via the dialog's clipboard icon). Detached + best-effort: never
                    # affects the block decision or the hook's own exit.
                    try:
                        from src.security.notifier import notify_blocked_prompt
                        _dialog_msg = (
                            f"Sensitive data detected and blocked!\n\n"
                            f"Detected:\n{_finding_lines}\n\n"
                            f"Scanned {_sec_result.line_count} lines in {_ms_str}ms"
                            f" [strategy: {_sec_result.scan_strategy}]\n\n"
                            f"Your prompt was not sent. Sanitized version"
                            f" (use the copy icon above to copy it):\n\n"
                            f"{_masked}\n\n"
                            f"False positive? Add safe values to your allowlist:\n"
                            f"http://localhost:4723/security"
                        )
                        notify_blocked_prompt(_dialog_msg, masked_prompt=_masked)
                    except Exception as _notify_err:
                        logger.debug(f"Blocked-prompt popup skipped (non-fatal): {_notify_err}")

                    return
        except Exception as _sec_err:
            # Scan failure must never block the user - log and continue
            logger.warning(f"Security scan error (non-fatal, prompt proceeding): {_sec_err}")

        # ── Store prompt in DB ───────────────────────────────────────────────
        # No prompt_id means a Claude Code build older than v2.1.196, which is
        # the version that added the field. Writing the row anyway would mean
        # minting a local id that nothing downstream can link to, so skip the
        # write and say why - rather than silently producing orphan rows.
        if not prompt_id:
            logger.error(
                "UserPromptSubmit payload carried no prompt_id - skipping USER_PROMPT write. "
                "This field requires Claude Code v2.1.196 or later."
            )
            print(json.dumps({
                "status": "error",
                "message": "No prompt_id in hook payload (requires Claude Code v2.1.196+)",
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": OBS_REMINDER,
                },
            }))
            return

        logger.info(
            f"Storing prompt: session_id={session_id}, "
            f"prompt_id={prompt_id}, mode={mode}, entrypoint={entrypoint}, "
            f"client_version={client_version}, git_branch={git_branch}, "
            f"length={len(prompt_text)}"
        )

        result = process_user_prompt(
            prompt=prompt_text,
            session_id=session_id,
            prompt_id=prompt_id,
            mode=mode,
            entrypoint=entrypoint,
            client_version=client_version,
            git_branch=git_branch,
            event_timestamp=event_timestamp,
            cwd=cwd,
        )

        logger.info(f"Prompt stored: {result.get('prompt_id')}")

        # ── Inject OBS reminder into Claude's context ────────────────────────
        logger.info("=" * 60)
        logger.info("INJECTING OBS REMINDER INTO USERPROMPTSUBMIT CONTEXT")
        logger.info("=" * 60)
        logger.info(f"Prompt ID : {result.get('prompt_id')}")
        logger.info(f"Reminder  : {OBS_REMINDER}")

        output_data = {
            "status": "success",
            "prompt_id": result.get("prompt_id"),
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": OBS_REMINDER,
            },
        }
        print(json.dumps(output_data))
        logger.info("✓ OBS reminder successfully output to Claude Code")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Error in UserPromptSubmit handler: {e}", exc_info=True)
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


def main():
    """Main entry point for the handler."""
    handle_user_prompt()


if __name__ == "__main__":
    main()