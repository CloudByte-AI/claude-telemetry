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
        Insert or update a project record.

        Args:
            project_data: Dict with project_id, name, path, created_at

        Returns:
            bool: True if successful
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO PROJECT (project_id, name, path, created_at)
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
            session_data: Dict with session_id, project_id, cwd, jsonl_file, created_at, kind, entrypoint

        Returns:
            bool: True if successful
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO SESSION
                (session_id, project_id, cwd, jsonl_file, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                session_data["session_id"],
                session_data["project_id"],
                session_data["cwd"],
                session_data["jsonl_file"],
                session_data["created_at"],
            ))

            conn.commit()
            logger.debug(f"Wrote session: {session_data['session_id']}")
            return True

        except Exception as e:
            logger.error(f"Error writing session: {e}")
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

            # Check if prompt_id already exists (idempotent)
            prompt_id = prompt_data["prompt_id"]
            cursor.execute("SELECT 1 FROM USER_PROMPT WHERE prompt_id = ? LIMIT 1", (prompt_id,))
            if cursor.fetchone() is not None:
                logger.debug(f"User prompt {prompt_id} already exists, skipping")
                return True

            session_id = prompt_data["session_id"]

            cursor.execute("""
                INSERT INTO USER_PROMPT
                (prompt_id, session_id, uuid, parent_uuid, prompt, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                prompt_id,
                session_id,
                prompt_data.get("uuid"),
                prompt_data.get("parent_uuid"),
                prompt_text,
                prompt_data["timestamp"],
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
                (tool_id, prompt_id, uuid, parent_uuid, tool_name, model, input_json, output_json, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                (thinking_id, prompt_id, uuid, parent_uuid, content, signature, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                thinking_data["thinking_id"],
                thinking_data["prompt_id"],
                thinking_data.get("uuid"),
                thinking_data.get("parent_uuid"),
                thinking_data.get("content"),
                thinking_data.get("signature"),
                thinking_data["timestamp"],
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
