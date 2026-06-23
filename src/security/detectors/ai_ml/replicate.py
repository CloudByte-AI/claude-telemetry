"""
Replicate Detector.

Covers:
  r8_       API Token / Key  (r8_ + 37 alphanumeric = 40 chars total)
  whsec_    Webhook Signing Secret  (whsec_ + 32 base64 chars = 38 chars total)
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class ReplicateDetector(BaseDetector):
    CATEGORY           = "Replicate"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Replicate API tokens and webhook signing secrets"
    DOMAIN             = "AI & ML Platforms"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="API Token",
            label="REPLICATE_API_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("r8_", r"[a-zA-Z0-9]", 37),
            description="Replicate API token - used to run machine learning models on Replicate's cloud",
            example="r8_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Webhook Signing Secret",
            label="REPLICATE_WEBHOOK_SECRET",
            severity="HIGH",
            detection="prefix",
            # whsec_ + 32 chars of base64 (may include +, /, =)
            pattern=BaseDetector.prefix_pattern(
                "whsec_", r"[a-zA-Z0-9+/=]", 32, 48, word_boundary=True
            ),
            description="Replicate webhook signing secret - used to verify webhook payloads from Replicate",
            example="whsec_[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["r8_", "whsec_"]
