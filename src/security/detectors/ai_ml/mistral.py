"""
Mistral Detector.

Mistral API keys are 32 alphanumeric chars with no distinctive prefix.
Detected via variable name context only.
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class MistralDetector(BaseDetector):
    CATEGORY           = "Mistral"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Mistral AI API keys (context-anchored, no prefix)"
    DOMAIN             = "AI & ML Platforms"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="API Key",
            label="MISTRAL_API_KEY",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "mistral_api_key", "mistral_key", "mistral_token",
                    "MISTRAL_API_KEY", "MISTRAL_KEY", "MISTRAL_TOKEN",
                ],
                value_charset=r"[a-zA-Z0-9]",
                value_min=32,
                value_max=48,   # allow slight format variation
            ),
            description="Mistral API Key - used to call Mistral AI's language models",
            example="mistral_api_key=[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["mistral_api_key", "mistral_key", "mistral_token",
                "MISTRAL_API_KEY", "MISTRAL_KEY", "MISTRAL_TOKEN"]
