"""
Bearer Token Detector (disabled by default).

Catches HTTP Authorization header values of the form:
  Authorization: Bearer <token>
  Bearer <token>

The token is captured as the secret value.

Disabled by default because bearer tokens are very common in documentation,
log files, and example code - enable selectively in strict profiles.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_BEARER_RE = re.compile(
    r"""(?:Authorization\s*[=:]\s*)?[Bb]earer\s+([A-Za-z0-9\-_=+/\.]{16,512})""",
    re.IGNORECASE,
)


@register_detector
class BearerTokenDetector(BaseDetector):
    CATEGORY           = "Bearer Token"
    ENABLED_BY_DEFAULT = False
    DESCRIPTION        = "HTTP Authorization: Bearer tokens (off by default - noisy)"
    DOMAIN             = "Auth"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="HTTP Bearer Token",
            label="BEARER_TOKEN",
            severity="MEDIUM",
            detection="pattern",
            capture_group=1,
            pattern=_BEARER_RE,
            description="HTTP Authorization Bearer token - raw bearer token from an Authorization header",
            example="Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyXzEyMyIsImlhdCI6MTcwMDAwMDAwMH0.dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["Bearer ", "bearer "]
