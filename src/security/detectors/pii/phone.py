"""
Phone Number Detector (disabled by default).

Covers E.164 international format and common US/UK/Indian patterns.
Disabled by default because phone-like digit sequences appear frequently
in non-PII contexts (version numbers, IDs, zip codes).
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

# E.164: +[1-3 digit country code][4-14 digits] (total 8-15 digits after +)
_E164_RE = re.compile(
    r"(?<!\d)\+[1-9]\d{6,14}(?!\d)",
    re.ASCII,
)

# North American: (NXX) NXX-XXXX  or NXX-NXX-XXXX  or 1-NXX-NXX-XXXX
_NANP_RE = re.compile(
    r"(?<!\d)(?:1[\s\-.])?(?:\(\d{3}\)|\d{3})[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)",
    re.ASCII,
)


@register_detector
class PhoneDetector(BaseDetector):
    CATEGORY           = "Phone Number"
    ENABLED_BY_DEFAULT = False
    DESCRIPTION        = "Phone numbers (PII - off by default)"
    DOMAIN             = "PII"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="E.164 Phone Number",
            label="PHONE_E164",
            severity="MEDIUM",
            detection="pattern",
            capture_group=0,
            pattern=_E164_RE,
            description="International phone number (E.164 format) - PII that identifies an individual",
            example="+14155552671",
        ),
        TokenDefinition(
            type="North American Phone Number",
            label="PHONE_NANP",
            severity="MEDIUM",
            detection="pattern",
            capture_group=0,
            pattern=_NANP_RE,
            description="North American phone number (NANP format) - PII that identifies an individual",
            example="(415) 555-2671",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["+1", "+4", "+9"]
