"""
Email Address Detector (disabled by default).

Standard RFC 5322-compliant email pattern.  Disabled by default because
email addresses appear legitimately in many non-secret contexts (log lines,
user-profile fields, debug output).  Enable in strict/PII profiles.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

# Practical email pattern - not trying to be fully RFC 5322; covers 99.9% of real addresses
_EMAIL_RE = re.compile(
    r"(?<![a-zA-Z0-9._%+\-])"
    r"[a-zA-Z0-9._%+\-]{1,64}"
    r"@"
    r"[a-zA-Z0-9\-]{1,253}"
    r"(?:\.[a-zA-Z0-9\-]{1,63})*"
    r"\.[a-zA-Z]{2,63}"
    r"(?![a-zA-Z0-9._%+\-@])",
    re.ASCII,
)


@register_detector
class EmailDetector(BaseDetector):
    CATEGORY           = "Email Address"
    ENABLED_BY_DEFAULT = False
    DESCRIPTION        = "Email addresses (PII - off by default)"
    DOMAIN             = "PII"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Email Address",
            label="EMAIL_ADDRESS",
            severity="MEDIUM",
            detection="pattern",
            capture_group=0,
            pattern=_EMAIL_RE,
            description="Email address - personally identifiable information (PII) that may identify an individual",
            example="user.name@example.com",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["@"]
