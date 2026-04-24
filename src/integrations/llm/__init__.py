"""
CloudByte LLM Module

Provides LLM integration for generating observations and session summaries
using litellm for multi-provider support.
"""

from .client import LLMClient, LLMError
from .config import (
    get_llm_config,
    get_endpoint_config,
    LLMConfigError,
    list_available_endpoints,
)
from .generators import (
    generate_observation,
    generate_summary,
    generate_observation_for_tools,
    generate_summary_from_observations,
    save_observation_to_db,
    save_summary_to_db,
)
from .prompts import get_observation_prompt, get_summary_prompt
from .schemas import (
    ObservationSchema,
    ObservationSkipSchema,
    ObservationWithTextSchema,
    SummarySchema,
    observation_to_db_format,
    summary_to_db_format,
)

__all__ = [
    "LLMClient",
    "LLMError",
    "LLMConfigError",
    "get_llm_config",
    "get_endpoint_config",
    "list_available_endpoints",
    "generate_observation",
    "generate_summary",
    "generate_observation_for_tools",
    "generate_summary_from_observations",
    "save_observation_to_db",
    "save_summary_to_db",
    "get_observation_prompt",
    "get_summary_prompt",
    "ObservationSchema",
    "ObservationSkipSchema",
    "ObservationWithTextSchema",
    "SummarySchema",
    "observation_to_db_format",
    "summary_to_db_format",
]
