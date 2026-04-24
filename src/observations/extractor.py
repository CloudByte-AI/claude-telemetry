"""
Observation Block Extractor

Extracts <obs>...</obs> blocks from Claude's responses and parses them.
"""

import json
import re
from typing import Dict, List, Optional, Any
from pathlib import Path


def extract_obs_blocks(text: str) -> List[str]:
    """
    Extract all <obs>...</obs> blocks from text.

    Args:
        text: Text that may contain obs blocks

    Returns:
        List of obs block contents (JSON strings)
    """
    pattern = r'<obs>\s*\n?(.*?)\n?\s*</obs>'
    matches = re.findall(pattern, text, re.DOTALL)
    return matches


def parse_obs_block(obs_json: str) -> Optional[Dict[str, Any]]:
    """
    Parse an obs block JSON string into a dictionary.

    Args:
        obs_json: JSON string from obs block

    Returns:
        Parsed observation dict or None if invalid
    """
    try:
        obs = json.loads(obs_json)

        # Validate required fields
        required_fields = ["type", "title", "subtitle", "narrative"]
        for field in required_fields:
            if field not in obs:
                return None

        # Ensure optional fields exist and clean up file paths
        if "facts" not in obs:
            obs["facts"] = []
        if "concepts" not in obs:
            obs["concepts"] = []
        if "files_read" not in obs:
            obs["files_read"] = []
        else:
            # Normalize file paths (convert Windows backslashes to forward slashes)
            obs["files_read"] = [f.replace("\\", "/") for f in obs["files_read"]]

        if "files_modified" not in obs:
            obs["files_modified"] = []
        else:
            # Normalize file paths
            obs["files_modified"] = [f.replace("\\", "/") for f in obs["files_modified"]]

        return obs

    except (json.JSONDecodeError, TypeError):
        return None


def extract_and_parse_obs(text: str) -> List[Dict[str, Any]]:
    """
    Extract and parse all obs blocks from text.

    Args:
        text: Text that may contain obs blocks

    Returns:
        List of parsed observation dicts
    """
    blocks = extract_obs_blocks(text)
    observations = []

    for block in blocks:
        obs = parse_obs_block(block)
        if obs:
            observations.append(obs)

    return observations


def clean_response_text(text: str) -> str:
    """
    Remove obs blocks from text (for display/logging).

    Args:
        text: Text that may contain obs blocks

    Returns:
        Text with obs blocks removed
    """
    pattern = r'<obs>\s*\n?.*?\n?\s*</obs>'
    return re.sub(pattern, '', text, flags=re.DOTALL).strip()
