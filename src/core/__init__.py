"""
Core business logic for CloudByte.

This module contains the fundamental processing and coordination logic
that drives the CloudByte observation system.
"""

from src.core.event_processor import (
    EventProcessor,
    process_session_start,
    process_user_prompt,
    generate_observation,
    process_session_end,
)

__all__ = [
    "EventProcessor",
    "process_session_start",
    "process_user_prompt",
    "generate_observation",
    "process_session_end",
]
