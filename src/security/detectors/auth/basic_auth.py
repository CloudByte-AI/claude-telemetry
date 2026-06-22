"""
Basic Auth Detector.

Detects credentials embedded directly in URLs:
  http(s)://username:password@host/path
  ftp://user:pass@host

Also catches:
  Authorization: Basic <base64-encoded user:pass>

Disabled by default because HTTP URLs with auth are frequently referenced
in documentation with placeholder values.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_URL_RE = re.compile(
    r"https?://([^:@/\s]{1,128}:[^@\s]{1,256})@[^\s/]+",
    re.IGNORECASE,
)

_FTP_RE = re.compile(
    r"ftps?://([^:@/\s]{1,128}:[^@\s]{1,256})@[^\s/]+",
    re.IGNORECASE,
)

_BASIC_HDR_RE = re.compile(
    r"(?:Authorization\s*[=:]\s*)?[Bb]asic\s+([A-Za-z0-9+/=]{8,512})",
    re.IGNORECASE,
)


@register_detector
class BasicAuthDetector(BaseDetector):
    CATEGORY           = "Basic Auth"
    ENABLED_BY_DEFAULT = False
    DESCRIPTION        = "Credentials embedded in HTTP/FTP URLs or Authorization: Basic headers (off by default)"
    DOMAIN             = "Auth"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Credentials in HTTP URL",
            label="HTTP_URL_CREDENTIALS",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_URL_RE,
            description="Username and password embedded directly in an HTTP URL — transmitted in plain text",
            example="https://admin:s3cr3tP4ss@api.example.com/endpoint",
        ),
        TokenDefinition(
            type="Credentials in FTP URL",
            label="FTP_URL_CREDENTIALS",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_FTP_RE,
            description="Username and password embedded in an FTP URL",
            example="ftp://ftpuser:s3cr3tP4ss@ftp.example.com/files",
        ),
        TokenDefinition(
            type="HTTP Basic Auth Header",
            label="HTTP_BASIC_AUTH_HEADER",
            severity="HIGH",
            detection="pattern",
            capture_group=1,
            pattern=_BASIC_HDR_RE,
            description="HTTP Basic authentication header — base64-encoded username:password pair",
            example="Authorization: Basic YWRtaW46czNjcjN0UDRzcw==",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["http://", "https://", "ftp://", "Basic "]
