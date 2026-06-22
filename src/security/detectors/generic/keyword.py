"""
Keyword / Variable-assignment Detector (disabled by default).

Catches bare secret-looking assignments where no specific prefix or category
detector fires.  Pattern is:
  <keyword>  <sep>  <quoted-or-unquoted-value>

This is intentionally broad — enable only when you want maximum coverage
and are willing to triage false positives.

Does NOT require high entropy; it fires on any non-trivial value (8+ chars)
assigned to a sensitive-sounding name.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

# Keywords that strongly suggest a credential
_KEYWORDS = [
    "secret", "SECRET",
    "password", "PASSWORD", "passwd", "PASSWD",
    "api_key", "API_KEY", "apikey", "APIKEY",
    "api_secret", "API_SECRET",
    "access_key", "ACCESS_KEY",
    "access_token", "ACCESS_TOKEN",
    "auth_token", "AUTH_TOKEN",
    "private_key", "PRIVATE_KEY",
    "signing_key", "SIGNING_KEY",
    "encryption_key", "ENCRYPTION_KEY",
    "client_secret", "CLIENT_SECRET",
    "app_secret", "APP_SECRET",
    "token", "TOKEN",
    "credential", "CREDENTIAL",
]

_KEYWORD_ALT = "|".join(re.escape(k) for k in _KEYWORDS)

# Supports:
#   secret = "value"
#   SECRET: value
#   secret=value
#   secret := "value"
_KEYWORD_RE = re.compile(
    rf"""(?:^|(?<=[^a-zA-Z0-9_]))(?:{_KEYWORD_ALT})\s*[:=]{{1,2}}\s*['\"]?([A-Za-z0-9+/=_\-!@#$%^&*(){{}}[\]|;:<>,.?~`]{{8,512}})['\"]?""",
    re.MULTILINE,
)


@register_detector
class KeywordDetector(BaseDetector):
    CATEGORY           = "Keyword Secret"
    ENABLED_BY_DEFAULT = False
    DESCRIPTION        = "Variable assignments with secret-sounding names (off by default — broad / noisy)"
    DOMAIN             = "Generic"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Keyword-Based Secret",
            label="KEYWORD_SECRET",
            severity="LOW",
            detection="context",
            capture_group=1,
            pattern=_KEYWORD_RE,
            description="Value assigned to a security-sensitive variable name — may be a hardcoded credential",
            example="password=MySup3rS3cr3tP@ssword123!",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["secret", "SECRET", "password", "api_key", "API_KEY", "token", "TOKEN"]
