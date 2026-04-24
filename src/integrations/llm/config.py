"""
LLM Configuration Management for CloudByte

Handles loading and validation of LLM configuration from .cloudbyte/config.json
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from src.common.logging import get_logger
from src.common.paths import get_config_file
from src.common.file_io import read_json


logger = get_logger(__name__)


# Default LLM configuration
DEFAULT_LLM_CONFIG = {
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
}


class LLMConfigError(Exception):
    """Raised when LLM configuration is invalid or missing."""
    pass


def validate_llm_config(config: Dict[str, Any]) -> bool:
    """
    Validate LLM configuration structure.

    Args:
        config: LLM configuration dictionary

    Returns:
        bool: True if valid

    Raises:
        LLMConfigError: If configuration is invalid
    """
    if not isinstance(config, dict):
        raise LLMConfigError("LLM config must be a dictionary")

    # Check required top-level keys
    if "endpoints" not in config:
        raise LLMConfigError("LLM config missing 'endpoints' key")

    if not isinstance(config["endpoints"], dict):
        raise LLMConfigError("LLM 'endpoints' must be a dictionary")

    # Validate each endpoint
    for endpoint_name, endpoint_config in config["endpoints"].items():
        if not isinstance(endpoint_config, dict):
            raise LLMConfigError(f"Endpoint '{endpoint_name}' must be a dictionary")

        # Required fields for each endpoint
        required_fields = ["provider", "model"]
        for field in required_fields:
            if field not in endpoint_config:
                raise LLMConfigError(f"Endpoint '{endpoint_name}' missing required field: {field}")

        # Validate types
        if "api_key" not in endpoint_config or not endpoint_config["api_key"]:
            logger.warning(f"Endpoint '{endpoint_name}' has no API key set - will fail when called")

        if "temperature" in endpoint_config:
            temp = endpoint_config["temperature"]
            if not isinstance(temp, (int, float)) or temp < 0 or temp > 2:
                raise LLMConfigError(f"Endpoint '{endpoint_name}' has invalid temperature: {temp}")

        if "max_tokens" in endpoint_config:
            max_tokens = endpoint_config["max_tokens"]
            if not isinstance(max_tokens, int) or max_tokens <= 0:
                raise LLMConfigError(f"Endpoint '{endpoint_name}' has invalid max_tokens: {max_tokens}")

    return True


def get_llm_config() -> Dict[str, Any]:
    """
    Load LLM configuration from .cloudbyte/config.json.

    Returns:
        dict: LLM configuration dictionary

    Raises:
        LLMConfigError: If configuration is invalid
    """
    config_file = get_config_file()

    if not config_file.exists():
        logger.warning(f"Config file not found: {config_file}, using defaults")
        return DEFAULT_LLM_CONFIG.copy()

    try:
        full_config = read_json(config_file)
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to read config file: {e}, using defaults")
        return DEFAULT_LLM_CONFIG.copy()

    llm_config = full_config.get("llm")

    if llm_config is None:
        logger.warning("No 'llm' section in config, using defaults")
        return DEFAULT_LLM_CONFIG.copy()

    try:
        validate_llm_config(llm_config)
        return llm_config
    except LLMConfigError as e:
        logger.error(f"Invalid LLM config: {e}, using defaults")
        return DEFAULT_LLM_CONFIG.copy()


def get_endpoint_config(endpoint_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Get configuration for a specific LLM endpoint.

    Args:
        endpoint_name: Name of the endpoint (e.g., "observation", "summary")
                      If None, uses the default endpoint from config

    Returns:
        dict: Endpoint configuration

    Raises:
        LLMConfigError: If endpoint is not found or configuration is invalid
    """
    llm_config = get_llm_config()

    # Determine which endpoint to use
    if endpoint_name is None:
        endpoint_name = llm_config.get("default", "observation")

    endpoints = llm_config.get("endpoints", {})

    if endpoint_name not in endpoints:
        available = list(endpoints.keys())
        raise LLMConfigError(
            f"Endpoint '{endpoint_name}' not found. Available endpoints: {available}"
        )

    endpoint_config = endpoints[endpoint_name]

    # Check if API key is set
    if not endpoint_config.get("api_key"):
        logger.warning(f"Endpoint '{endpoint_name}' has no API key set")

    return endpoint_config


def merge_endpoint_config(
    endpoint_config: Dict[str, Any],
    **overrides
) -> Dict[str, Any]:
    """
    Merge endpoint configuration with runtime overrides.

    Args:
        endpoint_config: Base endpoint configuration
        **overrides: Runtime overrides (e.g., temperature, max_tokens)

    Returns:
        dict: Merged configuration
    """
    merged = endpoint_config.copy()

    # Only override allowed fields
    allowed_overrides = ["temperature", "max_tokens", "top_p", "stream"]

    for key, value in overrides.items():
        if key in allowed_overrides:
            merged[key] = value
        else:
            logger.warning(f"Cannot override field '{key}' at runtime")

    return merged


def list_available_endpoints() -> list[str]:
    """
    List all available LLM endpoint names.

    Returns:
        list[str]: List of endpoint names
    """
    llm_config = get_llm_config()
    return list(llm_config.get("endpoints", {}).keys())


def get_default_endpoint_name() -> str:
    """
    Get the default endpoint name from configuration.

    Returns:
        str: Default endpoint name
    """
    llm_config = get_llm_config()
    return llm_config.get("default", "observation")
