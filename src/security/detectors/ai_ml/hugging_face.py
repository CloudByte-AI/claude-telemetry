"""Hugging Face Detector - hf_ prefix + 34+ alphanumeric chars."""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class HuggingFaceDetector(BaseDetector):
    CATEGORY           = "HuggingFace"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Hugging Face Hub access tokens (hf_ prefix)"
    DOMAIN             = "AI & ML Platforms"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Access Token",
            label="HUGGING_FACE_TOKEN",
            severity="HIGH",
            detection="prefix",
            # min 34, max 50 to avoid very long base64 false positives
            pattern=BaseDetector.prefix_pattern("hf_", r"[a-zA-Z0-9]", 34, 50),
            description="Hugging Face API token - access to models, datasets, and Spaces on huggingface.co",
            example="hf_[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["hf_"]
