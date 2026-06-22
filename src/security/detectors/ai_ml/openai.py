"""
OpenAI Detector.

Covers all current OpenAI key formats:
  sk-proj-      Project API Key      (48-156 chars)
  sk-svcacct-   Service Account Key  (48-156 chars)
  sk-admin-     Admin Key            (48-156 chars)
  sk-None-      User Key             (48-156 chars)
  sk-           Legacy Key           (exactly 48 chars)

More-specific prefixes are listed first; the legacy sk- pattern uses a negative
lookahead to avoid matching tokens that already belong to a more specific type.
"""

import re

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

# OpenAI keys use alphanumeric + underscore + dash in the variable portion.
_OPENAI_CHARSET    = r"[a-zA-Z0-9_\-]"
_OPENAI_VAR_MIN    = 48
_OPENAI_VAR_MAX    = 156


@register_detector
class OpenAIDetector(BaseDetector):
    CATEGORY           = "OpenAI"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "OpenAI API keys (Project, Service Account, Admin, User, and Legacy formats)"
    DOMAIN             = "AI & ML Platforms"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Project API Key",
            label="OPENAI_PROJECT_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "sk-proj-", _OPENAI_CHARSET, _OPENAI_VAR_MIN, _OPENAI_VAR_MAX
            ),
            description="OpenAI Project API Key — scoped to a specific project in your OpenAI organization",
            example="sk-proj-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Service Account Key",
            label="OPENAI_SERVICE_ACCOUNT_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "sk-svcacct-", _OPENAI_CHARSET, _OPENAI_VAR_MIN, _OPENAI_VAR_MAX
            ),
            description="OpenAI Service Account Key — used for automated workloads and CI/CD pipelines",
            example="sk-svcacct-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Admin Key",
            label="OPENAI_ADMIN_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "sk-admin-", _OPENAI_CHARSET, _OPENAI_VAR_MIN, _OPENAI_VAR_MAX
            ),
            description="OpenAI Admin Key — organization-wide administrative access",
            example="sk-admin-[EXAMPLE]",
        ),
        TokenDefinition(
            type="User Key",
            label="OPENAI_USER_KEY",
            severity="HIGH",
            detection="prefix",
            # "sk-None-" is a literal prefix string used for user keys
            pattern=BaseDetector.prefix_pattern(
                "sk-None-", _OPENAI_CHARSET, _OPENAI_VAR_MIN, _OPENAI_VAR_MAX
            ),
            description="OpenAI User Key — personal API key tied to a specific user",
            example="sk-None-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Legacy API Key",
            label="OPENAI_LEGACY_KEY",
            severity="HIGH",
            detection="prefix",
            # Negative lookahead: only match sk- NOT followed by known sub-type prefixes
            pattern=re.compile(
                r'(?<![a-zA-Z0-9\-_])sk-(?!proj-|svcacct-|admin-|None-|ant-)'
                r'[a-zA-Z0-9]{48}(?![a-zA-Z0-9\-_])'
            ),
            description="OpenAI legacy API key — older format still active, grants full API access",
            example="sk-[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["sk-proj-", "sk-svcacct-", "sk-admin-", "sk-None-", "sk-"]
