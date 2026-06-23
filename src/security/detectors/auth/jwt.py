"""
JWT Detector.

JWTs follow the structure:  eyJ[header].eyJ[payload].[signature]
Both header and payload are base64url-encoded JSON objects starting with "eyJ"
(the base64url encoding of '{"').

The detector captures the full three-part token.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

# Base64url alphabet: A-Z a-z 0-9 - _  (no padding)
_B64URL = r"[A-Za-z0-9\-_]"

# Full JWT pattern: eyJ<header>.eyJ<payload>.<signature>
# header and payload start with eyJ; signature may be empty (unsecured JWT)
_JWT_RE = re.compile(
    rf"eyJ{_B64URL}+\.eyJ{_B64URL}+\.{_B64URL}*",
    re.ASCII,
)


@register_detector
class JWTDetector(BaseDetector):
    CATEGORY           = "JWT"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "JSON Web Tokens (three-part base64url-encoded bearer tokens)"
    DOMAIN             = "Auth"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="JSON Web Token",
            label="JWT_TOKEN",
            severity="HIGH",
            detection="pattern",
            capture_group=0,
            pattern=_JWT_RE,
            description="JSON Web Token - signed bearer token containing user identity/claims, used for authentication",
            example="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["eyJ"]
