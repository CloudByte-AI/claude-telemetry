"""
DigitalOcean Detector.

Covers Personal Access Tokens (dop_v1_), OAuth Access Tokens (doo_v1_),
and OAuth Refresh Tokens (dor_v1_). All are prefix + 64 hex characters.
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_DO_HEX_CHARSET = "[a-f0-9]"
_DO_HEX_LEN     = 64


@register_detector
class DigitalOceanDetector(BaseDetector):
    CATEGORY           = "DigitalOcean"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "DigitalOcean PATs and OAuth tokens"
    DOMAIN             = "Cloud & Infrastructure"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Personal Access Token",
            label="DIGITALOCEAN_PAT",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("dop_v1_", _DO_HEX_CHARSET, _DO_HEX_LEN),
            description="DigitalOcean Personal Access Token - full control over your DigitalOcean account (droplets, DNS, networking)",
            example="dop_v1_[EXAMPLE]",
        ),
        TokenDefinition(
            type="OAuth Access Token",
            label="DIGITALOCEAN_OAUTH_ACCESS_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("doo_v1_", _DO_HEX_CHARSET, _DO_HEX_LEN),
            description="DigitalOcean OAuth access token - third-party application access to DigitalOcean resources",
            example="doo_v1_[EXAMPLE]",
        ),
        TokenDefinition(
            type="OAuth Refresh Token",
            label="DIGITALOCEAN_OAUTH_REFRESH_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("dor_v1_", _DO_HEX_CHARSET, _DO_HEX_LEN),
            description="DigitalOcean OAuth refresh token - used to obtain new access tokens",
            example="dor_v1_[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["dop_v1_", "doo_v1_", "dor_v1_"]
