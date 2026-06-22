"""
Cohere Detector.

Cohere API keys have no distinctive prefix (40 alphanumeric chars).
Detected via variable name context only.
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class CohereDetector(BaseDetector):
    CATEGORY           = "Cohere"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Cohere AI API keys (context-anchored, no prefix)"
    DOMAIN             = "AI & ML Platforms"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="API Key",
            label="COHERE_API_KEY",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "cohere_api_key", "cohere_key", "co_api_key",
                    "COHERE_API_KEY", "COHERE_KEY", "CO_API_KEY",
                ],
                value_charset=r"[a-zA-Z0-9]",
                value_min=40,
                value_max=40,
            ),
            description="Cohere API Key — used to access Cohere's NLP models (Command, Embed, Rerank)",
            example="cohere_api_key=[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["cohere_api_key", "cohere_key", "co_api_key",
                "COHERE_API_KEY", "COHERE_KEY", "CO_API_KEY"]
