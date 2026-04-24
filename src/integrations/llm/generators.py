"""
LLM-based Generators for Observations and Summaries

High-level functions that orchestrate LLM calls to generate observations
and session summaries from database data.

Updated to support:
- Multi-tool observation generation
- Context from past observations
- Skip logic for routine operations
- Bullet-point formatted summaries
- Pydantic schema validation for structured extraction
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.common.logging import get_logger
from src.integrations.llm.client import LLMClient, LLMError
from src.integrations.llm.config import get_endpoint_config, LLMConfigError
from src.integrations.llm.prompts import get_observation_prompt, get_summary_prompt
from src.integrations.llm.db_helpers import (
    get_prompt_text,
    get_tool_calls,
    get_last_observation,
    get_all_observations,
    get_files_from_tools,
)
from src.integrations.llm.schemas import (
    ObservationSchema,
    ObservationSkipSchema,
    ObservationWithTextSchema,
    SummarySchema,
    observation_to_db_format,
    summary_to_db_format,
)
from src.db.manager import get_db_connection


logger = get_logger(__name__)


def generate_observation_for_tools_with_timeout(
    session_id: str,
    prompt_id: str,
    tool_calls: List[Dict[str, Any]],
    past_observation: Optional[Dict[str, Any]] = None,
    endpoint_name: Optional[str] = None,
    timeout: int = 60
) -> Optional[Dict[str, Any]]:
    """
    Generate an observation for a prompt with tool calls, with timeout.

    This is a wrapper around generate_observation_for_tools that adds
    timeout handling to prevent hanging during LLM calls.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        tool_calls: List of tool call dicts with tool_name, input_json, output_json
        past_observation: Optional past observation for context
        endpoint_name: LLM endpoint to use (defaults to "default")
        timeout: Timeout in seconds (default 60)

    Returns:
        dict: Observation data matching database schema, or None if generation fails
    """
    import concurrent.futures
    import functools

    try:
        # Create a partial function with all arguments
        func = functools.partial(
            generate_observation_for_tools,
            session_id=session_id,
            prompt_id=prompt_id,
            tool_calls=tool_calls,
            past_observation=past_observation,
            endpoint_name=endpoint_name
        )

        # Run with timeout
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(func)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.error(f"Observation generation timed out after {timeout}s")
                future.cancel()
                return None

    except Exception as e:
        logger.error(f"Observation generation with timeout error: {e}", exc_info=True)
        return None


def generate_observation_for_tools(
    session_id: str,
    prompt_id: str,
    tool_calls: List[Dict[str, Any]],
    past_observation: Optional[Dict[str, Any]] = None,
    endpoint_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Generate an observation for a prompt with tool calls.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        tool_calls: List of tool call dicts with tool_name, input_json, output_json
        past_observation: Optional past observation for context
        endpoint_name: LLM endpoint to use (defaults to "default")

    Returns:
        dict: Observation data matching database schema, or None if generation fails

    Schema:
        {
            "id": str,
            "session_id": str,
            "prompt_number": int,
            "title": str,
            "subtitle": str,
            "narrative": str,
            "text": str,
            "facts": str,  # JSON array string
            "concepts": str,  # JSON array string
            "type": str,
            "files_read": str,  # JSON array string
            "files_modified": str,  # JSON array string
            "content_hash": str,
            "created_at": str,
            "sync_status": str,
        }
    """
    import json
    import hashlib

    try:
        # Get endpoint configuration
        endpoint_config = get_endpoint_config(endpoint_name or "default")

        if not endpoint_config.get("api_key"):
            logger.warning("No API key configured for observation endpoint")
            return None

        # Get prompt text
        prompt_text = get_prompt_text(session_id, prompt_id)
        if not prompt_text:
            logger.warning(f"Prompt text not found: {prompt_id}")
            return None

        # Prepare past observations for context
        past_observations = [past_observation] if past_observation else []

        # Build tool_calls with working_dir for the prompt
        tool_calls_with_context = []
        for tc in tool_calls:
            tool_calls_with_context.append({
                "tool_name": tc.get("tool_name", ""),
                "tool_result": tc.get("output_json", ""),
                "working_dir": "",  # Can be enhanced if needed
            })

        # Generate prompt using new template
        llm_prompt = get_observation_prompt(
            prompt_text=prompt_text,
            tool_calls=tool_calls_with_context,
            past_observations=past_observations
        )

        # Call LLM
        client = LLMClient(endpoint_config)
        response_text = client.complete_with_retry(llm_prompt)

        # Parse JSON response (handle markdown code blocks)
        try:
            # First try direct JSON parse
            observation_data = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'```json\s*\n?(.*?)\n?```', response_text, re.DOTALL)
            if json_match:
                observation_data = json.loads(json_match.group(1))
            else:
                # Try to find any JSON object
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    observation_data = json.loads(json_match.group(0))
                else:
                    raise LLMError("Could not parse LLM response as JSON")

        # Check for skip response AFTER JSON extraction
        if observation_data.get("skip"):
            logger.info(f"Observation skipped for prompt {prompt_id}: {observation_data.get('reason', 'unknown')}")
            return {"skipped": True, "reason": observation_data.get("reason", "routine operations")}

        # Validate with Pydantic and generate text field
        try:
            obs_schema = ObservationWithTextSchema(**observation_data)
            # Ensure text field is populated
            if not obs_schema.text:
                obs_schema.text = obs_schema.generate_text()
            observation_data = obs_schema.model_dump()
        except Exception as e:
            logger.warning(f"Pydantic validation failed: {e}, skipping observation")
            # If validation fails, skip this observation rather than saving empty data
            return {"skipped": True, "reason": f"Validation failed: {str(e)[:100]}"}

        # Create content hash
        content_str = f"{prompt_text[:500]}{str(tool_calls)[:500]}"
        content_hash = hashlib.sha256(content_str.encode()).hexdigest()[:16]

        # Extract facts and concepts as JSON arrays
        facts = observation_data.get("facts", [])
        concepts = observation_data.get("concepts", [])
        files_read = observation_data.get("files_read", [])
        files_modified = observation_data.get("files_modified", [])

        # Convert to JSON strings if they're lists
        if isinstance(facts, list):
            facts = json.dumps(facts)
        if isinstance(concepts, list):
            concepts = json.dumps(concepts)
        if isinstance(files_read, list):
            files_read = json.dumps(files_read)
        if isinstance(files_modified, list):
            files_modified = json.dumps(files_modified)

        # Build observation record
        observation = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "prompt_id": prompt_id,
            "title": observation_data.get("title", "Untitled")[:100],
            "subtitle": observation_data.get("subtitle", "")[:200],
            "narrative": observation_data.get("narrative", ""),
            "text": observation_data.get("text", ""),
            "facts": facts,
            "concepts": concepts,
            "type": observation_data.get("type", "change"),
            "files_read": files_read,
            "files_modified": files_modified,
            "content_hash": content_hash,
            "created_at": datetime.now().isoformat(),
        }

        logger.info(f"Generated observation for prompt {prompt_id}")
        return observation

    except LLMConfigError as e:
        logger.error(f"LLM config error: {e}")
        return None
    except LLMError as e:
        logger.error(f"LLM generation failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Observation generation error: {e}", exc_info=True)
        return None


