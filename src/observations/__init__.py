"""
Observation handling module.

This module consolidates all observation-related functionality including
extraction, generation, storage, and queue management.
"""

from src.observations.extractor import (
    extract_obs_blocks,
    parse_obs_block,
    extract_and_parse_obs,
    clean_response_text,
)
from src.observations.writer import (
    save_observation,
    get_session_observations,
)
from src.observations.hook_handler import (
    handle_hook_observation_extraction,
)
from src.observations.queue import (
    queue_observation_task,
    get_pending_tasks,
    mark_task_started,
    mark_task_completed,
)

__all__ = [
    # Extractor
    "extract_obs_blocks",
    "parse_obs_block",
    "extract_and_parse_obs",
    "clean_response_text",
    # Writer
    "save_observation",
    "get_session_observations",
    # Hook Handler
    "handle_hook_observation_extraction",
    # Queue
    "queue_observation_task",
    "get_pending_tasks",
    "mark_task_started",
    "mark_task_completed",
]
