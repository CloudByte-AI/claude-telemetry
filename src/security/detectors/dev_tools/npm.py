"""NPM Detector - npm_ prefix + 36 alphanumeric chars."""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class NPMDetector(BaseDetector):
    CATEGORY           = "NPM"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "NPM registry authentication tokens (npm_ prefix)"
    DOMAIN             = "Developer Tools"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Access Token",
            label="NPM_ACCESS_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("npm_", r"[a-zA-Z0-9]", 36),
            description="NPM access token - used to publish/install packages and manage npm organization access",
            example="npm_[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["npm_"]