def generate_observation(
    session_id: str,
    prompt_id: str,
    endpoint_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Generate an observation for a prompt (legacy function for backward compatibility).

    This function checks if the prompt has tool calls and generates an observation.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        endpoint_name: LLM endpoint to use (defaults to "default")

    Returns:
        dict: Observation data matching database schema, or None/skipped
    """
    from src.integrations.llm.db_helpers import has_tool_calls, get_tool_calls, get_last_observation

    try:
        # Check if prompt has tool calls
        if not has_tool_calls(session_id, prompt_id):
            logger.debug(f"No tool calls for prompt {prompt_id}, skipping observation")
            return {"skipped": True, "reason": "no_tool_calls"}

        # Get tool calls
        tool_calls = get_tool_calls(session_id, prompt_id)

        # Get last observation for context
        past_observations = get_last_observation(session_id, count=1)
        past_obs = past_observations[0] if past_observations else None

        # Generate observation for tools
        return generate_observation_for_tools(
            session_id=session_id,
            prompt_id=prompt_id,
            tool_calls=tool_calls,
            past_observation=past_obs,
            endpoint_name=endpoint_name
        )

    except Exception as e:
        logger.error(f"Observation generation error: {e}", exc_info=True)
        return None


def generate_summary_from_observations(
    session_id: str,
    observations: List[Dict[str, Any]],
    endpoint_name: Optional[str] = None,
    timeout: int = 90
) -> Optional[Dict[str, Any]]:
    """
    Generate a session summary from observations with timeout handling.

    Args:
        session_id: Session identifier
        observations: List of observation dicts
        endpoint_name: LLM endpoint to use (defaults to "default")
        timeout: Timeout in seconds (default 90)

    Returns:
        dict: Summary data matching database schema, or None if generation fails

    Schema:
        {
            "id": str,
            "session_id": str,
            "project": str,
            "request": str,
            "investigated": str,
            "learned": str,
            "completed": str,
            "next_steps": str,
            "notes": str,
            "created_at": str,
            "sync_status": str,
        }
    """
    import concurrent.futures
    import functools
    import json

    try:
        # Get endpoint configuration
        endpoint_config = get_endpoint_config(endpoint_name or "default")

        if not endpoint_config.get("api_key"):
            logger.warning("No API key configured for summary endpoint")
            return None

        if not observations:
            logger.warning(f"No observations provided for session {session_id}")
            return None

        # Get project name
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT p.name
            FROM PROJECT p
            JOIN SESSION s ON p.project_id = s.project_id
            WHERE s.session_id = ?
        """, (session_id,))

        project_row = cursor.fetchone()
        project = project_row[0] if project_row else "Unknown"
        cursor.close()

        # Get first prompt as the "request"
        first_obs = observations[0]
        request = first_obs.get("title", "Session summary")[:500]

        # Generate summary prompt using new template
        llm_prompt = get_summary_prompt(observations)

        # Create a function for the LLM call
        def make_llm_call():
            client = LLMClient(endpoint_config)
            return client.complete_with_retry(llm_prompt)

        # Run with timeout
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(make_llm_call)
            try:
                response_text = future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.error(f"Summary generation timed out after {timeout}s")
                future.cancel()
                return None

        # Parse JSON response using Pydantic
        try:
            # First try direct JSON parse
            summary_data = json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from response
            import re
            json_match = re.search(r'```json\s*\n?(.*?)\n?```', response_text, re.DOTALL)
            if json_match:
                summary_data = json.loads(json_match.group(1))
            else:
                # Try to find any JSON object
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    summary_data = json.loads(json_match.group(0))
                else:
                    raise LLMError("Could not parse LLM response as JSON")

        # Validate with Pydantic
        try:
            summary_schema = SummarySchema(**summary_data)
            summary_data = summary_schema.model_dump()
        except Exception as e:
            logger.warning(f"Pydantic validation failed for summary: {e}, using raw data")

        # Build summary record
        summary = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "project": project,
            "request": request,
            "investigated": summary_data.get("investigated", ""),
            "learned": summary_data.get("learned", ""),
            "completed": summary_data.get("completed", ""),
            "next_steps": summary_data.get("next_steps", ""),
            "notes": summary_data.get("notes", ""),
            "created_at": datetime.now().isoformat(),
        }

        logger.info(f"Generated summary for session {session_id}")
        return summary

    except LLMConfigError as e:
        logger.error(f"LLM config error: {e}")
        return None
    except LLMError as e:
        logger.error(f"LLM generation failed: {e}")
        return None
    except Exception as e:
        logger.error(f"Summary generation error: {e}", exc_info=True)
        return None


