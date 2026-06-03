"""
CloudByte Main Entry Point

Handles all Claude Code hooks:
- Setup: Initialize database and directories
- SessionStart: Start a new session
- UserPromptSubmit: Log user prompts
- Stop: Process all session data (responses, tools, tokens) and cleanup
- SessionEnd: Log session end event
"""

import os
import sys
from src.common.time_utils import get_now_ist_iso, to_ist
from pathlib import Path

# Add src directory to path for imports
src_path = Path(__file__).parent
sys.path.insert(0, str(src_path))

from src.common.logging import get_logger, setup_logging
from src.common.paths import (
    get_cloudbyte_dir,
    get_data_dir,
    get_logs_dir,
    get_db_path,
    ensure_directories,
    get_config_file,
)
from src.db.manager import DatabaseManager, get_db_manager
from src.db.schema import initialize_database_with_manager, DatabaseSchema
from src.common.file_io import read_json, write_json

# Import handlers
from src.handlers.session_start import handle_session_start, _ensure_mcp_permission
from src.handlers.user_prompt import handle_user_prompt
from src.handlers.session_end import handle_session_end
from src.handlers.permission_request import handle_permission_request
from src.observations.writer import save_observation
import json as _json
import glob as _glob
from ftfy import fix_text as _fix_text

# Initialize logger
logger = get_logger(__name__)


def setup() -> None:
    """
    Setup hook - Initialize database and directories.
    Called when Claude Code starts with the plugin.
    """
    setup_logging(log_to_file=True, log_to_console=True)
    logger.info("=== CloudByte Setup Starting ===")

    try:
        # Ensure all directories exist
        ensure_directories()
        logger.info(f"CloudByte directory: {get_cloudbyte_dir()}")
        logger.info(f"Data directory: {get_data_dir()}")
        logger.info(f"Logs directory: {get_logs_dir()}")

        # Initialize database
        db_manager = get_db_manager()
        initialize_database_with_manager(db_manager)

        # Verify schema
        if DatabaseSchema.verify_schema(db_manager.get_connection()):
            logger.info("Database schema verified successfully")
        else:
            logger.warning("Database schema verification failed")

        # Create default config if not exists
        config_file = get_config_file()
        if not config_file.exists():
            default_config = {
                "version": "0.1.30",
                "created_at": get_now_ist_iso(),
                "settings": {
                    "log_level": "INFO",
                    "enable_observations": True,
                    "enable_summaries": True,
                    "alert_sound": "",  # Path to a WAV file for permission alerts. Leave empty to use platform default.
                    "alert_sound_name": "chime",  # Built-in sound name to use if alert_sound is empty. Options: "chime", "soft", "urgent".
                },
                "worker": {
                    "enabled": True,
                    "port": 8765,
                    "timeout": 120,
                    "shutdown_idle_seconds": 60,
                    "max_shutdown_wait_seconds": 300,
                    "max_retries": 3,
                    "cleanup_days": 7,
                },
                "llm": {
                    "default": "observation",
                    "endpoints": {
                        "observation": {
                            "provider": "openai",
                            "model": "gpt-4o",
                            "api_key": "",
                            "temperature": 0.7,
                            "max_tokens": 2000,
                            "base_url": None,
                        },
                        "summary": {
                            "provider": "anthropic",
                            "model": "claude-3-5-sonnet-20241022",
                            "api_key": "",
                            "temperature": 0.5,
                            "max_tokens": 4000,
                            "base_url": None,
                        },
                    },
                },
            }
            write_json(config_file, default_config)
            logger.info("Created default configuration")

        logger.info("=== CloudByte Setup Complete ===")

    except Exception as e:
        logger.error(f"Setup failed: {e}", exc_info=True)
        sys.exit(1)


def session_start() -> None:
    """
    SessionStart hook - Called when a new Claude session starts.
    Delegates to the session_start handler.
    """
    setup_logging(log_to_file=True, log_to_console=False)
    _ensure_mcp_permission()
    handle_session_start()


def user_prompt() -> None:
    """
    UserPromptSubmit hook - Called when user submits a prompt.
    Delegates to the user_prompt handler.
    """
    handle_user_prompt()


