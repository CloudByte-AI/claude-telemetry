"""
Keyword / Variable-assignment Detector (disabled by default).

Catches bare secret-looking assignments where no specific prefix or category
detector fires.  Pattern is:
  <keyword>  <sep>  <quoted-or-unquoted-value>

This is intentionally broad - enable only when you want maximum coverage
and are willing to triage false positives.

Does NOT require high entropy; it fires on any non-trivial value (8+ chars)
assigned to a sensitive-sounding name.

── False-positive reduction (value validation) ──────────────────────────────
A keyword match is only a *locator*. Matching `token`/`secret`/… is not enough
to conclude a real credential is present - e.g. the username segment of a git
clone URL (`gitlab-ci-token:[MASKED]@host/repo.git`) trips the bare word
`token` yet contains no secret. So every candidate value is validated by
`_post_filter()` through three gates before a Finding is emitted:

  Gate 0  Whole-identifier boundary   - the keyword must not be the tail of a
          (in the regex below)          hyphenated/dotted word (`gitlab-ci-token`).
  Gate 1  Negative filters            - drop placeholders/redactions, stopwords,
                                         env/var references, and URL/path-shaped
                                         values (never opaque credential tokens).
  Gate 2  Structural check            - plausible length + minimal character
                                         variety (reject pure repetition/junk).
  Gate 3  Entropy floor (booster)     - a *low* floor that only strips
                                         near-repetition noise. Deliberately far
                                         below a "this is random ⇒ secret"
                                         threshold, because real secrets are
                                         often low-entropy (e.g. `Summer2024!`),
                                         so entropy must never be the sole gate.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.detectors.generic.entropy import shannon_entropy
from src.security.detectors.generic.value_filters import (
    is_nonsecret_value,
    normalize_value,
)
from src.security.registry import register_detector

# Keywords that strongly suggest a credential
_KEYWORDS = [
    "secret", "SECRET",
    "password", "PASSWORD", "passwd", "PASSWD",
    "api_key", "API_KEY", "apikey", "APIKEY",
    "api_secret", "API_SECRET",
    "access_key", "ACCESS_KEY",
    "access_token", "ACCESS_TOKEN",
    "auth_token", "AUTH_TOKEN",
    "private_key", "PRIVATE_KEY",
    "signing_key", "SIGNING_KEY",
    "encryption_key", "ENCRYPTION_KEY",
    "client_secret", "CLIENT_SECRET",
    "app_secret", "APP_SECRET",
    "token", "TOKEN",
    "credential", "CREDENTIAL",
]

_KEYWORD_ALT = "|".join(re.escape(k) for k in _KEYWORDS)

# ── Gate 0: whole-identifier left boundary ────────────────────────────────────
# The keyword must be at the start of the string or preceded by a character that
# is NOT part of an identifier/host/hyphenated word. Compared with the original
# `[^a-zA-Z0-9_]`, this ALSO excludes `.` and `-`, so the bare word `token` no
# longer matches inside `gitlab-ci-token` / `foo.token`. Snake_case compounds
# like `MY_SECRET_TOKEN` are unaffected (they were never matched: `_` already
# blocks the boundary, and the compound isn't in the keyword list).
#
# Supports:
#   secret = "value"
#   SECRET: value
#   secret=value
#   secret := "value"
_KEYWORD_RE = re.compile(
    rf"""(?:^|(?<=[^a-zA-Z0-9_.\-]))(?:{_KEYWORD_ALT})\s*[:=]{{1,2}}\s*['\"]?([A-Za-z0-9+/=_\-!@#$%^&*(){{}}[\]|;:<>,.?~`]{{8,512}})['\"]?""",
    re.MULTILINE,
)

# ── Gate 2 / Gate 3 local thresholds ──────────────────────────────────────────
# Gate 1 (placeholder / env-ref / stopword / URL-path shape) is the shared
# is_nonsecret_value() from value_filters. Gates 2-3 below are keyword-specific.
#
# The entropy floor is intentionally LOW. It is NOT the "looks random ⇒ secret"
# threshold (that is ~3.8 in the entropy detector); it only removes pathological
# repetition/sequence noise (e.g. "aaaaaaaa" ≈ 0.0, "abababab" ≈ 1.0) while
# staying well under the entropy of realistic low-diversity secrets like
# "Summer2024!" (≈ 3.1) - so entropy is never the sole gate.
_MIN_ENTROPY_FLOOR = 2.0
_MIN_VALUE_LEN = 8
_MAX_VALUE_LEN = 512
_MIN_UNIQUE_CHARS = 3   # reject "aaaa", "0000", two-symbol junk


def _is_nonsecret_value(value: str) -> bool:
    """
    Return True if *value* is provably NOT a credential (a false positive).
    Gate 1 (shared negative filters) + Gate 2 (structural) + Gate 3 (low
    entropy floor).
    """
    v = normalize_value(value)
    if not v:
        return True

    # ── Gate 1: shared negative filters (placeholder / env-ref / stopword / shape)
    if is_nonsecret_value(v):
        return True

    # ── Gate 2: structural check ──────────────────────────────────────────────
    if not (_MIN_VALUE_LEN <= len(v) <= _MAX_VALUE_LEN):
        return True
    if len(set(v)) < _MIN_UNIQUE_CHARS:
        return True

    # ── Gate 3: entropy floor (repetition/sequence noise only) ────────────────
    if shannon_entropy(v) < _MIN_ENTROPY_FLOOR:
        return True

    return False


@register_detector
class KeywordDetector(BaseDetector):
    CATEGORY           = "Keyword Secret"
    ENABLED_BY_DEFAULT = False
    DESCRIPTION        = "Variable assignments with secret-sounding names (off by default - broad / noisy)"
    DOMAIN             = "Generic"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Keyword-Based Secret",
            label="KEYWORD_SECRET",
            severity="LOW",
            detection="context",
            capture_group=1,
            pattern=_KEYWORD_RE,
            description="Value assigned to a security-sensitive variable name - may be a hardcoded credential",
            example="password=MySup3rS3cr3tP@ssword123!",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["secret", "SECRET", "password", "api_key", "API_KEY", "token", "TOKEN"]

    def _post_filter(self, value: str, definition: TokenDefinition) -> bool:
        """Keep the finding only if the captured value survives value validation."""
        return not _is_nonsecret_value(value)
