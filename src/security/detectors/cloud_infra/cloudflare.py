"""
Cloudflare Detector.

Covers the three new prefixed token formats (cfk_, cfut_, cfat_) plus
a context-anchored pattern for legacy tokens without a distinctive prefix.
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_CF_CHARSET = r"[a-zA-Z0-9]"
_CF_LEN     = 40


@register_detector
class CloudflareDetector(BaseDetector):
    CATEGORY           = "Cloudflare"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Cloudflare Global API Keys, User API Tokens, and Account API Tokens"
    DOMAIN             = "Cloud & Infrastructure"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Global API Key",
            label="CLOUDFLARE_GLOBAL_API_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("cfk_", _CF_CHARSET, _CF_LEN),
            description="Cloudflare Global API Key - full account access, equivalent to account password",
            example="cfk_[EXAMPLE]",
        ),
        TokenDefinition(
            type="User API Token",
            label="CLOUDFLARE_USER_API_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("cfut_", _CF_CHARSET, _CF_LEN),
            description="Cloudflare User API Token - scoped token for specific Cloudflare zones/permissions",
            example="cfut_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Account API Token",
            label="CLOUDFLARE_ACCOUNT_API_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("cfat_", _CF_CHARSET, _CF_LEN),
            description="Cloudflare Account API Token - scoped to account-level operations",
            example="cfat_[EXAMPLE]",
        ),
        # Legacy / context-anchored for tokens without new prefixes
        TokenDefinition(
            type="API Token (Legacy)",
            label="CLOUDFLARE_API_TOKEN",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "cloudflare_api_token", "cloudflare_api_key", "cf_api_token",
                    "cf_api_key", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY",
                    "CF_API_TOKEN", "CF_API_KEY",
                ],
                value_charset=r"[a-zA-Z0-9\-_]",
                value_min=37,
                value_max=45,
            ),
            description="Cloudflare API key (legacy context-only detection) - used for older integrations",
            example="cloudflare_api_key=[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return [
            "cfk_", "cfut_", "cfat_",
            "cloudflare_api", "CLOUDFLARE_API", "cf_api", "CF_API",
        ]