def observation() -> None:
    """
    Observation generation hook - Ready for LLM integration.

    To enable observation generation:
    1. Set API key in .cloudbyte/config.json under llm.endpoints.observation.api_key
    2. Decide when to call this (Stop hook vs SessionEnd vs after each prompt)
    3. Import and use generate_observation() from src.utils.llm.generators

    Example:
        from src.utils.llm import generate_observation, save_observation_to_db
        observation = generate_observation(session_id, prompt_id)
        if observation:
            save_observation_to_db(observation)
    """
    setup_logging(log_to_file=True, log_to_console=False)
    logger.debug("Observation generation hook - LLM integration ready but not yet triggered")
    # TODO: Implement observation generation timing and logic


def permission_request() -> None:
    """
    PermissionRequest hook - Called when Claude is about to ask the user
    to approve a tool/command permission.
    Plays an alert sound so the user notices the pending dialog.
    """
    handle_permission_request()


def stop() -> None:
    """
    Stop hook - Called when Claude stops processing.
    Processes only the current (most recent) prompt's response, tools, and tokens.
    Does NOT save prompts (they are saved by UserPromptSubmit hook).
    """
    setup_logging(log_to_file=True, log_to_console=False)
    logger.debug("Stop signal received - processing current prompt data")

    try:
        import json
        import os
        from src.integrations.claude.prompt_response import (
            extract_prompt_response_pairs,
            convert_pairs_to_db_format,
        )
        from src.db.writers import DatabaseWriter
        from src.integrations.claude.reader import get_claude_dir, read_jsonl_file, normalize_project_name
        from src.db.schema import migrate_schema
        from src.db.manager import get_db_connection
        from src.common.time_utils import get_now_ist_iso, to_ist
        import uuid
        MCP_OBS_TOOL = "mcp__plugin_claude-telemetry_cloudbyte__record_observation"

        # Run migrations before writing — handles mid-session plugin updates
        try:
            _db_mgr = get_db_manager()
            migrate_schema(_db_mgr.get_connection())
        except Exception as _me:
            logger.warning(f"Migration check failed in stop(): {_me}")

        # Read hook data from stdin
        hook_data = {}
        try:
            data = sys.stdin.read().strip()
            if data:
                hook_data = json.loads(data)
                logger.debug(f"Got hook data: {list(hook_data.keys())}")
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Could not read stdin: {e}")

        # Get session_id
        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
        if not session_id:
            logger.warning("No session_id found - unable to process response data")
            return

        logger.info(f"Processing current prompt data for: {session_id}")

        # Get session info to find JSONL path
        cwd = hook_data.get("cwd") or os.getcwd()
        project_name = normalize_project_name(cwd)
        claude_dir = get_claude_dir()
        jsonl_path = claude_dir / "projects" / project_name / f"{session_id}.jsonl"

        if not jsonl_path.exists():
            logger.warning(f"JSONL file not found: {jsonl_path}")
            return

        # Read events from JSONL — include subagent files if they exist
        events = list(read_jsonl_file(jsonl_path))

        subagents_dir = str(jsonl_path).replace(".jsonl", "") + os.sep + "subagents"
        if os.path.isdir(subagents_dir):
            for subagent_file in _glob.glob(os.path.join(subagents_dir, "*.jsonl")):
                try:
                    with open(subagent_file, encoding="utf-8", errors="ignore") as sf:
                        for line in sf:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                events.append(json.loads(line))
                            except Exception:
                                continue
                    logger.debug(f"Loaded subagent events from: {os.path.basename(subagent_file)}")
                except Exception as exc:
                    logger.warning(f"Failed reading subagent file {subagent_file}: {exc}")

        # Scan events for session title records (ai-title and custom-title)
        # Always overwrites when found (latest wins), preserves when not found (COALESCE)
        ai_title = None
        custom_title = None
        for event in events:
            if event.get("type") == "ai-title":
                ai_title = event.get("aiTitle")
            elif event.get("type") == "custom-title":
                custom_title = event.get("customTitle")

        if ai_title or custom_title:
            try:
                _title_conn = get_db_connection()
                _title_cursor = _title_conn.cursor()
                _title_cursor.execute(
                    """UPDATE SESSION
                       SET ai_title     = COALESCE(?, ai_title),
                           custom_title = COALESCE(?, custom_title)
                       WHERE session_id = ?""",
                    (ai_title, custom_title, session_id),
                )
                _title_conn.commit()
                logger.debug(f"Session titles updated — ai: {ai_title!r}, custom: {custom_title!r}")
            except Exception as _te:
                logger.warning(f"Could not update session titles: {_te}")

        pairs = extract_prompt_response_pairs(events)

        # Filter out hook/system output (e.g., "● Ran X hooks", "⎿")
        def is_hook_output(text: str) -> bool:
            """Check if text looks like hook system output."""
            if not text:
                return False
            text_stripped = text.strip()
            # Hook output patterns
            if text_stripped.startswith("● Ran") and "hooks" in text_stripped:
                return True
            if text_stripped.startswith("⎿"):
                return True
            return False

        # Filter out hook output from pairs
        filtered_pairs = []
        for pair in pairs:
            prompt_text = pair.get("prompt", "")
            if not is_hook_output(prompt_text):
                filtered_pairs.append(pair)

        logger.debug(f"Filtered {len(pairs) - len(filtered_pairs)} hook output pairs from {len(pairs)} total")

        if not filtered_pairs:
            logger.debug("No non-hook prompt/response pairs found")
            return

        # Get only the MOST RECENT prompt/response pair (excluding hook output)
        most_recent_pair = filtered_pairs[-1]
        jsonl_prompt_id = most_recent_pair["prompt_id"]
        prompt_text = _fix_text(most_recent_pair.get("prompt", ""))
        logger.info(f"Most recent pair: prompt_id={jsonl_prompt_id}, prompt=\"{prompt_text[:50]}\"")

        # Normalize whitespace: collapse all runs of whitespace (including \n, \r, \t) to a
        # single space. Used for Python-side comparison so both DB text and JSONL text go
        # through the same re.sub — SQL REPLACE can't collapse consecutive spaces reliably.
        import re as _re

        def _norm(text: str) -> str:
            return _re.sub(r'\s+', ' ', (text or '').strip().lower())

        prompt_text_norm = _norm(prompt_text)

        # Find the matching prompt_id in the database (by content and session_id).
        # Fetch recent unresponded prompts and compare in Python so both sides use
        # identical whitespace normalization.
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.prompt_id, p.prompt FROM USER_PROMPT p
            LEFT JOIN RESPONSE r ON r.prompt_id = p.prompt_id
            WHERE p.session_id = ? AND r.message_id IS NULL
            ORDER BY p.timestamp DESC LIMIT 30
        """, (session_id,))
        candidates = cursor.fetchall()

        db_prompt_id = None
        prompt_created = False

        # Pass 1 — normalized exact match
        for cand_id, cand_text in candidates:
            if _norm(cand_text) == prompt_text_norm:
                db_prompt_id = cand_id
                logger.info(f"Found DB prompt_id (norm-exact): {db_prompt_id} (JSONL has: {jsonl_prompt_id})")
                break

        if not db_prompt_id:
            logger.info(f"Normalized exact match not found for: \"{prompt_text[:50]}\", trying fuzzy match...")
            # Pass 2 — fuzzy: one normalized text is a substring of the other
            for cand_id, cand_text in candidates:
                cand_norm = _norm(cand_text)
                if prompt_text_norm in cand_norm or cand_norm in prompt_text_norm:
                    db_prompt_id = cand_id
                    logger.info(f"Fuzzy match found: {db_prompt_id}")
                    break

        if not db_prompt_id:
            # No match found - create the prompt in the database with retry logic
            logger.warning(f"No matching prompt found in DB, creating new record for: \"{prompt_text[:50]}\"")

            prompt_rec = most_recent_pair.get("prompt_rec", {})
            new_prompt_id = jsonl_prompt_id  # Use JSONL's prompt_id

            # Retry logic for INSERT
            import time
            max_retries = 10
            retry_delay = 0.3
            insert_success = False

            for attempt in range(max_retries):
                try:
                    # Get fresh connection for each attempt
                    conn = get_db_connection()
                    cursor = conn.cursor()

                    cursor.execute("""
                        INSERT INTO USER_PROMPT (
                            prompt_id, session_id, uuid, parent_uuid, prompt, timestamp,
                            entrypoint, claude_version, git_branch, permission_mode,
                            jsonl_prompt_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        new_prompt_id,
                        session_id,
                        prompt_rec.get("uuid", str(uuid.uuid4())),
                        prompt_rec.get("parentUuid"),
                        prompt_text,
                        to_ist(prompt_rec.get("timestamp")),
                        prompt_rec.get("entrypoint"),
                        prompt_rec.get("version"),
                        prompt_rec.get("gitBranch"),
                        prompt_rec.get("permissionMode"),
                        new_prompt_id,  # jsonl_prompt_id = same as prompt_id in fallback
                    ))
                    conn.commit()
                    insert_success = True
                    break
                except Exception as e:
                    if "database is locked" in str(e) and attempt < max_retries - 1:
                        if attempt == 0:
                            logger.warning(f"Database locked on prompt insert, retrying (up to {max_retries} attempts)")
                        time.sleep(retry_delay)
                        continue
                    else:
                        logger.error(f"Failed to insert prompt after {max_retries} attempts: {e}")
                        # Fall through to use jsonl_prompt_id anyway
                        break

            db_prompt_id = new_prompt_id
            prompt_created = True
            logger.info(f"Created new prompt record: {db_prompt_id}")

        if not prompt_created:
            # Store real JSONL ID in jsonl_prompt_id column.
            # prompt_id (auto-gen) stays unchanged to preserve relationships with responses/tools/tokens.
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                _prompt_rec = most_recent_pair.get("prompt_rec", {})
                cursor.execute(
                    """UPDATE USER_PROMPT
                       SET jsonl_prompt_id = ?,
                           timestamp = ?,
                           parent_uuid = COALESCE(parent_uuid, ?),
                           entrypoint = ?,
                           claude_version = ?,
                           git_branch = ?,
                           permission_mode = ?
                       WHERE prompt_id = ?""",
                    (
                        jsonl_prompt_id,
                        to_ist(_prompt_rec.get("timestamp")),
                        _prompt_rec.get("parentUuid"),
                        _prompt_rec.get("entrypoint"),
                        _prompt_rec.get("version"),
                        _prompt_rec.get("gitBranch"),
                        _prompt_rec.get("permissionMode"),
                        db_prompt_id,
                    )
                )
                conn.commit()
                logger.info(f"Stored jsonl_prompt_id={jsonl_prompt_id}... for prompt_id={db_prompt_id}...")
            except Exception as e:
                logger.warning(f"Could not store jsonl_prompt_id: {e}")

            # prompt_id is stable — use as effective_prompt_id for all child records
            effective_prompt_id = db_prompt_id
        else:
            effective_prompt_id = db_prompt_id  # newly created by stop(), already correct

        # Convert pairs to DB format
        db_data = convert_pairs_to_db_format(pairs)

        # Remove user_prompts - we already updated the existing one
        db_data["user_prompts"] = []

        # Initialize writer and write data
        writer = DatabaseWriter()
        counts = {
            "responses": 0,
            "tools": 0,
            "thinking": 0,
            "io_tokens": 0,
            "tool_tokens": 0,
        }

        # Helper to remap prompt_id to effective_prompt_id (from DB, not JSONL)
        def remap_to_jsonl_id(data_item, prompt_id_field="prompt_id"):
            item = data_item.copy()
            item[prompt_id_field] = effective_prompt_id
            return item

        # Write responses (remap prompt_id to DB prompt_id)
        for response in db_data.get("responses", []):
            if response["prompt_id"] == jsonl_prompt_id:
                remapped = remap_to_jsonl_id(response)
                if writer.write_response(remapped):
                    counts["responses"] += 1

        # Write tools (remap prompt_id to DB prompt_id)
        for tool in db_data.get("tools", []):
            if tool["prompt_id"] == jsonl_prompt_id:
                remapped = remap_to_jsonl_id(tool)
                if writer.write_tool(remapped):
                    counts["tools"] += 1
                    # Don't count record_observation toward LLM task queue
                    if tool.get("tool_name") == MCP_OBS_TOOL:
                        counts["tools"] -= 1

        # Write thinking (remap prompt_id to DB prompt_id)
        for thinking in db_data.get("thinking", []):
            if thinking["prompt_id"] == jsonl_prompt_id:
                remapped = remap_to_jsonl_id(thinking)
                if writer.write_thinking(remapped):
                    counts["thinking"] += 1

        # Write IO tokens (remap prompt_id to DB prompt_id)
        for io_tokens in db_data.get("io_tokens", []):
            if io_tokens["prompt_id"] == jsonl_prompt_id:
                remapped = remap_to_jsonl_id(io_tokens)
                if writer.write_io_tokens(remapped):
                    counts["io_tokens"] += 1

        # Write tool tokens (only for tools matching current prompt)
        for tool_tokens in db_data.get("tool_tokens", []):
            tool_id = tool_tokens["tool_id"]
            for tool in db_data.get("tools", []):
                if tool["tool_id"] == tool_id and tool["prompt_id"] == jsonl_prompt_id:
                    if writer.write_tool_tokens(tool_tokens):
                        counts["tool_tokens"] += 1
                    break

        logger.info(f"Current prompt data stored during Stop: {counts}")

        # # Extract inline obs blocks from response and save to HOOK_OBSERVATION
        # try:
        #     from src.observations.extractor import extract_and_parse_obs
        #     from src.observations.writer import save_observation

        #     # Get the most recent response text
        #     most_recent_response = None
        #     for response in db_data.get("responses", []):
        #         if response["prompt_id"] == effective_prompt_id:
        #             most_recent_response = response.get("response_text", "")
        #             break

        #     if most_recent_response:
        #         observations = extract_and_parse_obs(most_recent_response)

        #         if observations:
        #             logger.info(f"Found {len(observations)} obs blocks in response")

        #             for obs_data in observations:
        #                 obs_id = save_observation(
        #                     session_id=session_id,
        #                     prompt_id=effective_prompt_id,
        #                     obs_data=obs_data
        #                 )
        #                 if obs_id:
        #                     logger.info(f"Saved hook observation {obs_id}: {obs_data.get('title', 'Untitled')}")
        #                 else:
        #                     logger.warning(f"Failed to save observation: {obs_data.get('title', 'Unknown')}")
        #         else:
        #             logger.debug("No obs blocks found in response")
        # except Exception as obs_error:
        #     logger.error(f"Hook observation extraction failed: {obs_error}", exc_info=True)
        
        logger.debug("Observation capture handled by MCP tool (record_observation)")
        # Extract observation from MCP tool call and save to HOOK_OBSERVATION
        try:
            for tool in db_data.get("tools", []):
                if tool["prompt_id"] != jsonl_prompt_id:
                    continue
                if tool.get("tool_name") != MCP_OBS_TOOL:
                    continue

                raw_input = tool.get("input_json", "{}")
                try:
                    obs_data = _json.loads(raw_input) if isinstance(raw_input, str) else raw_input
                except Exception:
                    obs_data = {}
                if not obs_data.get("title"):
                    continue

                obs_id = save_observation(
                    session_id=session_id,
                    prompt_id=effective_prompt_id,
                    obs_data=obs_data,
                )
                if obs_id:
                    logger.info(f"Saved MCP observation: {obs_data.get('title', 'Untitled')}")
                else:
                    logger.warning(f"Failed to save MCP observation: {obs_data.get('title', 'Unknown')}")

        except Exception as obs_error:
            logger.warning(f"MCP observation save failed: {obs_error}")
        # Queue observation task if tools were used
        if counts["tools"] > 0:
            try:
                from src.workers.llm_client import queue_observation_task
                from src.common.paths import get_config_file
                from src.common.file_io import read_json

                # Check if observations are enabled (default: True)
                observations_enabled = True
                config_file = get_config_file()
                if config_file.exists():
                    config = read_json(config_file)
                    settings = config.get("settings", {})
                    observations_enabled = settings.get("enable_observations", True)

                if observations_enabled:
                    logger.info(f"Queueing observation task for prompt with {counts['tools']} tools")

                    # Use HTTP to queue task (avoids database lock)
                    result = queue_observation_task(session_id, effective_prompt_id)

                    if result.get("status") == "queued":
                        logger.info(f"Observation task queued: {result['task_id']}")
                    else:
                        logger.warning(f"Failed to queue observation task: {result.get('message', 'Unknown error')}")
                else:
                    logger.info("Observations disabled in config, skipping queue")
            except ImportError:
                logger.debug("Worker module not available, skipping observation queue")
            except Exception as obs_error:
                # Don't fail the stop hook if observation queuing fails
                logger.warning(f"Observation queue failed (will retry on next prompt): {obs_error}")

        # ── Response security scan ────────────────────────────────────────────
        # Runs after all DB writes. Response is never blocked — findings are
        # logged to SECURITY_SCAN_EVENT with masked text and surfaced as a
        # UI notice via systemMessage.
        try:
            from src.security.config import load_security_config
            from src.security.scanner import scan_text
            from src.security.masker import mask_text as _mask_response
            from src.security.db_writer import write_finding

            _sec_cfg = load_security_config(cwd=cwd)
            if _sec_cfg.enabled and _sec_cfg.scope == "both":
                _response_text = most_recent_pair.get("response", "")
                if _response_text:
                    _sec_result = scan_text(_response_text, _sec_cfg.response_config)
                    if _sec_result.findings:
                        _masked_response = _mask_response(_response_text, _sec_result.findings)
                        write_finding(
                            session_id=session_id,
                            scan_target="response",
                            result=_sec_result,
                            blocked=False,
                            masked_text=_masked_response,
                        )
                        logger.info(
                            f"Response scan: {len(_sec_result.findings)} finding(s) logged"
                            f" [{_sec_result.scan_strategy}, {_sec_result.scan_ms}ms]"
                        )
                        _summary = ", ".join(f.detector for f in _sec_result.findings[:3])
                        if len(_sec_result.findings) > 3:
                            _summary += f" +{len(_sec_result.findings) - 3} more"
                        print(_json.dumps({
                            "systemMessage": (
                                f"⚠️ Security: {len(_sec_result.findings)} sensitive item(s) detected"
                                f" in Claude's response ({_summary})."
                                f" Event logged to telemetry."
                            )
                        }))
        except Exception as _sec_err:
            logger.warning(f"Response security scan error (non-fatal): {_sec_err}")

        # Close database connection
        from src.db.manager import close_db
        close_db()
        logger.debug("Database connection closed")

    except Exception as e:
        logger.error(f"Stop handling failed: {e}", exc_info=True)


def session_end() -> None:
    """
    SessionEnd hook - Called when a Claude session ends.
    Finalize session data and create summaries.
    Delegates to the session_end handler.
    """
    handle_session_end()


def main():
    """
    Main entry point - dispatches to appropriate handler based on args.
    Called from hooks.json with command like:
        uv run --directory "D:/CloudByte_plugin/CloudByte" -m src.main setup
    """
    if len(sys.argv) < 2:
        print("Usage: python -m src.main <command>")
        print("Commands: setup, session_start, user_prompt, observation, stop, session_end")
        sys.exit(1)

    command = sys.argv[1]

    handlers = {
        "setup": setup,
        "session_start": session_start,
        "user_prompt": user_prompt,
        "observation": observation,
        "stop": stop,
        "session_end": session_end,
        "permission_request": permission_request,
    }

    handler = handlers.get(command)
    if handler is None:
        print(f"Unknown command: {command}")
        print(f"Available commands: {', '.join(handlers.keys())}")
        sys.exit(1)

    try:
        handler()
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Command '{command}' failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()