"""
Cursor BeforeSubmitPrompt Handler

Handles the beforeSubmitPrompt hook, fired right after the user hits send
and before the backend request. Performs a security scan on the prompt
BEFORE any DB write - secrets are never stored. If findings are detected,
the prompt is blocked and a user-visible warning is shown in the chat UI.
On a clean prompt, persists a USER_PROMPT row and backfills
SESSION.transcript_path and SESSION.ai_title if not set yet.

Field mapping:
  prompt_id      <- generation_id (used directly as the primary key)
  session_id     <- session_id, falling back to conversation_id
  prompt         <- prompt, repaired via repair_text() (see hook_io.py)
  attachments    <- attachments, JSON-encoded
  client_version <- cursor_version
  mode           <- composer_mode (agent/ask/edit - Cursor's closest
                     equivalent to Claude Code's permission_mode)
  git_branch     <- self-derived via `git rev-parse --abbrev-ref HEAD`
                     against workspace_roots[0] - no hook gives this
  entrypoint     <- hardcoded "cursor-ide"
  timestamp      <- stamped locally
  uuid, parent_uuid, jsonl_prompt_id: left NULL - no Cursor equivalent.
  status: left NULL - populated later by the stop hook.

Security scanning:
  - Runs before any DB write so raw secrets are never persisted.
  - On finding: returns {"continue": false, "user_message": "..."} which
    Cursor displays in the chat UI. The masked prompt is included so the
    user can copy and resubmit safely.
  - On clean prompt or disabled security: falls through to DB write.
  - Scan failures always fail open (prompt is never blocked due to a bug).
  - Findings are logged to SECURITY_SCAN_EVENT with blocked=True.
"""

import json
import subprocess

from src.common.logging import get_logger, setup_logging
from src.common.time_utils import get_now_ist_iso
from src.cursor.utils import obs_state
from src.cursor.utils.composer_title import get_composer_title
from src.cursor.utils.hook_io import debug, normalize_cwd, normalize_path, read_stdin_json, repair_text
from src.cursor.utils.paths import get_cursor_logs_dir
from src.db.writers import DatabaseWriter


logger = get_logger(__name__)

CURSOR_ENTRYPOINT = "cursor-ide"


def _get_git_branch(cwd: str | None) -> str | None:
    """Best-effort current git branch for cwd. Returns None on any failure."""
    if not cwd:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=3,
        )
        branch = result.stdout.strip()
        return branch if result.returncode == 0 and branch else None
    except Exception as e:
        debug(f"git branch lookup failed for {cwd}: {e}")
        return None


def _build_block_user_message(findings: list, masked_text: str, scan_result) -> str:
    """
    Build a clear, actionable user_message shown in the Cursor chat UI
    when a prompt is blocked due to detected secrets.
    """
    ms = scan_result.scan_ms
    ms_str = f"{ms:.2f}" if ms < 1 else f"{int(ms)}"

    finding_lines = "\n".join(
        f"  • {f.category} - {f.type} [{f.severity}]"
        for f in findings
    )

    return (
        f"⚠️  Sensitive data detected - prompt blocked by CloudByte Security\n\n"
        f"Detected {len(findings)} sensitive item(s) found in the prompt:\n\n"
        f"{finding_lines}\n\n"
        f"📊 Scanned {scan_result.line_count} line(s) in {ms_str}ms"
        f" [strategy: {scan_result.scan_strategy}]\n\n"
        f"✅ Your prompt has been sanitized. Copy the masked version below and resubmit:\n\n"
        f"────────────────────────────────────────────────────────\n\n"
        f"{masked_text}\n\n"
        f"────────────────────────────────────────────────────────\n\n"
        f"💡 False positive? Add the value to your allowlist so it is never\n"
        f"   flagged again:\n\n"
        f"   http://localhost:8765/security\n\n"
    )


def _run_security_scan(
    prompt_text: str,
    session_id: str,
    cwd: str | None,
) -> dict | None:
    """
    Run the security scan on the prompt text.

    Returns a Cursor hook output dict with {"continue": false, "user_message": "..."}
    if the prompt should be blocked, or None if the prompt is clean / scanning
    is disabled / an error occurred (all three cases allow the prompt through).

    Never raises - scan failures always fail open.
    """
    try:
        from src.db.manager import get_db_connection
        from src.db.schema import migrate_schema
        from src.security.config import load_security_config
        from src.security.scanner import scan_text
        from src.security.masker import mask_text
        from src.security.db_writer import write_finding

        # Ensure SECURITY_SCAN_EVENT table exists (safe to call repeatedly).
        try:
            migrate_schema(get_db_connection())
        except Exception as me:
            logger.debug(f"Security schema migration check skipped: {me}")

        sec_cfg = load_security_config(cwd=cwd)

        if not sec_cfg.enabled:
            logger.debug("Security scanning disabled - skipping prompt scan")
            return None

        logger.info(
            f"Security scan starting - plan={sec_cfg.plan!r}, scope={sec_cfg.scope!r}, "
            f"prompt_len={len(prompt_text)} chars"
        )

        scan_result = scan_text(prompt_text, sec_cfg.prompt_config)

        ms = scan_result.scan_ms
        ms_str = f"{ms:.2f}" if ms < 1 else f"{int(ms)}"
        logger.info(
            f"Security scan complete - findings={len(scan_result.findings)}, "
            f"lines={scan_result.line_count}, time={ms_str}ms, "
            f"strategy={scan_result.scan_strategy}"
        )

        if not scan_result.findings:
            logger.info("Security scan: no sensitive data detected - prompt allowed")
            return None

        # Secrets found - mask the text and block the prompt.
        masked_text = mask_text(prompt_text, scan_result.findings)
        scan_result.masked_text = masked_text

        for finding in scan_result.findings:
            logger.warning(
                f"Security finding: category={finding.category!r}, "
                f"type={finding.type!r}, severity={finding.severity!r}, "
                f"line={finding.line_number}"
            )

        event_id = write_finding(
            session_id=session_id,
            scan_target="prompt",
            result=scan_result,
            blocked=True,
            masked_text=masked_text,
        )
        logger.info(
            f"Security event logged: event_id={event_id}, "
            f"findings={len(scan_result.findings)}, blocked=True"
        )

        user_message = _build_block_user_message(scan_result.findings, masked_text, scan_result)
        logger.info(
            f"Blocking prompt - {len(scan_result.findings)} finding(s) detected. "
            f"event_id={event_id}"
        )
        return {"continue": False, "user_message": user_message}

    except Exception as e:
        logger.warning(
            f"Security scan error (non-fatal, prompt proceeding): {e}",
            exc_info=True,
        )
        debug(f"Security scan exception: {e}")
        return None


