"""
Razorpay Detector.

Covers:
  rzp_live_ / rzp_test_   Key ID (prefix + 14 alphanumeric)
  Key Secret              Context-anchored, 24 alphanumeric chars (no prefix)
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class RazorpayDetector(BaseDetector):
    CATEGORY           = "Razorpay"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Razorpay API Key IDs (live/test) and Key Secrets"
    DOMAIN             = "Payment Gateways"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Live Key ID",
            label="RAZORPAY_LIVE_KEY_ID",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("rzp_live_", r"[a-zA-Z0-9]", 14),
            description="Razorpay live Key ID - identifies your live Razorpay merchant account",
            example="rzp_live_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Test Key ID",
            label="RAZORPAY_TEST_KEY_ID",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("rzp_test_", r"[a-zA-Z0-9]", 14),
            description="Razorpay test Key ID - used for sandbox/testing transactions",
            example="rzp_test_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Key Secret",
            label="RAZORPAY_KEY_SECRET",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "razorpay_key_secret", "razorpay_secret", "rzp_secret",
                    "RAZORPAY_KEY_SECRET", "RAZORPAY_SECRET", "RZP_SECRET",
                ],
                value_charset=r"[a-zA-Z0-9]",
                value_min=24,
                value_max=24,
            ),
            description="Razorpay Key Secret - paired with Key ID, used to authenticate API requests and verify webhooks",
            example="razorpay_key_secret=[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return [
            "rzp_live_", "rzp_test_",
            "razorpay_key_secret", "razorpay_secret", "RAZORPAY_KEY_SECRET",
        ]
