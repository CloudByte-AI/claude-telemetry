"""
Twilio Detector.

Covers:
  SK[hex]{32}   API Key SID        (prefix SK + 32 hex = 34 total)
  AC[hex]{32}   Account SID        (prefix AC + 32 hex = 34 total)
  API Key Secret  context-anchored, 32 alphanumeric
  Auth Token    context-anchored, 32 hex chars
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class TwilioDetector(BaseDetector):
    CATEGORY           = "Twilio"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Twilio API key SIDs, Account SIDs, and auth tokens"
    DOMAIN             = "Communication"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="API Key SID",
            label="TWILIO_API_KEY_SID",
            severity="HIGH",
            detection="prefix",
            # SK + 32 hex digits, strict hex-only charset for specificity
            pattern=BaseDetector.prefix_pattern("SK", r"[0-9a-fA-F]", 32),
            description="Twilio API Key SID - identifies an API key, used with the API Key Secret for authentication",
            example="SK[EXAMPLE]",
        ),
        TokenDefinition(
            type="Account SID",
            label="TWILIO_ACCOUNT_SID",
            severity="HIGH",
            detection="prefix",
            # AC + 32 hex digits
            pattern=BaseDetector.prefix_pattern("AC", r"[0-9a-fA-F]", 32),
            description="Twilio Account SID - uniquely identifies your Twilio account",
            example="AC[EXAMPLE]",
        ),
        TokenDefinition(
            type="API Key Secret",
            label="TWILIO_API_KEY_SECRET",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "twilio_api_key_secret", "twilio_key_secret", "twilio_secret",
                    "TWILIO_API_KEY_SECRET", "TWILIO_KEY_SECRET", "TWILIO_SECRET",
                ],
                value_charset=r"[a-zA-Z0-9]",
                value_min=32,
                value_max=32,
            ),
            description="Twilio API Key Secret - paired with API Key SID, used to authenticate Twilio REST API calls",
            example="twilio_api_key_secret=[EXAMPLE]",
        ),
        TokenDefinition(
            type="Auth Token",
            label="TWILIO_AUTH_TOKEN",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "twilio_auth_token", "TWILIO_AUTH_TOKEN",
                ],
                value_charset=r"[0-9a-fA-F]",
                value_min=32,
                value_max=32,
            ),
            description="Twilio Auth Token - master credential for your Twilio account, equivalent to account password",
            example="twilio_auth_token=[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return [
            "SK", "AC",
            "twilio_auth_token", "twilio_api_key_secret",
            "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SECRET",
        ]
