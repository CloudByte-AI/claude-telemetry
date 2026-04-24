"""
Hook Observation Handler

Extracts <obs> blocks from Claude's responses and saves to HOOK_OBSERVATION table.
This works with the inline obs system where Claude emits obs blocks directly.
"""

import json
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.logging import get_logger, setup_logging
from src.observations.extractor import extract_and_parse_obs, clean_response_text
from src.observations.writer import save_observation


logger = get_logger(__name__)


def read_stdin_data() -> dict:
    """Read hook data from stdin."""
    try:
        data = sys.stdin.read().strip()
        if data:
            return json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing stdin JSON: {e}")

    return {}


def handle_hook_observation_extraction(session_id: str, prompt_id: str, response_text: str) -> dict:
    """
    Extract and save observations from Claude's response.

    Args:
        session_id: Session identifier
        prompt_id: Prompt identifier
        response_text: Claude's full response text

    Returns:
        dict: Status with observation count
    """
    try:
        # Extract obs blocks from response
        observations = extract_and_parse_obs(response_text)

        if not observations:
            logger.debug(f"No obs blocks found in prompt {prompt_id}")
            return {"status": "skipped", "reason": "no_obs_blocks", "count": 0}

        # Save each observation to HOOK_OBSERVATION table
        saved_count = 0
        for obs_data in observations:
            obs_id = save_observation(
                session_id=session_id,
                prompt_id=prompt_id,
                obs_data=obs_data
            )
            if obs_id:
                saved_count += 1
                logger.info(f"Saved observation {obs_id}: {obs_data.get('title', 'Untitled')}")
            else:
                logger.warning(f"Failed to save observation: {obs_data.get('title', 'Unknown')}")

        return {
            "status": "success",
            "count": saved_count,
            "total": len(observations)
        }

    except Exception as e:
        logger.error(f"Hook observation extraction failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e), "count": 0}


def main():
    """
    Main entry point for hook observation extraction.

    This can be called from the Stop hook to extract inline obs blocks.
    """
    setup_logging(log_to_file=True, log_to_console=False)

    try:
        # Read hook data from stdin
        hook_data = read_stdin_data()

        session_id = hook_data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID")
        prompt_id = hook_data.get("prompt_id")
        response_text = hook_data.get("response_text", "")

        if not session_id or not prompt_id:
            logger.error("Missing session_id or prompt_id in hook data")
            sys.exit(1)

        # Extract and save observations
        result = handle_hook_observation_extraction(session_id, prompt_id, response_text)

        # Output result as JSON
        print(json.dumps(result))

        if result.get("status") == "error":
            sys.exit(1)
        else:
            sys.exit(0)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Hook observation handler failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
