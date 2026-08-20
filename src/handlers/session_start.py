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
from src.common.obs_instructions import CLAUDE_OBS_INSTRUCTION
from src.common.paths import get_claude_logs_dir
from src.core.event_processor import process_session_start


logger = get_logger(__name__)


# OBS instruction to inject into Claude's context
OBS_INSTRUCTION = CLAUDE_OBS_INSTRUCTION


def read_stdin_data() -> dict:
    """
    Read hook data from stdin.

    Claude Code passes hook data via stdin.
    Expected format: JSON with session_id, pid, cwd, etc.

    Returns:
        dict: Parsed hook data
    """
    try:
        # Decode UTF-8 explicitly - sys.stdin uses the locale codepage on
        # Windows (cp1252), which corrupts any non-ASCII field in the payload.
        raw = sys.stdin.buffer.read()
        data = raw.decode("utf-8", errors="replace").strip()
        if data:
            return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")
    except Exception as e:
        logger.error(f"Error reading stdin: {e}")

    return {}

def _ensure_mcp_permission() -> None:
    """
    Add record_observation to ~/.claude/settings.json permissions.allow.

    User-scope settings apply across ALL projects so the user is never
    prompted for permission when Claude calls record_observation.
    Idempotent - safe to run on every setup call.
    """
    import json as _json

    MCP_TOOL = "mcp__plugin_claude-telemetry_cloudbyte__record_observation"
    user_settings = Path.home() / ".claude" / "settings.json"

    try:
        settings = {}
        if user_settings.exists():
            raw_bytes = user_settings.read_bytes()
            has_bom = raw_bytes.startswith(b'\xef\xbb\xbf')

            if has_bom:
                logger.warning("settings.json has BOM - stripping and parsing with utf-8-sig")
            else:
                logger.info("settings.json has no BOM - parsing normally")

            try:
                raw = raw_bytes.decode("utf-8-sig").strip()
                if raw:
                    settings = _json.loads(raw)
                    if has_bom:
                        logger.info(f"BOM parse succeeded, existing keys: {list(settings.keys())}")
                    else:
                        logger.info(f"Normal parse succeeded, existing keys: {list(settings.keys())}")
                else:
                    logger.warning("settings.json exists but is empty")
            except Exception as e:
                logger.warning(f"Parse failed ({e}), starting fresh")
                import shutil, time
                backup = user_settings.with_suffix(f".bak.{int(time.time())}")
                shutil.copy2(user_settings, backup)
                logger.warning(f"Backed up unparseable settings to {backup}")
                settings = {}
        else:
            logger.info("settings.json does not exist, will create fresh")
            user_settings.parent.mkdir(parents=True, exist_ok=True)

        changed = False

        if "permissions" not in settings:
            settings["permissions"] = {}
        if "allow" not in settings["permissions"]:
            settings["permissions"]["allow"] = []
        if MCP_TOOL not in settings["permissions"]["allow"]:
            settings["permissions"]["allow"].append(MCP_TOOL)
            changed = True

        if "allowedTools" not in settings:
            settings["allowedTools"] = []
        if MCP_TOOL not in settings["allowedTools"]:
            settings["allowedTools"].append(MCP_TOOL)
            changed = True

        if changed:
            user_settings.write_text(
                _json.dumps(settings, indent=2),
                encoding="utf-8",
            )
            logger.info(f"settings.json updated, final keys: {list(settings.keys())}")
        else:
            logger.debug(f"MCP tool config already present, no write needed, keys: {list(settings.keys())}")

    except Exception as e:
        logger.warning(f"Could not update {user_settings}: {e}")

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
    setup_logging(log_to_file=True, log_to_console=False, log_dir=get_claude_logs_dir())
    logger.info("=== SessionStart Handler ===")

    try:
        # Read hook data from stdin
        hook_data = read_stdin_data()

        # Extract session info
        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
        pid = hook_data.get("pid") or os.environ.get("CLAUDE_PID")
        cwd = hook_data.get("cwd") or os.environ.get("PWD") or os.environ.get("cwd")

        logger.info(f"Session start data: session_id={session_id}, pid={pid}, cwd={cwd}")

        # Mark this session active in the shared registry so a Cursor session
        # (or another Claude Code window) sharing the same worker/dashboard
        # isn't torn down when this session ends first - see
        # src/common/session_registry.py.
        if session_id:
            try:
                from src.common.session_registry import register
                register(session_id, "claude_code")
            except Exception as e:
                logger.debug(f"session_registry.register failed: {e}")

        # Quick check: ensure worker is running
        try:
            from src.workers.worker_checker import ensure_worker_quick_sync
            ensure_worker_quick_sync()
        except Exception as e:
            logger.debug(f"Worker check failed: {e}")

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
