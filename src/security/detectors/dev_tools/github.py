"""
GitHub Detector.

All GitHub token types share a 4-char prefix + 36 alphanumeric format,
except the fine-grained PAT which has a more complex structure.
"""

import re

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_GH_CHARSET = r"[a-zA-Z0-9]"
_GH_LEN     = 36


@register_detector
class GitHubDetector(BaseDetector):
    CATEGORY           = "GitHub"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "GitHub personal access tokens, OAuth tokens, and app installation tokens"
    DOMAIN             = "Developer Tools"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Personal Access Token (Classic)",
            label="GITHUB_CLASSIC_PAT",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ghp_", _GH_CHARSET, _GH_LEN),
            description="GitHub Personal Access Token (classic) - grants access to GitHub repositories and APIs",
            example="ghp_[EXAMPLE]",
        ),
        TokenDefinition(
            type="OAuth Access Token",
            label="GITHUB_OAUTH_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("gho_", _GH_CHARSET, _GH_LEN),
            description="GitHub OAuth access token - issued to OAuth apps, scoped to user's permitted resources",
            example="gho_[EXAMPLE]",
        ),
        TokenDefinition(
            type="User Access Token (GitHub App)",
            label="GITHUB_APP_USER_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ghu_", _GH_CHARSET, _GH_LEN),
            description="GitHub App user access token - issued when a user authorizes a GitHub App installation",
            example="ghu_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Installation Access Token (GitHub App)",
            label="GITHUB_APP_INSTALLATION_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ghs_", _GH_CHARSET, _GH_LEN),
            description="GitHub App installation access token - short-lived token for automated GitHub App operations",
            example="ghs_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Refresh Token (GitHub App)",
            label="GITHUB_APP_REFRESH_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ghr_", _GH_CHARSET, _GH_LEN),
            description="GitHub App OAuth refresh token - used to renew user access tokens without re-authorization",
            example="ghr_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Fine-Grained Personal Access Token",
            label="GITHUB_FINE_GRAINED_PAT",
            severity="HIGH",
            detection="prefix",
            # github_pat_ + 22-char identifier + _ + 59-char secret segment
            pattern=re.compile(
                r'(?<![a-zA-Z0-9\-_])github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59}'
                r'(?![a-zA-Z0-9\-_])'
            ),
            description="GitHub fine-grained PAT - repository-scoped token with granular permissions",
            example="github_pat_[EXAMPLE]_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_"]
