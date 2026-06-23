"""
Google Cloud Platform Detector.

Covers API keys (current AIza format + newer AQ. format), OAuth credentials
(client secret, access token, refresh token), and OAuth client IDs.
"""

import re

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class GCPDetector(BaseDetector):
    CATEGORY           = "GCP"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Google Cloud Platform API keys and OAuth credentials"
    DOMAIN             = "Cloud & Infrastructure"

    _DEFINITIONS: list[TokenDefinition] = [
        # ── API Key - current format (AIza prefix) ────────────────────────────
        TokenDefinition(
            type="API Key",
            label="GCP_API_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("AIza", r"[0-9A-Za-z\-_]", 35),
            known_safe=frozenset({"AIzaSyxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}),
            description="Google Cloud API key - grants access to GCP APIs (Maps, Vision, Translation, etc.)",
            example="AIzaSy[EXAMPLE]",
        ),

        # ── API Key - newer reported format (AQ. prefix) ─────────────────────
        TokenDefinition(
            type="API Key (New Format)",
            label="GCP_API_KEY_NEW",
            severity="HIGH",
            detection="prefix",
            # AQ. followed by base64url characters, minimum 20 chars after prefix
            pattern=re.compile(
                r'(?<![a-zA-Z0-9\-_])AQ\.[a-zA-Z0-9\-_\.]{20,}(?![a-zA-Z0-9\-_])'
            ),
            description="Google Cloud API key (newer format) - grants access to GCP services",
            example="AQ.[EXAMPLE]",
        ),

        # ── OAuth Client Secret ───────────────────────────────────────────────
        TokenDefinition(
            type="OAuth Client Secret",
            label="GCP_OAUTH_CLIENT_SECRET",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("GOCSPX-", r"[a-zA-Z0-9\-_]", 28),
            description="Google OAuth 2.0 client secret - used to authenticate OAuth flows for your application",
            example="GOCSPX-[EXAMPLE]",
        ),

        # ── OAuth Access Token (ya29. prefix, variable length) ────────────────
        TokenDefinition(
            type="OAuth Access Token",
            label="GCP_OAUTH_ACCESS_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=re.compile(
                r'(?<![a-zA-Z0-9\-_])ya29\.[a-zA-Z0-9\-_\.]{20,}(?![a-zA-Z0-9\-_\.])'
            ),
            description="Google OAuth 2.0 access token - short-lived bearer token granting API access",
            example="ya29.a0[EXAMPLE]",
        ),

        # ── OAuth Refresh Token (context-anchored - "1//" prefix is too generic) ──
        TokenDefinition(
            type="OAuth Refresh Token",
            label="GCP_OAUTH_REFRESH_TOKEN",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "google_refresh_token", "gcp_refresh_token",
                    "GOOGLE_REFRESH_TOKEN", "GCP_REFRESH_TOKEN", "refresh_token",
                ],
                value_charset=r"[a-zA-Z0-9\-_/]",
                value_min=20,
            ),
            description="Google OAuth 2.0 refresh token - used to obtain new access tokens without re-authentication",
            example="1//[EXAMPLE]",
        ),

        # ── OAuth Client ID (structural pattern) ─────────────────────────────
        TokenDefinition(
            type="OAuth Client ID",
            label="GCP_OAUTH_CLIENT_ID",
            severity="MEDIUM",
            detection="pattern",
            pattern=re.compile(
                r'(?<!\w)\d{6,30}-[a-zA-Z0-9]{16,32}\.apps\.googleusercontent\.com(?!\w)'
            ),
            description="Google OAuth 2.0 client ID - identifies your application (low sensitivity, but flag in context)",
            example="123456789012-abc123def456abc123def456.apps.googleusercontent.com",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return [
            "AIza", "AQ.", "GOCSPX-", "ya29.",
            "googleusercontent.com",
            "google_refresh_token", "GOOGLE_REFRESH_TOKEN",
        ]
