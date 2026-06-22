"""
Anthropic Detector.

Covers all known Anthropic API key sub-types:
  sk-ant-api03-    API Key       (total 99–108 chars)
  sk-ant-oat01-    OAuth Key
  sk-ant-ort01-    OAuth Refresh Token
  sk-ant-admin01-  Admin Key
  sk-ant-          Generic catch-all for any future sub-types

More-specific types are listed first. The generic catch-all uses a negative
lookahead to not re-match tokens already caught by specific entries.
"""

import re

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_ANT_CHARSET = r"[a-zA-Z0-9\-_]"

# Prefix lengths (chars): sk-ant-api03- = 13, sk-ant-oat01- = 13,
# sk-ant-ort01- = 13, sk-ant-admin01- = 15
# Total key length documented as 99–108 chars.
_AFTER_API03   = (99  - 13, 108 - 13)   # (86, 95)
_AFTER_OAT01   = (99  - 13, 108 - 13)
_AFTER_ORT01   = (99  - 13, 108 - 13)
_AFTER_ADMIN01 = (99  - 15, 108 - 15)   # (84, 93)
_AFTER_GENERIC = (85, 110)              # generous range for unknown future sub-types


@register_detector
class AnthropicDetector(BaseDetector):
    CATEGORY           = "Anthropic"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Anthropic API keys (all sub-types: API, OAuth, Admin)"
    DOMAIN             = "AI & ML Platforms"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="API Key",
            label="ANTHROPIC_API_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "sk-ant-api03-", _ANT_CHARSET, _AFTER_API03[0], _AFTER_API03[1]
            ),
            description="Anthropic Claude API Key — used to call Claude models via the Anthropic API",
            example="sk-ant-api03-[EXAMPLE]",
        ),
        TokenDefinition(
            type="OAuth Key",
            label="ANTHROPIC_OAUTH_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "sk-ant-oat01-", _ANT_CHARSET, _AFTER_OAT01[0], _AFTER_OAT01[1]
            ),
            description="Anthropic OAuth access token — used for OAuth-based authentication flows",
            example="sk-ant-oat01-[EXAMPLE]",
        ),
        TokenDefinition(
            type="OAuth Refresh Token",
            label="ANTHROPIC_OAUTH_REFRESH_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "sk-ant-ort01-", _ANT_CHARSET, _AFTER_ORT01[0], _AFTER_ORT01[1]
            ),
            description="Anthropic OAuth refresh token — used to obtain new OAuth access tokens",
            example="sk-ant-ort01-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Admin Key",
            label="ANTHROPIC_ADMIN_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "sk-ant-admin01-", _ANT_CHARSET, _AFTER_ADMIN01[0], _AFTER_ADMIN01[1]
            ),
            description="Anthropic Admin Key — organization-wide administrative access to the Anthropic platform",
            example="sk-ant-admin01-[EXAMPLE]",
        ),
        # Generic catch-all: sk-ant- NOT followed by a known sub-type
        TokenDefinition(
            type="API Key (Unknown Sub-type)",
            label="ANTHROPIC_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=re.compile(
                r'(?<![a-zA-Z0-9\-_])sk-ant-'
                r'(?!api03-|oat01-|ort01-|admin01-)'
                r'[a-zA-Z0-9\-_]{85,110}'
                r'(?![a-zA-Z0-9\-_])'
            ),
            description="Anthropic API key (unrecognized sub-type) — grants access to Anthropic Claude models",
            example="sk-ant-api03-[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["sk-ant-"]
