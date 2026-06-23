"""Groq Detector - gsk_ prefix + exactly 52 alphanumeric chars."""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class GroqDetector(BaseDetector):
    CATEGORY           = "Groq"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Groq API keys (gsk_ prefix)"
    DOMAIN             = "AI & ML Platforms"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="API Key",
            label="GROQ_API_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("gsk_", r"[a-zA-Z0-9_]", 52),
            description="Groq API Key - used to call Groq's LLM inference API (LLaMA, Mixtral, etc.)",
            example="gsk_[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["gsk_"]
