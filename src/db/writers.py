"""
Database Writers Module

Writes extracted data to the CloudByte database.
"""

import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.common.logging import get_logger
from src.db.manager import DatabaseManager, get_db_connection, retry_on_lock


logger = get_logger(__name__)


class DatabaseWriter:
    """
    Handles writing data to CloudByte database tables.
    """

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        """
        Initialize the database writer.

        Args:
            db_manager: Optional DatabaseManager instance
        """
        self.db_manager = db_manager

    def get_connection(self) -> sqlite3.Connection:
        """Get database connection."""
        if self.db_manager:
            return self.db_manager.get_connection()
        return get_db_connection()

    @retry_on_lock(retries=3, delay=0.5)
    def write_project(self, project_data: Dict[str, Any]) -> bool:
        """
        Ensure a project record exists. Does NOT update an existing row.

        project_id is a hash of the (normalized) path, so path can never
        legitimately differ for an existing project_id - and name/created_at
        should only ever be set by whichever plugin (Claude or Cursor) sees
        this project first. A project is shared across both plugins, so this
        must stay a no-op on conflict rather than overwriting - otherwise
        created_at resets to "now" and name flip-flops between Claude's and
        Cursor's naming conventions every time either plugin's sessionStart
        runs against an already-known project.

        Args:
            project_data: Dict with project_id, name, path, created_at

        Returns:
            bool: True if successful (including when the project already
            existed and this call was a no-op)
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR IGNORE INTO PROJECT (project_id, name, path, created_at)
                VALUES (?, ?, ?, ?)
            """, (
                project_data["project_id"],
                project_data["name"],
                project_data["path"],
                project_data["created_at"],
            ))

            conn.commit()
            logger.debug(f"Wrote project: {project_data['name']}")
            return True

        except Exception as e:
            logger.error(f"Error writing project: {e}")
            return False

    @retry_on_lock(retries=3, delay=0.5)
    def write_session(self, session_data: Dict[str, Any]) -> bool:
        """
        Insert or update a session record.

        Args:
            session_data: Dict with session_id, project_id, cwd, transcript_path, created_at, kind, entrypoint.
                Optional "client" key ('claude_code' | 'cursor') — defaults to 'claude_code'
                for callers that don't know about multi-IDE support yet.

        Returns:
            bool: True if successful
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO SESSION
                (session_id, project_id, cwd, transcript_path, created_at, client)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                session_data["session_id"],
                session_data["project_id"],
                session_data["cwd"],
                session_data["transcript_path"],
                session_data["created_at"],
                session_data.get("client", "claude_code"),
            ))

            conn.commit()
            logger.debug(f"Wrote session: {session_data['session_id']}")
            return True

        except Exception as e:
            logger.error(f"Error writing session: {e}")
            return False

    @retry_on_lock(retries=3, delay=0.5)
    def update_session_transcript_path(self, session_id: str, transcript_path: str) -> bool:
        """
        Backfill SESSION.transcript_path if it isn't set yet.

        No-op if the session already has a transcript_path — safe to call on
        every prompt without re-writing an already-known value.

        Returns:
            bool: True if the update executed without error (regardless of whether
            a row was actually changed)
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE SESSION
                SET transcript_path = ?
                WHERE session_id = ? AND (transcript_path IS NULL OR transcript_path = '')
            """, (transcript_path, session_id))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error updating session transcript_path: {e}")
            return False

    @retry_on_lock(retries=3, delay=0.5)
    def update_session_end(
        self, session_id: str, ended_at: str, end_reason: Optional[str], final_status: Optional[str]
    ) -> bool:
        """
        Set SESSION.ended_at/end_reason/final_status - the terminal outcome
        of a session, so unlike the backfill-style updates above this always
        overwrites, no "already known, don't touch" guard needed.

        Returns:
            bool: True only if a row was actually matched and updated.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE SESSION
                SET ended_at = ?, end_reason = ?, final_status = ?
                WHERE session_id = ?
            """, (ended_at, end_reason, final_status, session_id))

            conn.commit()
            return cursor.rowcount > 0

        except Exception as e:
            logger.error(f"Error updating session end: {e}")
            return False

    def get_session_ai_title(self, session_id: str) -> Optional[str]:
        """Read SESSION.ai_title, or None if unset/session doesn't exist."""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT ai_title FROM SESSION WHERE session_id = ?", (session_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Error reading session ai_title: {e}")
            return None

    @retry_on_lock(retries=3, delay=0.5)
    def update_session_ai_title(self, session_id: str, ai_title: str) -> bool:
        """
        Backfill SESSION.ai_title if it isn't set yet.

        No-op if the session already has an ai_title — safe to call
        repeatedly without re-writing an already-known value.

        Returns:
            bool: True if the update executed without error (regardless of whether
            a row was actually changed)
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE SESSION
                SET ai_title = ?
                WHERE session_id = ? AND (ai_title IS NULL OR ai_title = '')
            """, (ai_title, session_id))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error updating session ai_title: {e}")
            return False

    @retry_on_lock(retries=3, delay=0.5)
    def update_user_prompt_status(self, prompt_id: str, status: str) -> bool:
        """
        Set USER_PROMPT.status for a specific prompt - the stop hook's
        terminal outcome for that turn ('completed' / 'aborted' for Cursor).

        Unlike the backfill-style updates above, this always overwrites -
        status reflects the final state of one specific turn, so there's
        no "already known, don't touch" case to guard against.

        Returns:
            bool: True only if a row was actually matched and updated -
            False if prompt_id doesn't exist yet (e.g. beforeSubmitPrompt's
            write hasn't landed), so callers can tell a real no-op from a
            successful update instead of both looking like success.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE USER_PROMPT SET status = ? WHERE prompt_id = ?",
                (status, prompt_id),
            )
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Error updating user prompt status: {e}")
            return False

    @retry_on_lock(retries=3, delay=0.5)
    def write_raw_log(self, log_data: Dict[str, Any]) -> bool:
        """
        Insert a raw log record.

        Note: Session should already exist (initialized by UserPromptSubmit handler).

        Args:
            log_data: Dict with id, session_id, uuid, parent_uuid, type, raw_json, timestamp

        Returns:
            bool: True if successful
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            session_id = log_data["session_id"]

            cursor.execute("""
                INSERT OR REPLACE INTO RAW_LOG
                (id, session_id, uuid, parent_uuid, type, raw_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                log_data["id"],
                session_id,
                log_data.get("uuid"),
                log_data.get("parent_uuid"),
                log_data["type"],
                log_data["raw_json"],
                log_data["timestamp"],
            ))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error writing raw log: {e}")
            return False

    @retry_on_lock(retries=3, delay=0.5)
    def write_user_prompt(self, prompt_data: Dict[str, Any]) -> bool:
        """
        Insert a user prompt record.

        Skips if prompt_id already exists (idempotent).

        Note: Session should already exist (initialized by UserPromptSubmit handler).

        Args:
            prompt_data: Dict with prompt_id, session_id, uuid, parent_uuid, prompt, timestamp

        Returns:
            bool: True if written or already exists, False on error
        """
        try:
            # Validate prompt text exists
            prompt_text = prompt_data.get("prompt", "").strip()
            if not prompt_text:
                logger.debug(f"Skipping user prompt {prompt_data.get('prompt_id')}: empty prompt text")
                return False

            conn = self.get_connection()
            cursor = conn.cursor()

            # Check if prompt_id already exists - update timestamp if so (for correct JSONL ordering)
            prompt_id = prompt_data["prompt_id"]
            cursor.execute("SELECT 1 FROM USER_PROMPT WHERE prompt_id = ? LIMIT 1", (prompt_id,))
            if cursor.fetchone() is not None:
                # Update timestamp with correct value from JSONL (more reliable than hook timestamp)
                cursor.execute("""
                    UPDATE USER_PROMPT
                    SET timestamp = ?
                    WHERE prompt_id = ?
                """, (prompt_data["timestamp"], prompt_id))
                conn.commit()
                logger.debug(f"Updated user prompt timestamp: {prompt_id} → {prompt_data['timestamp']}")
                return True

            session_id = prompt_data["session_id"]

            cursor.execute("""
                INSERT INTO USER_PROMPT
                (prompt_id, session_id, uuid, parent_uuid, prompt, timestamp, client_version,
                 attachments, mode, git_branch, entrypoint)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                prompt_id,
                session_id,
                prompt_data.get("uuid"),
                prompt_data.get("parent_uuid"),
                prompt_text,
                prompt_data["timestamp"],
                prompt_data.get("client_version"),
                prompt_data.get("attachments"),
                prompt_data.get("mode"),
                prompt_data.get("git_branch"),
                prompt_data.get("entrypoint"),
            ))

            conn.commit()
            logger.debug(f"Wrote user prompt: {prompt_id}")
            return True

        except Exception as e:
            logger.error(f"Error writing user prompt: {e}")
            return False

    def write_response(self, response_data: Dict[str, Any]) -> bool:
        """
        Insert a response record.

        Args:
            response_data: Dict with message_id, prompt_id, uuid, parent_uuid, response_text, model, timestamp

        Returns:
            bool: True if successful
        """
        try:
            # Validate response text exists
            response_text = response_data.get("response_text", "").strip()
            if not response_text:
                logger.debug(f"Skipping response {response_data.get('message_id')}: empty response text")
                return False

            conn = self.get_connection()
            cursor = conn.cursor()

            # Check if message_id already exists
            message_id = response_data["message_id"]
            cursor.execute("SELECT 1 FROM RESPONSE WHERE message_id = ? LIMIT 1", (message_id,))
            if cursor.fetchone() is not None:
                logger.debug(f"Skipping response {message_id}: already exists")
                return False

            cursor.execute("""
                INSERT INTO RESPONSE
                (message_id, prompt_id, uuid, parent_uuid, response_text, model, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                message_id,
                response_data["prompt_id"],
                response_data.get("uuid"),
                response_data.get("parent_uuid"),
                response_text,
                response_data.get("model"),
                response_data["timestamp"],
            ))

            conn.commit()
            logger.debug(f"Wrote response: {message_id}")
            return True

        except Exception as e:
            logger.error(f"Error writing response: {e}")
            return False

    def write_tool(self, tool_data: Dict[str, Any]) -> bool:
        """
        Insert a tool record.

        Args:
            tool_data: Dict with tool_id, prompt_id, uuid, parent_uuid, tool_name, model, input_json, output_json, timestamp

        Returns:
            bool: True if successful
        """
        try:
            import json

            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO TOOL
                (tool_id, prompt_id, uuid, parent_uuid, tool_name, model, input_json, output_json, timestamp, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tool_data["tool_id"],
                tool_data["prompt_id"],
                tool_data.get("uuid"),
                tool_data.get("parent_uuid"),
                tool_data["tool_name"],
                tool_data.get("model"),
                json.dumps(tool_data.get("input_json")) if tool_data.get("input_json") else None,
                json.dumps(tool_data.get("output_json")) if tool_data.get("output_json") else None,
                tool_data["timestamp"],
                tool_data.get("duration_ms"),
            ))

            conn.commit()
            logger.debug(f"Wrote tool: {tool_data['tool_name']}")
            return True

        except Exception as e:
            logger.error(f"Error writing tool: {e}")
            return False

    def write_thinking(self, thinking_data: Dict[str, Any]) -> bool:
        """
        Insert a thinking record.

        Args:
            thinking_data: Dict with thinking_id, prompt_id, uuid, parent_uuid, content, signature, timestamp

        Returns:
            bool: True if successful
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO THINKING
                (thinking_id, prompt_id, uuid, parent_uuid, content, signature, timestamp, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                thinking_data["thinking_id"],
                thinking_data["prompt_id"],
                thinking_data.get("uuid"),
                thinking_data.get("parent_uuid"),
                thinking_data.get("content"),
                thinking_data.get("signature"),
                thinking_data["timestamp"],
                thinking_data.get("duration_ms"),
            ))

            conn.commit()
            logger.debug(f"Wrote thinking: {thinking_data['thinking_id']}")
            return True

        except Exception as e:
            logger.error(f"Error writing thinking: {e}")
            return False

    def write_io_tokens(self, token_data: Dict[str, Any]) -> bool:
        """
        Insert an IO tokens record.

        Args:
            token_data: Dict with id, prompt_id, message_id, token_type, input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens

        Returns:
            bool: True if successful
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Verify message_id exists in RESPONSE table, otherwise set to None
            message_id = token_data.get("message_id")
            if message_id:
                cursor.execute("SELECT 1 FROM RESPONSE WHERE message_id = ? LIMIT 1", (message_id,))
                if cursor.fetchone() is None:
                    # message_id doesn't exist in RESPONSE table, set to None
                    logger.debug(f"message_id {message_id} not found in RESPONSE table, setting to None")
                    message_id = None

            cursor.execute("""
                INSERT OR REPLACE INTO IO_TOKENS
                (id, prompt_id, message_id, token_type, input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token_data["id"],
                token_data["prompt_id"],
                message_id,
                token_data["token_type"],
                token_data.get("input_tokens"),
                token_data.get("cache_creation_tokens"),
                token_data.get("cache_read_tokens"),
                token_data.get("output_tokens"),
            ))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error writing IO tokens: {e}")
            return False

    def write_tool_tokens(self, token_data: Dict[str, Any]) -> bool:
        """
        Insert a tool tokens record.

        Args:
            token_data: Dict with id, tool_id, input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens

        Returns:
            bool: True if successful
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Verify tool_id exists in TOOL table
            tool_id = token_data.get("tool_id")
            if tool_id:
                cursor.execute("SELECT 1 FROM TOOL WHERE tool_id = ? LIMIT 1", (tool_id,))
                if cursor.fetchone() is None:
                    # tool_id doesn't exist in TOOL table, skip this token record
                    logger.debug(f"tool_id {tool_id} not found in TOOL table, skipping token record")
                    return False

            cursor.execute("""
                INSERT OR REPLACE INTO TOOL_TOKENS
                (id, tool_id, input_tokens, cache_creation_tokens, cache_read_tokens, output_tokens)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                token_data["id"],
                tool_id,
                token_data.get("input_tokens"),
                token_data.get("cache_creation_tokens"),
                token_data.get("cache_read_tokens"),
                token_data.get("output_tokens"),
            ))

            conn.commit()
            return True

        except Exception as e:
            logger.error(f"Error writing tool tokens: {e}")
            return False

    def write_observation(self, observation_data: Dict[str, Any]) -> bool:
        """
        Insert an observation record.

        NOTE: Currently disabled - not writing to OBSERVATION table.

        Args:
            observation_data: Dict with observation data

        Returns:
            bool: True (disabled, returns True without writing)
        """
        # DISABLED: Not writing observations to database
        logger.debug("Observation write disabled - skipping")
        return True

    def write_session_summary(self, summary_data: Dict[str, Any]) -> bool:
        """
        Insert a session summary record.

        NOTE: Currently disabled - not writing to SESSION_SUMMARY table.

        Args:
            summary_data: Dict with summary data

        Returns:
            bool: True (disabled, returns True without writing)
        """
        # DISABLED: Not writing session summaries to database
        logger.debug("Session summary write disabled - skipping")
        return True

    def write_batch(self, extracted_data: Dict[str, List[Dict[str, Any]]]) -> Dict[str, int]:
        """
        Write a batch of extracted data to the database.

        Args:
            extracted_data: Dict from extract_all_from_event with lists of data for each table

        Returns:
            Dict with counts of records written per table
        """
        counts = {
            "raw_log": 0,
            "user_prompts": 0,
            "responses": 0,
            "tools": 0,
            "thinking": 0,
            "io_tokens": 0,
            "tool_tokens": 0,
            "observations": 0,
            "session_summaries": 0,
        }

        # Write raw logs
        for log in extracted_data.get("raw_log", []):
            if self.write_raw_log(log):
                counts["raw_log"] += 1

        # Write user prompts
        for prompt in extracted_data.get("user_prompts", []):
            if self.write_user_prompt(prompt):
                counts["user_prompts"] += 1

        # Write responses
        for response in extracted_data.get("responses", []):
            if self.write_response(response):
                counts["responses"] += 1

        # Write tools
        for tool in extracted_data.get("tools", []):
            if self.write_tool(tool):
                counts["tools"] += 1

        # Write thinking
        for thinking in extracted_data.get("thinking", []):
            if self.write_thinking(thinking):
                counts["thinking"] += 1

        # Write IO tokens
        for tokens in extracted_data.get("io_tokens", []):
            if self.write_io_tokens(tokens):
                counts["io_tokens"] += 1

        # Write tool tokens
        for tokens in extracted_data.get("tool_tokens", []):
            if self.write_tool_tokens(tokens):
                counts["tool_tokens"] += 1

        # DISABLED: Skip observations and session summaries
        logger.info(f"Batch write completed: {counts}")
        return counts


# Convenience functions for quick writes

def write_project(project_data: Dict[str, Any]) -> bool:
    """Quick write a project record."""
    writer = DatabaseWriter()
    return writer.write_project(project_data)


def write_session(session_data: Dict[str, Any]) -> bool:
    """Quick write a session record."""
    writer = DatabaseWriter()
    return writer.write_session(session_data)


def write_user_prompt(prompt_data: Dict[str, Any]) -> bool:
    """Quick write a user prompt record."""
    writer = DatabaseWriter()
    return writer.write_user_prompt(prompt_data)