def generate_summary(
    session_id: str,
    endpoint_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Generate a session summary (fetches observations automatically).

    Args:
        session_id: Session identifier
        endpoint_name: LLM endpoint to use (defaults to "default")

    Returns:
        dict: Summary data matching database schema, or None if generation fails
    """
    try:
        # Fetch all observations for this session
        observations = get_all_observations(session_id)

        if not observations:
            logger.warning(f"No observations found for session {session_id}")
            return None

        # Generate summary from observations
        return generate_summary_from_observations(
            session_id=session_id,
            observations=observations,
            endpoint_name=endpoint_name
        )

    except Exception as e:
        logger.error(f"Summary generation error: {e}", exc_info=True)
        return None


def save_observation_to_db(observation: Dict[str, Any]) -> bool:
    """
    Save an observation to the database.

    Args:
        observation: Observation data dict

    Returns:
        bool: True if saved successfully
    """
    # Handle skipped observations
    if observation.get("skipped"):
        return True

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO OBSERVATION (
                id, session_id, prompt_id, title, subtitle, narrative,
                text, facts, concepts, type, files_read, files_modified,
                content_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            observation["id"],
            observation["session_id"],
            observation["prompt_id"],
            observation["title"],
            observation["subtitle"],
            observation["narrative"],
            observation["text"],
            observation["facts"],
            observation["concepts"],
            observation["type"],
            observation["files_read"],
            observation["files_modified"],
            observation["content_hash"],
            observation["created_at"],
        ))

        conn.commit()
        cursor.close()

        logger.info(f"Saved observation {observation['id']} to database")
        return True

    except Exception as e:
        logger.error(f"Failed to save observation: {e}", exc_info=True)
        return False


def save_summary_to_db(summary: Dict[str, Any]) -> bool:
    """
    Save a summary to the database.

    Args:
        summary: Summary data dict

    Returns:
        bool: True if saved successfully
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT OR REPLACE INTO SESSION_SUMMARY (
                id, session_id, project, request, investigated, learned,
                completed, next_steps, notes, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary["id"],
            summary["session_id"],
            summary["project"],
            summary["request"],
            summary["investigated"],
            summary["learned"],
            summary["completed"],
            summary["next_steps"],
            summary["notes"],
            summary["created_at"],
        ))

        conn.commit()
        cursor.close()

        logger.info(f"Saved summary {summary['id']} to database")
        return True

    except Exception as e:
        logger.error(f"Failed to save summary: {e}", exc_info=True)
        return False
