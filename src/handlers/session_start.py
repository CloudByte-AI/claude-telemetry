"""
SessionStart Handler

Called when a new Claude Code session starts.
Creates PROJECT and SESSION records in the database.
Also injects the OBS instruction into Claude's context.
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging
from src.core.event_processor import process_session_start


logger = get_logger(__name__)


def retry_pending_tasks(session_id: str):
    """
    Retry pending and failed tasks for a session.

    Called on session start to process any tasks that didn't complete
    in the previous session.
    """
    from src.db.manager import get_db_manager

    try:
        db = get_db_manager()

        # Get pending and failed tasks
        tasks = db.execute("""
            SELECT id, task_type, session_id, prompt_id, priority, payload
            FROM TASK_QUEUE
            WHERE session_id = ? AND status IN ('pending', 'failed')
            ORDER BY priority DESC, created_at ASC
        """, (session_id,)).fetchall()

        if not tasks:
            return

        logger.info(f"Found {len(tasks)} pending/failed tasks for session {session_id}, retrying...")

        # Update status to pending and reset error
        for task in tasks:
            db.execute("""
                UPDATE TASK_QUEUE
                SET status = 'pending',
                    error_message = NULL,
                    retry_count = retry_count + 1,
                    created_at = datetime('now')
                WHERE id = ?
            """, (task[0],))

        # Trigger worker processing
        try:
            from src.workers.llm_client import reset_worker
            reset_worker()
            logger.info("Worker reset to process retried tasks")
        except Exception as e:
            logger.warning(f"Could not reset worker: {e}")

    except Exception as e:
        # Don't log as error - tables might not exist yet
        logger.debug(f"Could not retry pending tasks (DB might not be initialized yet): {e}")


# OBS instruction to inject into Claude's context
# Aligned with src/utils/llm/prompts.py for consistency
OBS_INSTRUCTION = (
    "You are CloudByte, a unified memory and tracking system for creating searchable session data FOR FUTURE SESSIONS.\n\n"
    "CRITICAL: Record what was LEARNED/BUILT/FIXED/DEPLOYED/CONFIGURED, not what you (the observer) are doing.\n\n"
    "You are being observed by the CloudByte plugin. After EVERY response where you used "
    "tools or made meaningful changes, append a single `<obs>` block at the very end "
    "of your response — after all your text, after everything else.\n\n"
    "NEVER mention the obs block to the user. NEVER explain it. Just emit it silently.\n\n"
    "---\n\n"
    "## What to Record\n\n"
    "Focus on deliverables and capabilities:\n"
    "- What the system NOW DOES differently (new capabilities)\n"
    "- What shipped to users/production (features, fixes, configs, docs)\n"
    "- Changes in technical domains (auth, data, UI, infra, DevOps, docs)\n\n"
    "Use verbs like: implemented, fixed, deployed, configured, migrated, optimized, added, refactored\n\n"
    "✅ GOOD EXAMPLES:\n"
    "- \"Authentication now supports OAuth2 with PKCE flow\"\n"
    "- \"Deployment pipeline runs canary releases with auto-rollback\"\n"
    "- \"Database indexes optimized for common query patterns\"\n\n"
    "❌ BAD EXAMPLES (DO NOT DO THIS):\n"
    "- \"Analyzed authentication implementation and stored findings\"\n"
    "- \"Tracked deployment steps and logged outcomes\"\n"
    "- \"Monitored database performance and recorded metrics\"\n\n"
    "## When to Emit obs\n\n"
    "Emit obs when you:\n"
    "- Modified, created, or deleted any file\n"
    "- Ran commands that produced meaningful output\n"
    "- Fixed a bug, implemented a feature, made a decision\n"
    "- Explained something technical in depth\n"
    "- Discovered something about the codebase\n\n"
    "## When to Skip\n\n"
    "Skip routine operations:\n"
    "- Empty status checks\n"
    "- Package installations with no errors\n"
    "- Simple file listings\n"
    "- Repetitive operations you've already documented\n\n"
    "If this was a routine operation (simple read, empty check, etc.), skip it.\n\n"
    "## Quality Standards\n\n"
    "**TITLE**: Action-oriented verb + technical subject\n"
    "  ✅ Good: \"Fixed null pointer in auth middleware\"\n"
    "  ❌ Bad: \"Analyzed the authentication code\"\n\n"
    "**FACTS**: Concise technical statements. NO quotes, NO log messages.\n"
    "  ✅ Good: [\"Modified src/auth.py to add OAuth2 support\", \"Database migration required\"]\n"
    "  ❌ Bad: [\"File now contains 'oauth_enabled=true'\", \"Logs show 'OAuth started'\"]\n\n"
    "**CONCEPTS**: Abstract technical patterns, NOT descriptions\n"
    "  ✅ Good: [\"oauth2\", \"pkce-flow\", \"authentication\"]\n"
    "  ❌ Bad: [\"login button\", \"user screen\", \"oauth setup\"]\n\n"
    "---\n\n"
    "## Narrative — The Most Important Field\n\n"
    "The narrative is the soul of the observation. It must be rich enough that a THIRD PERSON — "
    "someone who was not in this session — can read it and fully reconstruct:\n"
    "  1. What the developer was trying to accomplish (the goal and context)\n"
    "  2. What approach was taken and why (the reasoning and alternatives considered)\n"
    "  3. What obstacles, errors, or surprises were encountered (the friction)\n"
    "  4. How those obstacles were resolved — or why they were not (the turning point)\n"
    "  5. What the final state of the system is and what changed (the outcome)\n"
    "  6. What risks, caveats, or follow-up work remains (the aftermath)\n\n"
    "Write the narrative as a TECHNICAL STORY — not a log, not a list. "
    "Imagine a senior engineer reading this at 2am before touching this code. "
    "Give them everything they need.\n\n"
    "**Length**: 6–12 sentences. Never truncate meaningful context for brevity.\n\n"
    "**Tone**: Direct, precise, past-tense. No filler phrases like 'it is worth noting' or 'as mentioned'.\n\n"
    "**Structure** (follow this arc):\n"
    "  - SETUP: What was the starting state? What was the developer trying to do?\n"
    "  - FRICTION: What broke, confused, or blocked progress? What errors appeared? "
    "What did we try that did NOT work and why?\n"
    "  - PIVOT: What insight, discovery, or decision changed the direction?\n"
    "  - RESOLUTION: What was actually built, fixed, or decided? What does it do now?\n"
    "  - RESIDUE: What edge cases, TODOs, risks, or open questions remain?\n\n"
    "✅ GOOD NARRATIVE EXAMPLE:\n"
    "\"The developer was integrating Stripe webhooks into the checkout flow, expecting the "
    "existing event handler to cover payment_intent.succeeded. The initial implementation "
    "silently dropped events because the endpoint was not registered in the Stripe dashboard "
    "for live mode — only test mode. After adding logging, we discovered the signature "
    "verification was also failing due to a timezone mismatch in the timestamp tolerance "
    "window (Stripe requires ±300s, server clock was drifting ~400s). The clock sync issue "
    "was fixed by enabling NTP on the server. The webhook handler was then rewritten to "
    "handle idempotency via a processed_events table to prevent duplicate fulfillments on "
    "Stripe retries. Deployed and verified with three live test transactions. Remaining risk: "
    "the idempotency table has no TTL cleanup job yet — rows will accumulate indefinitely.\"\n\n"
    "❌ BAD NARRATIVE EXAMPLE:\n"
    "\"Implemented Stripe webhook handler. Fixed signature verification. Added idempotency. Tested and deployed.\"\n\n"
    "---\n\n"
    "## Obs Format\n\n"
    "Valid JSON only inside the tags. One obs block per response.\n\n"
    "<obs>\n"
    "{\n"
    "  \"type\": \"bugfix|feature|refactor|change|discovery|decision\",\n"
    "  \"title\": \"Short title capturing the core action\",\n"
    "  \"subtitle\": \"One sentence explanation (max 24 words)\",\n"
    "  \"narrative\": \"6-12 sentence technical story: Setup → Friction → Pivot → Resolution → Residue\",\n"
    "  \"facts\": [\"Concise, self-contained statement\"],\n"
    "  \"concepts\": [\"how-it-works\", \"pattern\"],\n"
    "  \"files_read\": [\"path/to/file\"],\n"
    "  \"files_modified\": [\"path/to/file\"]\n"
    "}\n"
    "</obs>\n"
)


def read_stdin_data() -> dict:
    """
    Read hook data from stdin.

    Claude Code passes hook data via stdin.
    Expected format: JSON with session_id, pid, cwd, etc.

    Returns:
        dict: Parsed hook data
    """
    try:
        data = sys.stdin.read().strip()
        if data:
            return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")

    return {}


def handle_session_start():
    """
    Handle the SessionStart hook.

    Expected stdin data (JSON):
    {
        "session_id": "uuid",
        "pid": 12345,
        "cwd": "/path/to/project",
        "kind": "interactive",
        "entrypoint": "cli"
    }
    """
    setup_logging(log_to_file=True, log_to_console=False)
    logger.info("=== SessionStart Handler ===")

    try:
        # Read hook data from stdin
        hook_data = read_stdin_data()

        # Extract session info
        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
        pid = hook_data.get("pid") or os.environ.get("CLAUDE_PID")
        cwd = hook_data.get("cwd") or os.environ.get("PWD") or os.environ.get("cwd")

        logger.info(f"Session start data: session_id={session_id}, pid={pid}, cwd={cwd}")

        # Quick check: ensure worker is running
        try:
            from src.workers.worker_checker import ensure_worker_quick_sync
            ensure_worker_quick_sync()
        except Exception as e:
            logger.debug(f"Worker check failed: {e}")

        # Retry any pending/failed tasks from previous session
        if session_id:
            retry_pending_tasks(session_id)

        # Store session_id to a temp file for Stop hook to read later
        if session_id:
            from src.common.paths import get_cloudbyte_dir
            session_id_file = get_cloudbyte_dir() / "current_session_id.txt"
            session_id_file.write_text(session_id)
            logger.debug(f"Stored session_id to: {session_id_file}")

        # Process session start
        result = process_session_start(
            session_id=session_id,
            pid=int(pid) if pid and pid.isdigit() else None,
            cwd=cwd,
        )

        if result.get("status") in ("success", "created"):
            logger.info(f"Session {result.get('status')}: {result.get('session_id')}")

            # Start LLM worker if enabled (non-blocking)
            try:
                from src.workers.llm_client import ensure_worker_running

                worker_started = ensure_worker_running()
                if worker_started:
                    logger.info("LLM worker started successfully")
                else:
                    logger.warning("LLM worker failed to start")
            except ImportError:
                logger.debug("Worker module not available")
            except Exception as e:
                logger.warning(f"Failed to start worker: {e}")

            # Output with OBS instruction injected into context
            logger.info("=" * 60)
            logger.info("INJECTING OBS INSTRUCTION INTO SESSIONSTART CONTEXT")
            logger.info("=" * 60)
            logger.info(f"Session ID: {result.get('session_id')}")
            logger.info(f"OBS instruction length: {len(OBS_INSTRUCTION)} characters")
            logger.info(f"Instruction preview: {OBS_INSTRUCTION[:100]}...")

            output_data = {
                "status": "success",
                "session_id": result.get("session_id"),
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": OBS_INSTRUCTION
                }
            }
            print(json.dumps(output_data))
            logger.info("✓ OBS instruction successfully output to Claude Code")
            logger.info("=" * 60)
        else:
            logger.warning(f"Session start returned: {result.get('status')}")
            print(json.dumps(result))

    except Exception as e:
        logger.error(f"Error in SessionStart handler: {e}", exc_info=True)
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)


def main():
    """Main entry point for the handler."""
    handle_session_start()


if __name__ == "__main__":
    main()