def handle_before_submit_prompt() -> None:
    """Handle Cursor's beforeSubmitPrompt hook: security scan, then persist a USER_PROMPT row."""
    debug("beforeSubmitPrompt handler triggered")
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_cursor_logs_dir())
    logger.info("=== Cursor BeforeSubmitPrompt Handler ===")

    try:
        hook_data = read_stdin_json()
        logger.info(f"beforeSubmitPrompt full payload: {json.dumps(hook_data, default=str)}")
        debug(f"payload keys: {list(hook_data.keys())}")

        prompt_id = hook_data.get("generation_id")
        session_id = hook_data.get("session_id") or hook_data.get("conversation_id")
        prompt_text = repair_text(hook_data.get("prompt"))
        attachments = hook_data.get("attachments")
        workspace_roots = hook_data.get("workspace_roots") or []
        cwd = normalize_cwd(workspace_roots[0]) if workspace_roots else None

        obs_state.create(session_id, prompt_id)

        # Heartbeat this session in the shared active-session registry, and
        # self-heal the worker/dashboard if it died mid-session - mirrors
        # Claude Code's user_prompt.py handling of the same shared resource.
        # Uses register() (not a separate "touch if exists" call) because
        # Cursor's sessionStart only fires for a genuinely NEW composer
        # conversation (confirmed in hooks.md: "Called when a new composer
        # conversation is created") - continuing a past conversation never
        # triggers it, so this session may never have been registered yet.
        # register() safely overwrites either way.
        try:
            from src.common.session_registry import register
            register(session_id, "cursor")
        except Exception as e:
            debug(f"session_registry.register failed: {e}")

        try:
            from src.workers.worker_checker import ensure_worker_quick_sync
            ensure_worker_quick_sync()
        except Exception as e:
            debug(f"Worker check failed: {e}")

        if not prompt_id or not session_id or not prompt_text:
            logger.warning(
                f"Incomplete beforeSubmitPrompt payload - generation_id={prompt_id!r}, "
                f"session_id={session_id!r}, prompt present={bool(prompt_text)}. Skipping write."
            )
            print(json.dumps({"continue": True}))
            return

        # ── Ensure SESSION exists before any DB write (including security events) ──
        # sessionStart only fires for new composer chats; resumed conversations
        # used to hit write_finding() without a SESSION row and lose audit logs.
        from src.db.manager import get_db_connection
        from src.db.schema import migrate_schema
        from src.cursor.utils.session_bootstrap import ensure_session_and_project

        migrate_schema(get_db_connection())
        ensure_session_and_project(
            session_id, cwd, transcript_path=normalize_path(hook_data.get("transcript_path"))
        )

        # ── Security scan - runs before USER_PROMPT write ─────────────────────
        # If the scan detects secrets, block immediately. The raw secret is
        # never written to USER_PROMPT. write_finding() logs the masked version
        # to SECURITY_SCAN_EVENT. Failures always fail open.
        block_response = _run_security_scan(prompt_text, session_id, cwd)
        if block_response is not None:
            debug(f"Prompt blocked by security scan - session_id={session_id}")
            print(json.dumps(block_response))
            return

        # ── Persist prompt (only reached on clean / disabled scan) ────────────
        writer = DatabaseWriter()
        written = writer.write_user_prompt({
            "prompt_id": prompt_id,
            "session_id": session_id,
            "prompt": prompt_text,
            "timestamp": get_now_ist_iso(),
            "client_version": hook_data.get("cursor_version"),
            "attachments": json.dumps(attachments) if attachments is not None else None,
            "mode": hook_data.get("composer_mode"),
            "git_branch": _get_git_branch(cwd),
            "entrypoint": CURSOR_ENTRYPOINT,
        })

        transcript_path = normalize_path(hook_data.get("transcript_path"))
        if transcript_path:
            writer.update_session_transcript_path(session_id, transcript_path)

        if not writer.get_session_ai_title(session_id):
            title = get_composer_title(session_id)
            if title:
                writer.update_session_ai_title(session_id, title)

        if written:
            debug(f"prompt stored - prompt_id={prompt_id}, session_id={session_id}")
            logger.info(f"Cursor prompt stored: prompt_id={prompt_id}, session_id={session_id}")
        else:
            debug(f"prompt NOT stored - prompt_id={prompt_id}, session_id={session_id}")
            logger.warning(
                f"Cursor prompt write failed - prompt_id={prompt_id}, session_id={session_id} "
                f"(see preceding error log line for the reason, e.g. missing SESSION row)"
            )

    except Exception as e:
        debug(f"ERROR - {e}")
        logger.error(f"Error in Cursor BeforeSubmitPrompt handler: {e}", exc_info=True)

    print(json.dumps({"continue": True}))


def main() -> None:
    handle_before_submit_prompt()


if __name__ == "__main__":
    main()
