"""
Entropy Detector (disabled by default).

Self-implemented Shannon entropy — no external library.

Operates in three modes (all active when this detector is enabled):

1. Context-anchored: high-entropy value assigned to a secret-sounding variable
   name (password, api_key, token, azure_storage_key, ibm_api_key, …).
   Threshold: 3.8 bits/char (lower — variable name already signals intent).

2. Bare hex: high-entropy standalone hex string (32–128 chars).
   Catches hex-encoded API keys, HMACs, IBM/Azure keys, etc.
   Threshold: 3.5 bits/char (hex max is ~4.0).

3. Bare base64: high-entropy standalone base64/base64url string (32–512 chars).
   Catches base64-encoded credentials, Azure storage keys, etc.
   Threshold: 4.5 bits/char (base64 max is ~6.0; high bar needed to reduce noise).

Cross-detector deduplication in scanner.py ensures bare-entropy findings are
dropped whenever a more specific detector (AWS, GitHub, etc.) already claimed
the same character range — making entropy a true fallback.
"""

import math
import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


# --- Pure-Python Shannon entropy -------------------------------------------------

def _shannon_entropy(s: str) -> float:
    """Return bits-per-character Shannon entropy of *s*."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


# Thresholds
_ENTROPY_THRESHOLD_CONTEXT = 3.8   # bits/char — weaker when variable name signals intent
_ENTROPY_THRESHOLD_HEX     = 3.5   # bits/char — hex-encoded secrets (IBM, Azure, HMAC, etc.)
_ENTROPY_THRESHOLD_B64     = 4.5   # bits/char — base64-encoded secrets (Azure storage, etc.)
_MIN_SECRET_LEN            = 20    # ignore shorter values
_MAX_SECRET_LEN            = 512   # avoid matching large blobs

# Context pattern: variable_name = "value"
_CONTEXT_NAMES = [
    # Generic
    "password", "passwd", "pwd", "secret", "api_key", "apikey", "token",
    "auth_token", "access_token", "private_key", "encryption_key",
    "signing_key", "hmac_key", "credential", "credentials",
    # Azure-specific
    "azure_key", "account_key", "storage_key", "accountkey",
    "azure_storage_key", "azure_client_secret", "connection_string",
    # IBM-specific
    "ibm_api_key", "ibm_key", "ibm_iam_key", "ibm_cloud_api_key",
]
_CONTEXT_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(n) for n in _CONTEXT_NAMES)
    + r")\s*[:=]\s*['\"]?([A-Za-z0-9+/=_\-]{" + str(_MIN_SECRET_LEN) + r",512})['\"]?",
    re.IGNORECASE,
)

# Bare hex: standalone hex string, word-boundary delimited
# 32 chars minimum = MD5 hash length; captures IBM API keys, Azure keys, HMAC secrets
_BARE_HEX_RE = re.compile(
    r"(?<![0-9a-fA-F])"
    r"([0-9a-fA-F]{32,128})"
    r"(?![0-9a-fA-F])",
)

# Bare base64: standalone base64/base64url string, word-boundary delimited
# High threshold (4.5) needed — many code identifiers and URLs would otherwise match
_BARE_B64_RE = re.compile(
    r"(?<![A-Za-z0-9+/=_\-])"
    r"([A-Za-z0-9+/=_\-]{32,512})"
    r"(?![A-Za-z0-9+/=_\-])",
)


@register_detector
class EntropyDetector(BaseDetector):
    CATEGORY           = "Entropy Secret"
    ENABLED_BY_DEFAULT = False
    DESCRIPTION        = (
        "High-entropy strings (self-implemented Shannon entropy, no library). "
        "Context-anchored + bare hex/base64 modes — acts as fallback for unknown services."
    )
    DOMAIN             = "Generic"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="High-Entropy Context Value",
            label="HIGH_ENTROPY_SECRET",
            severity="MEDIUM",
            detection="context",
            capture_group=1,
            pattern=_CONTEXT_RE,
            description="High-entropy value assigned to a secret-sounding variable — likely a credential or key",
            example="api_key=aX9bY2cZ3dW4eV5fU6gT7hS8iR9jQ0kP1lO2mN3",
        ),
        TokenDefinition(
            type="High-Entropy Hex String",
            label="HIGH_ENTROPY_HEX",
            severity="LOW",
            detection="pattern",
            capture_group=1,
            pattern=_BARE_HEX_RE,
            description="High-entropy hex string — may be an API key, HMAC, IBM or Azure credential",
            example="a3f1b2c4d5e6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
        ),
        TokenDefinition(
            type="High-Entropy Base64 String",
            label="HIGH_ENTROPY_B64",
            severity="LOW",
            detection="pattern",
            capture_group=1,
            pattern=_BARE_B64_RE,
            description="High-entropy base64 string — may be an Azure storage key, IBM IAM token, or other encoded credential",
            example="dGhpcyBpcyBhIGZha2Uga2V5IGZvciBleGFtcGxl",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        # Return empty so can_skip() never skips this detector.
        # Entropy is a fallback for unknown services — it must run on all text.
        return []

    def _post_filter(self, value: str, definition: TokenDefinition) -> bool:
        """Apply per-mode entropy threshold; True = keep the finding."""
        h = _shannon_entropy(value)
        if definition.label == "HIGH_ENTROPY_HEX":
            threshold = _ENTROPY_THRESHOLD_HEX
        elif definition.label == "HIGH_ENTROPY_B64":
            threshold = _ENTROPY_THRESHOLD_B64
        else:
            threshold = _ENTROPY_THRESHOLD_CONTEXT
        return h >= threshold and len(value) >= _MIN_SECRET_LEN


# Make entropy helper importable for scanner
shannon_entropy = _shannon_entropy
