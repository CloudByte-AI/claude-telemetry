"""
Shared value-validation helpers for the low-precision *generic* detectors
(keyword + entropy). A pattern/keyword/entropy match only *locates* a candidate;
these predicates inspect the captured VALUE and decide whether it is plausibly a
real secret. This is where false-positive reduction actually happens, so the
logic lives in one place and is unit-tested directly.

None of these helpers look at surrounding context - they operate purely on the
captured value string, because that is all a detector's `_post_filter(value)`
hook receives.
"""

import base64
import binascii
import re

# ── Placeholder / redaction markers ───────────────────────────────────────────
# Values that are explicitly NOT the real secret (already masked, templated, …).
_PLACEHOLDER_RE = re.compile(
    r"""(?ix)
      \[\s*(?:MASKED|REDACTED|HIDDEN|SECRET|SECRETS|REMOVED)\b   # [MASKED], [REDACTED:...]
    | (?:MASKED|REDACTED|HIDDEN)\s*\]
    | ^< .* >$                                                  # <your-token-here>
    | ^[*xX•.\-_]{4,}$                                          # ****, xxxx, ----, ....
    """,
    re.VERBOSE,
)

# ── Environment / variable references ──────────────────────────────────────────
# The value points at a secret, it is not one.
_ENVREF_RE = re.compile(
    r"""(?ix)
      ^\$\{?\w+\}?$              # $TOKEN / ${TOKEN}
    | ^%\w+%$                    # %TOKEN%   (windows)
    | os\.environ                # os.environ[...] / os.environ.get(...)
    | process\.env               # process.env.X
    | \bgetenv\b                 # getenv("X")
    """,
    re.VERBOSE,
)

# ── Common non-secret words assigned to secret-named variables ─────────────────
_STOPWORDS = frozenset({
    "changeme", "change_me", "changethis", "example", "examplekey",
    "test", "testing", "testkey", "yourtokenhere", "yourkeyhere",
    "none", "null", "nil", "true", "false", "undefined", "empty",
    "password", "passwd", "secret", "token", "apikey", "api_key",
    "xxx", "todo", "tbd", "placeholder", "dummy", "sample",
    "foo", "bar", "foobar", "baz", "abc", "abcdefgh", "12345678",
})

# ── Hash / digest shapes ───────────────────────────────────────────────────────
# Exact hex lengths of the common digests. A bare hex string of exactly one of
# these lengths is overwhelmingly a checksum/commit-sha/etag, not a credential.
_HASH_HEX_LENGTHS = frozenset({32, 40, 64, 128})   # md5, sha1, sha256, sha512
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")

# UUID (with dashes) - not a secret on its own.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def normalize_value(value: str) -> str:
    """Strip surrounding whitespace and a single layer of quotes."""
    return (value or "").strip().strip("'\"").strip()


def is_placeholder_or_reference(value: str) -> bool:
    """Gate 1a: value is a placeholder/redaction or an env/variable reference."""
    v = normalize_value(value)
    if not v:
        return True
    if _PLACEHOLDER_RE.search(v):
        return True
    if _ENVREF_RE.search(v):
        return True
    if v.lower() in _STOPWORDS:
        return True
    if v.lower().startswith("your_") or v.lower().endswith("_here"):
        return True
    return False


def has_nonsecret_shape(value: str) -> bool:
    """
    Gate 1b: value is shaped like a URL / path / email / sentence, not an opaque
    credential token. An opaque token never contains a scheme, userinfo, a
    leading path separator, or whitespace.
    """
    v = normalize_value(value)
    if "://" in v or "@" in v:
        return True
    if v.startswith("/") or v.startswith("\\"):
        return True
    if any(c.isspace() for c in v):
        return True
    return False


def is_nonsecret_value(value: str) -> bool:
    """
    Gate 1 (combined): return True if the value is provably NOT a secret.
    Shared by the keyword detector and the entropy detector's context mode.
    """
    return is_placeholder_or_reference(value) or has_nonsecret_shape(value)


def looks_like_hash(value: str) -> bool:
    """
    True if the value is a bare hex string of exactly a common digest length
    (md5/sha1/sha256/sha512) or a UUID - i.e. almost certainly a checksum/id,
    not a credential. Used to suppress bare-hex entropy false positives.
    """
    v = normalize_value(value)
    if _UUID_RE.match(v):
        return True
    if _HEX_RE.match(v) and len(v) in _HASH_HEX_LENGTHS:
        return True
    return False


def base64_decodes_to_text(value: str) -> bool:
    """
    True if the value is valid base64 that decodes to mostly-printable ASCII text
    (an encoded sentence/JSON/word), rather than random secret bytes. Real
    base64-encoded secrets decode to high-entropy binary with few printable
    characters, so this cleanly separates "base64 of English" from a real key.
    """
    v = normalize_value(value)
    if len(v) < 8 or not re.match(r"^[A-Za-z0-9+/=_\-]+$", v):
        return False
    candidate = v.replace("-", "+").replace("_", "/")
    candidate += "=" * (-len(candidate) % 4)
    try:
        raw = base64.b64decode(candidate, validate=False)
    except (binascii.Error, ValueError):
        return False
    if len(raw) < 4:
        return False
    printable = sum(1 for b in raw if 32 <= b <= 126 or b in (9, 10, 13))
    return (printable / len(raw)) >= 0.90
