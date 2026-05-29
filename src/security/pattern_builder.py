"""
Pattern Builder — generates regex detectors from example keys.

Users provide one or more example keys (real or dummy with same format).
The builder analyzes structure and produces a precise, validated regex
with confidence scoring and false-positive risk assessment.

YAML usage in security_profile.yaml:

  custom_patterns:
    # Mode A — examples (system generates pattern):
    - name: "Your Secret Token"
      examples:
        - "INT_TKN_aBcD1234eFgH5678"
        - "INT_TKN_xYz9876wVuT5432"
        - "INT_TKN_mNoP2345qRsT6789"
      severity: HIGH

    # Mode B — manual regex (existing, unchanged):
    - name: "Legacy Token"
      pattern: 'LGC-[a-f0-9]{32}'
      severity: MEDIUM

Analysis pipeline:
  1. Find fixed prefix  (common left-anchor across all examples)
  2. Find fixed suffix  (common right-anchor across all examples)
  3. Extract variable parts
  4. Detect segmented structure (UUID-like, hyphen-separated)
  5. Classify character set (hex, base64, alphanumeric, …)
  6. Measure variable-part length (exact or range)
  7. Generate primary pattern
  8. Self-validate against every example
  9. Score confidence  (HIGH / MEDIUM / LOW)
 10. Score false-positive risk  (LOW / MEDIUM / HIGH)
 11. Generate alternative variants (strict / loose / context-anchored)
 12. Collect warnings
"""

import hashlib
import json
import math
import os
import re
import time as _time_module
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

_SEPARATORS  = frozenset('_-.:/\\|@#')
_HEX_CHARS   = frozenset('0123456789abcdefABCDEF')
# Base32 RFC 4648: A-Z + 2-7 (uppercase) or a-z + 2-7 (lowercase)
_B32_UPPER   = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567')
_B32_LOWER   = frozenset('abcdefghijklmnopqrstuvwxyz234567')

# Codebase FP scan settings
_CODEBASE_SCAN_EXTS = frozenset({
    '.py', '.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs',
    '.yaml', '.yml', '.json', '.toml', '.ini', '.cfg',
    '.sh', '.bash', '.zsh', '.rb', '.go', '.java', '.cs',
    '.php', '.html', '.vue', '.svelte', '.md', '.txt', '.xml',
    # Note: .env intentionally excluded — these files typically CONTAIN real secrets.
    # Counting a match there as a FP would be misleading.
})
_CODEBASE_SKIP_DIRS = frozenset({
    '.git', 'node_modules', '__pycache__', '.venv', 'venv',
    'dist', 'build', '.tox', 'coverage', '.pytest_cache',
    '.mypy_cache', '.ruff_cache', 'htmlcov', '.eggs', '.cloudbyte',
})
# Filenames that likely contain real secrets — skip to avoid counting TPs as FPs
_CODEBASE_SKIP_FILENAMES = frozenset({
    '.env', '.env.local', '.env.production', '.env.staging', '.env.development',
    'credentials.json', 'service_account.json', 'secrets.yaml', 'secrets.yml',
    'secrets.json', '.netrc', 'id_rsa', 'id_ed25519',
})
_CODEBASE_MAX_FILES   = 200
_CODEBASE_MAX_SECONDS = 2.5
_CODEBASE_MAX_FILE_BYTES = 100_000   # skip minified/generated files
_FP_CACHE_TTL_SECONDS = 86400        # re-scan after 24 hours

# Variable-part must be at least this long for HIGH confidence
_MIN_CONFIDENT_LEN = 8
# Prefix must be at least this long for LOW false-positive risk
_MIN_SAFE_PREFIX_LEN = 3


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class GeneratedPattern:
    """
    Result of analyzing example keys.

    confidence       — how reliably the builder inferred the pattern
    false_positive_risk — how likely the pattern is to fire on benign text
    alternatives     — looser / stricter / context-anchored variants the user
                       can substitute if the primary pattern over- or under-fires
    """
    name:                str
    pattern:             str
    confidence:          str           # HIGH / MEDIUM / LOW
    confidence_reason:   str
    severity:            str           # user-set
    false_positive_risk: str           # LOW / MEDIUM / HIGH
    fp_risk_reason:      str
    alternatives:           list[str] = field(default_factory=list)
    examples_matched:       int = 0
    examples_total:         int = 0
    warnings:               list[str] = field(default_factory=list)
    # -1 means codebase scan was not run (no cwd provided)
    codebase_fp_count:      int = -1
    codebase_files_checked: int = 0

    def to_scan_config_entry(self) -> dict:
        """Convert to format expected by scanner._scan_custom()."""
        return {
            "name":                    self.name,
            "pattern":                 self.pattern,
            "severity":                self.severity,
            "_confidence":             self.confidence,
            "_fp_risk":                self.false_positive_risk,
            "_warnings":               self.warnings,
            "_codebase_fp_count":      self.codebase_fp_count,
            "_codebase_files_checked": self.codebase_files_checked,
        }

    def summary(self) -> str:
        lines = [
            f"Pattern        : {self.pattern}",
            f"Confidence     : {self.confidence}  ({self.confidence_reason})",
            f"FP risk        : {self.false_positive_risk}  ({self.fp_risk_reason})",
            f"Validated      : {self.examples_matched}/{self.examples_total} examples matched",
        ]
        if self.codebase_fp_count >= 0:
            lines.append(
                f"Codebase scan  : {self.codebase_fp_count} FP hit(s) in "
                f"{self.codebase_files_checked} project files"
            )
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"  ! {w}" for w in self.warnings)
        if self.alternatives:
            lines.append("Alternatives:")
            lines.extend(f"  {a}" for a in self.alternatives)
        return "\n".join(lines)


# ── Internal analysis helpers ─────────────────────────────────────────────────

def _common_prefix(strings: list[str]) -> str:
    """Longest common prefix across all strings."""
    if not strings:
        return ""
    prefix: list[str] = []
    for chars in zip(*strings):
        if len(set(chars)) == 1:
            prefix.append(chars[0])
        else:
            break
    return "".join(prefix)


def _common_suffix(strings: list[str], prefix_len: int) -> str:
    """Longest common suffix in the variable portions (after prefix_len)."""
    tails = [s[prefix_len:] for s in strings]
    rev = _common_prefix([t[::-1] for t in tails])
    return rev[::-1]


def _detect_variable_prefix(examples: list[str]) -> tuple[str, str]:
    """
    Detect mixed-environment prefixes where examples differ only in a
    small set of leading words before a common core pattern.

    Example:
      ["prod_TKN_aBcD1234eFgH5678",
       "dev_TKN_xYz9876wVuT5432",
       "staging_TKN_mNoP2345qRsT6789"]
    Returns:
      env_group = "(?:prod|dev|staging)"
      core_prefix = "_TKN_"

    Returns ("", "") if no variable-prefix pattern is detected.
    Only applies when the common prefix across all examples is empty or very short.
    At most 5 distinct prefix variants are grouped (more than that → too ambiguous).
    """
    if not examples or len(examples) < 2:
        return "", ""

    # Find where the common inner suffix begins by looking for a shared separator sequence
    # Try every suffix of the first example as a candidate "core" that all examples share
    first = examples[0]

    for sep_pos in range(len(first)):
        if first[sep_pos] not in _SEPARATORS:
            continue
        # Candidate core: everything from sep_pos onward that is common to all examples
        suffix_candidate = first[sep_pos:]
        # Check if all examples end with a substring that starts with suffix_candidate chars
        # Find the inner common prefix across all examples from the separator position
        inner_suffixes = []
        for ex in examples:
            idx = ex.find(suffix_candidate[:2])   # match first 2 chars of candidate
            if idx == -1:
                inner_suffixes = []
                break
            inner_suffixes.append(ex[idx:])

        if not inner_suffixes:
            continue

        inner_common = _common_prefix(inner_suffixes)
        if len(inner_common) < 3:
            continue

        # The part before the inner common in each example is the "env prefix"
        env_parts = []
        for ex in examples:
            idx = ex.find(inner_common)
            if idx == -1:
                env_parts = []
                break
            env_parts.append(ex[:idx])

        if not env_parts:
            continue

        # All env parts must be non-empty and there must be 2–5 distinct values
        distinct_envs = list(dict.fromkeys(env_parts))   # preserve order, deduplicate
        if len(distinct_envs) < 2 or len(distinct_envs) > 5:
            continue
        if any(not e for e in distinct_envs):
            continue

        # Found a valid variable-prefix pattern
        escaped_envs = "|".join(re.escape(e) for e in distinct_envs)
        return f"(?:{escaped_envs})", inner_common

    return "", ""


def _heuristic_prefix_single(example: str) -> str:
    """
    Infer fixed prefix from one example by finding the last separator that
    is followed by enough random-looking content.

    A key like  rzp_live_ABC123XYZ789  has separator '_' at pos 3 and 7.
    The second split gives 'rzp_live_' as prefix and 12 random chars — good.
    """
    best = 0
    for i, ch in enumerate(example):
        if ch in _SEPARATORS:
            remainder_len = len(example) - (i + 1)
            if remainder_len >= _MIN_CONFIDENT_LEN:
                best = i + 1
    return example[:best]


def _classify_chars(samples: list[str]) -> str:
    """
    Return the tightest regex character class that covers every character
    across all samples. Produces specific classes (hex, base64, …) before
    falling back to generic alphanumeric.
    """
    if not samples or not any(samples):
        return "[a-zA-Z0-9]"

    chars: set[str] = set()
    for s in samples:
        chars.update(s)

    # ── Hex only ──────────────────────────────────────────────────────────────
    if chars <= _HEX_CHARS:
        has_up = any(c.isupper() for c in chars if c.isalpha())
        has_lo = any(c.islower() for c in chars if c.isalpha())
        if has_up and not has_lo:
            return "[0-9A-F]"
        if has_lo and not has_up:
            return "[0-9a-f]"
        return "[0-9a-fA-F]"

    # ── Base32 RFC 4648 (A-Z + 2-7, or a-z + 2-7) ────────────────────────
    # Common in TOTP secrets, some internal systems. More specific than alphanumeric.
    if chars <= _B32_UPPER:
        return "[A-Z2-7]"
    if chars <= _B32_LOWER:
        return "[a-z2-7]"

    has_upper = any(c.isupper() for c in chars)
    has_lower = any(c.islower() for c in chars)
    has_digit = any(c.isdigit() for c in chars)
    has_dash  = "-" in chars
    has_under = "_" in chars
    has_plus  = "+" in chars
    has_slash = "/" in chars
    has_eq    = "=" in chars
    has_dot   = "." in chars

    # ── Standard base64 (+ / =) ────────────────────────────────────────────
    if has_upper and has_lower and has_digit and (has_plus or has_slash) \
            and not has_dash and not has_under:
        sfx = "=" if has_eq else ""
        return f"[a-zA-Z0-9+/{sfx}]"

    # ── URL-safe base64 (- _) ──────────────────────────────────────────────
    if has_upper and has_lower and has_digit and has_dash and has_under \
            and not has_plus and not has_slash:
        sfx = "=" if has_eq else ""
        return f"[a-zA-Z0-9\\-_{sfx}]"

    # ── Generic: build minimum required class ─────────────────────────────
    parts: list[str] = []
    if has_digit:
        parts.append("0-9")
    if has_lower:
        parts.append("a-z")
    if has_upper:
        parts.append("A-Z")
    if has_dash:
        parts.append("\\-")
    if has_under:
        parts.append("_")
    if has_dot:
        parts.append("\\.")
    return f"[{''.join(parts)}]" if parts else "[a-zA-Z0-9]"


def _measure_length(samples: list[str]) -> tuple[int, int, str]:
    """
    Return (min_len, max_len, quantifier_string).

    Adds ±tolerance when lengths vary across examples so minor format
    changes (e.g. a version bump that adds 2 chars) still get caught.
    """
    if not samples:
        return (8, 8, "{8}")
    lengths = sorted({len(s) for s in samples})
    lo, hi = min(lengths), max(lengths)
    if lo == hi:
        return (lo, hi, f"{{{lo}}}")
    tol = max(1, round((hi - lo) * 0.25))
    return (lo, hi, f"{{{max(1, lo - tol)},{hi + tol}}}")


def _detect_segments(samples: list[str], sep: str = "-") -> Optional[str]:
    """
    Detect structured tokens where all samples share the same number of
    segments separated by `sep` (hyphen for UUID-like; dot for JWT-like).
    Returns a segment-aware regex fragment or None.

    Examples handled:
      Hyphens: 550e8400-e29b-41d4-a716-446655440000  (UUID)
      Dots:    v1.ABC123DEF456.checksum0123456789abcd  (custom token)
    """
    if not all(sep in s for s in samples):
        return None
    splits = [s.split(sep) for s in samples]
    n_segs = len(splits[0])
    if n_segs < 2 or not all(len(sp) == n_segs for sp in splits):
        return None
    seg_parts: list[str] = []
    for i in range(n_segs):
        seg_samples = [sp[i] for sp in splits]
        lengths = {len(s) for s in seg_samples}
        if len(lengths) != 1:
            return None  # inconsistent segment length
        cc = _classify_chars(seg_samples)
        seg_parts.append(f"{cc}{{{lengths.pop()}}}")
    escaped_sep = re.escape(sep)
    return escaped_sep.join(seg_parts)


def _entropy_prefix_single(example: str) -> str:
    """
    Infer fixed prefix from a single example using Shannon entropy.
    Scans from left to right; returns the longest prefix whose characters
    have lower entropy than the remainder.

    This handles prefixes without separators, e.g.:
      prodABCD1234EFGH5678  →  prefix='prod', variable='ABCD1234EFGH5678'

    Strategy: expand the prefix one character at a time; stop when adding
    the next character would raise the suffix entropy significantly.
    A simple heuristic: prefix chars tend to be human-readable (lower entropy)
    while the variable part has high entropy (e.g. > 3.0 bits/char).
    """
    if len(example) < _MIN_CONFIDENT_LEN + 2:
        return ""

    # First try separator-based heuristic (faster, more reliable when available)
    sep_prefix = _heuristic_prefix_single(example)
    if sep_prefix:
        return sep_prefix

    # Entropy-based fallback for no-separator tokens
    # Find the longest prefix such that the remaining suffix has high entropy
    best_prefix_len = 0
    for split in range(1, len(example) - _MIN_CONFIDENT_LEN + 1):
        suffix = example[split:]
        if len(suffix) < _MIN_CONFIDENT_LEN:
            break
        if _shannon_entropy(suffix) >= 3.5:
            best_prefix_len = split

    return example[:best_prefix_len]


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for c in s:
        freq[c] = freq.get(c, 0) + 1
    n = len(s)
    return -sum((cnt / n) * math.log2(cnt / n) for cnt in freq.values())


def _confidence(
    n_examples: int,
    prefix: str,
    variable_parts: list[str],
    validates: bool,
) -> tuple[str, str]:
    issues: list[str] = []
    if n_examples < 2:
        issues.append("single example — add more to improve accuracy")
    if not validates:
        issues.append("generated pattern does not match all examples")
    if len(prefix) < _MIN_SAFE_PREFIX_LEN:
        issues.append("prefix is very short — higher false-positive risk")
    min_var_len = min((len(v) for v in variable_parts), default=0)
    if min_var_len < _MIN_CONFIDENT_LEN:
        issues.append(f"variable part only {min_var_len} chars")
    mean_entropy = (
        sum(_shannon_entropy(v) for v in variable_parts) / len(variable_parts)
        if variable_parts else 0
    )
    if mean_entropy < 2.0:
        issues.append(f"low entropy ({mean_entropy:.1f}) — may not be a secret")

    if not issues:
        return "HIGH", (
            f"{n_examples} examples, prefix='{prefix}', "
            f"{min_var_len}-char variable part"
        )
    if len(issues) == 1:
        return "MEDIUM", issues[0]
    return "LOW", "; ".join(issues[:2])


def _fp_risk(prefix: str, char_class: str, min_len: int) -> tuple[str, str]:
    plen = len(prefix)
    if plen >= 5:
        return "LOW", f"distinctive prefix '{prefix}' ({plen} chars) anchors pattern"
    if plen >= _MIN_SAFE_PREFIX_LEN and min_len >= 12:
        return "LOW", f"prefix '{prefix}' + {min_len}-char minimum"
    if plen >= _MIN_SAFE_PREFIX_LEN:
        return "MEDIUM", f"prefix '{prefix}' is short — ensure {min_len}-char min is distinctive"
    if min_len >= 32:
        return "MEDIUM", f"no prefix but {min_len}-char minimum provides specificity"
    if char_class in ("[0-9a-f]", "[0-9A-F]", "[0-9a-fA-F]") and min_len >= 16:
        return "MEDIUM", f"hex-only {min_len}-char — specific but may match hash values"
    return "HIGH", (
        f"no distinctive prefix and short length ({min_len}) — "
        "provide more examples or add a prefix to your key format"
    )


_CLEAN_TEXT_CORPUS = """
def get_user(user_id):
    return db.query(User).filter(User.id == user_id).first()

config = {"host": "localhost", "port": 5432, "database": "myapp"}
password_policy = {"min_length": 8, "require_uppercase": True}

import os
DB_HOST = os.getenv("DB_HOST", "localhost")
API_URL = os.getenv("API_URL", "https://api.example.com")

class AuthService:
    def authenticate(self, token):
        return self.verify_token(token)

    def get_bearer_info(self):
        return {"type": "bearer", "expires": 3600}

# Example usage
result = requests.get(url, headers={"Content-Type": "application/json"})
logger.info("Processing request for user %s", user_id)

def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

colors = ["red", "green", "blue", "yellow"]
status_codes = {200: "ok", 404: "not found", 500: "error"}

user_data = {"name": "Alice", "email": "alice@example.com", "role": "admin"}
session_key = session.get("auth_state", None)
""".strip()


def _check_clean_text_fps(pattern: str) -> int:
    """
    Run the pattern against typical developer code and count how many
    times it fires on clearly benign content.
    Returns the false-positive hit count.
    """
    try:
        compiled = re.compile(pattern)
        return len(compiled.findall(_CLEAN_TEXT_CORPUS))
    except re.error:
        return 0


# ── Codebase FP scan helpers ──────────────────────────────────────────────────

def _fp_cache_path() -> Path:
    return Path.home() / ".cloudbyte" / "pattern_fp_cache.json"


def _fp_cache_key(name: str, examples: list[str]) -> str:
    raw = name + "\x00" + "\x00".join(sorted(examples))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _get_cached_fp(cache_key: str) -> dict | None:
    try:
        data = json.loads(_fp_cache_path().read_text(encoding="utf-8"))
        entry = data.get(cache_key)
        if entry and (_time_module.time() - entry.get("ts", 0)) < _FP_CACHE_TTL_SECONDS:
            return entry
    except Exception:
        pass
    return None


def _set_cached_fp(cache_key: str, fp_count: int, files_checked: int, scan_ms: int) -> None:
    try:
        cache_file = _fp_cache_path()
        cache: dict = {}
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        cache[cache_key] = {
            "fp_count": fp_count,
            "files_checked": files_checked,
            "scan_ms": scan_ms,
            "ts": _time_module.time(),
        }
        # Keep only the 50 most recent entries
        if len(cache) > 50:
            oldest = sorted(cache, key=lambda k: cache[k].get("ts", 0))
            for k in oldest[:-50]:
                del cache[k]
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    except Exception:
        pass


def _scan_codebase_files(pattern: str, cwd: str) -> tuple[int, int, int]:
    """
    Scan project source files with `pattern` and count false-positive hits.

    Returns (fp_count, files_checked, elapsed_ms).
    Bails out early if the file count or time budget is exceeded.
    """
    try:
        compiled = re.compile(pattern)
    except re.error:
        return 0, 0, 0

    fp_count = 0
    files_checked = 0
    start = _time_module.monotonic()

    for root, dirs, files in os.walk(cwd):
        # Prune noise directories in-place (modifies dirs to skip os.walk recursion)
        dirs[:] = [
            d for d in dirs
            if d not in _CODEBASE_SKIP_DIRS and not d.startswith(".")
        ]

        for fname in files:
            elapsed = _time_module.monotonic() - start
            if elapsed > _CODEBASE_MAX_SECONDS or files_checked >= _CODEBASE_MAX_FILES:
                return fp_count, files_checked, int(elapsed * 1000)

            # Skip files that typically contain real secrets (not FPs)
            if fname in _CODEBASE_SKIP_FILENAMES:
                continue

            ext = os.path.splitext(fname)[1].lower()
            if ext not in _CODEBASE_SCAN_EXTS:
                continue

            fpath = os.path.join(root, fname)
            try:
                size = os.path.getsize(fpath)
                if size > _CODEBASE_MAX_FILE_BYTES:
                    continue
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    content = f.read(_CODEBASE_MAX_FILE_BYTES)
                fp_count += len(compiled.findall(content))
                files_checked += 1
            except Exception:
                continue

    elapsed_ms = int((_time_module.monotonic() - start) * 1000)
    return fp_count, files_checked, elapsed_ms


def _alternatives(
    prefix: str,
    suffix: str,
    char_class: str,
    min_len: int,
    max_len: int,
) -> list[str]:
    ep = re.escape(prefix)
    es = re.escape(suffix) if suffix else ""

    # Strict: exact length
    q_strict = f"{{{min_len}}}" if min_len == max_len else f"{{{min_len},{max_len}}}"
    strict = f"(?<![a-zA-Z0-9]){ep}{char_class}{q_strict}{es}(?![a-zA-Z0-9])"

    # Loose: ±4 length tolerance, no boundary
    q_loose = f"{{{max(1, min_len - 4)},{max_len + 4}}}"
    loose = f"{ep}{char_class}{q_loose}{es}"

    alts = [f"strict  : {strict}", f"loose   : {loose}"]

    # Context-anchored (use when prefix is too short)
    if len(prefix) < _MIN_SAFE_PREFIX_LEN:
        ctx = (
            f"(?i)(?:api.?key|secret|token|auth.?token|access.?key)"
            f"\\s*[:=]\\s*['\"]?{char_class}{q_strict}['\"]?"
        )
        alts.append(f"context : {ctx}")

    return alts


def _safe_compile(pattern: str) -> Optional[re.Pattern]:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def _fallback_pattern(prefix: str, char_class: str, length_spec: str) -> str:
    ep = re.escape(prefix)
    return f"{ep}{char_class}{length_spec}"


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_examples(
    name: str,
    examples: list[str],
    severity: str = "HIGH",
    cwd: str | None = None,
) -> GeneratedPattern:
    """
    Generate a regex pattern from one or more example keys.

    Args:
        name:     Human-readable name for this secret type.
        examples: Example key strings (real or safely-anonymised dummies).
        severity: HIGH / MEDIUM / LOW — how critical is this if leaked.
        cwd:      Project working directory for real codebase FP scan (optional).
                  When provided, the generated pattern is tested against actual
                  project files using a file-based cache (24-hour TTL).

    Returns:
        GeneratedPattern with pattern, confidence, FP risk, alternatives.

    Raises:
        ValueError: if no usable examples are provided.
    """
    examples = [str(e).strip() for e in examples if str(e).strip()]
    if not examples:
        raise ValueError(f"No usable examples provided for pattern '{name}'")

    warnings: list[str] = []
    n = len(examples)

    # ── 1. Prefix ─────────────────────────────────────────────────────────────
    prefix = _common_prefix(examples) if n >= 2 else _entropy_prefix_single(examples[0])

    # ── 1b. Mixed-environment prefix detection ────────────────────────────────
    # When common prefix is very short (e.g. empty), check if examples differ only
    # in a small set of leading environment labels (prod/dev/staging).
    env_group = ""
    env_core_prefix = ""
    if n >= 2 and len(prefix) < _MIN_SAFE_PREFIX_LEN:
        env_group, env_core_prefix = _detect_variable_prefix(examples)
        if env_group:
            # Adjust effective prefix for downstream analysis
            prefix = env_core_prefix
            warnings.append(
                f"Examples have variable environment prefixes — "
                f"generated pattern uses optional group {env_group}"
            )

    # ── 2. Suffix ─────────────────────────────────────────────────────────────
    suffix = _common_suffix(examples, len(prefix)) if n >= 2 else ""

    # ── 3. Variable parts ─────────────────────────────────────────────────────
    suf_len = len(suffix)
    var_parts = [
        e[len(prefix): len(e) - suf_len if suf_len else len(e)]
        for e in examples
    ]

    if any(not v for v in var_parts):
        warnings.append(
            "Some examples produced empty variable parts — "
            "prefix/suffix detection may be over-eager. Adding more diverse examples helps."
        )
        var_parts = [v or e for v, e in zip(var_parts, examples)]

    if n >= 2 and len(set(var_parts)) == 1:
        warnings.append(
            "All examples share identical variable parts — "
            "pattern will be too specific. Provide varied examples."
        )

    # ── 4. Segment structure (UUID-like) ──────────────────────────────────────
    # Try hyphen segments first (UUID-like), then dot segments (JWT-like custom tokens)
    segment_pattern = None
    if n >= 2:
        segment_pattern = _detect_segments(var_parts, sep="-") or _detect_segments(var_parts, sep=".")

    # ── 5. Char class + length ────────────────────────────────────────────────
    char_class = _classify_chars(var_parts)
    min_len, max_len, length_spec = _measure_length(var_parts)

    # ── 6. Build primary pattern ──────────────────────────────────────────────
    ep = re.escape(prefix)
    es = re.escape(suffix) if suffix else ""

    if segment_pattern:
        core = segment_pattern
    else:
        core = f"{char_class}{length_spec}"

    # Lookbehind prevents matching in the middle of longer tokens.
    # If variable-prefix group was detected, prepend it before the fixed prefix.
    if env_group:
        primary = f"(?<![a-zA-Z0-9]){env_group}{ep}{core}{es}"
    elif prefix:
        primary = f"(?<![a-zA-Z0-9]){ep}{core}{es}"
    elif suffix:
        primary = f"{core}{es}(?![a-zA-Z0-9])"
    else:
        primary = f"\\b{core}\\b"

    # ── 7. Validate + fallback ────────────────────────────────────────────────
    compiled = _safe_compile(primary)
    if compiled is None:
        warnings.append(f"Generated pattern had a regex error — using fallback pattern")
        primary = _fallback_pattern(prefix, char_class, length_spec)
        compiled = _safe_compile(primary)

    matched = 0
    if compiled:
        for ex in examples:
            if compiled.search(ex):
                matched += 1
            else:
                warnings.append(f"Pattern does not match example: '{ex[:50]}'")

    validates = matched == n

    # ── 8. Confidence ─────────────────────────────────────────────────────────
    conf, conf_reason = _confidence(n, prefix, var_parts, validates)

    # ── 9. False-positive risk ────────────────────────────────────────────────
    fp, fp_reason = _fp_risk(prefix, char_class, min_len)

    # ── 10. Alternatives ──────────────────────────────────────────────────────
    alts = _alternatives(prefix, suffix, char_class, min_len, max_len)

    # ── 11. Clean-text false-positive check ──────────────────────────────────
    # Test the pattern against typical developer code. If it fires on benign
    # content, escalate FP risk and warn the user with concrete guidance.
    clean_fp_hits = _check_clean_text_fps(primary)
    if clean_fp_hits > 0:
        fp = "HIGH" if clean_fp_hits >= 2 else "MEDIUM"
        fp_reason = (
            f"pattern fires {clean_fp_hits} time(s) on sample developer code "
            f"— likely to produce false positives in practice"
        )

    # ── 12. User guidance warnings ────────────────────────────────────────────
    if n == 1:
        warnings.append(
            "Only one example provided — confidence is limited. "
            "Add 2–3 more examples for a more accurate and reliable pattern."
        )
    if not validates:
        warnings.append(
            "Generated pattern does not match all provided examples. "
            "This may indicate the examples have inconsistent structure. "
            "Review your examples and ensure they all follow the same format."
        )
    if fp == "HIGH":
        warnings.append(
            f"High false-positive risk: {fp_reason}. "
            "Recommended action: use the 'context' alternative which requires "
            "the token to appear after a variable assignment like api_key=... "
            "This dramatically reduces false positives for tokens without a distinctive prefix."
        )
    elif fp == "MEDIUM" and clean_fp_hits > 0:
        warnings.append(
            f"Pattern fired {clean_fp_hits} time(s) on sample developer code. "
            "Review prompts carefully — some legitimate code may be flagged."
        )
    if min_len < 6:
        warnings.append(
            f"Variable part is only {min_len} chars — very likely to produce false positives. "
            "If your token format is genuinely this short, rely on the 'context' alternative "
            "that anchors detection to variable assignment patterns."
        )
    if env_group:
        warnings.append(
            f"Mixed environment prefixes detected {env_group}. "
            "The generated pattern matches all variants. "
            "If you only want to detect production keys, use the strict alternative instead."
        )

    # ── 13. Codebase FP scan (if cwd provided) ───────────────────────────────
    codebase_fp_count = -1
    codebase_files_checked = 0

    if cwd and os.path.isdir(cwd):
        cache_key = _fp_cache_key(name, examples)
        cached = _get_cached_fp(cache_key)

        if cached:
            codebase_fp_count = cached["fp_count"]
            codebase_files_checked = cached["files_checked"]
        else:
            cb_fp, cb_files, cb_ms = _scan_codebase_files(primary, cwd)
            codebase_fp_count = cb_fp
            codebase_files_checked = cb_files
            _set_cached_fp(cache_key, cb_fp, cb_files, cb_ms)

            if cb_fp >= 5:
                fp = "HIGH"
                fp_reason = (
                    f"pattern fires {cb_fp} time(s) across {cb_files} project files "
                    f"— likely to produce many false positives in your codebase"
                )
                warnings.append(
                    f"Codebase scan: {cb_fp} false-positive hit(s) in {cb_files} project files. "
                    "Your pattern is firing on your own code — use the 'context' alternative "
                    "which requires the token to appear after a variable assignment (api_key=...)."
                )
            elif cb_fp >= 2:
                if fp == "LOW":
                    fp = "MEDIUM"
                    fp_reason = (
                        f"pattern fires {cb_fp} time(s) in your project files "
                        f"— some legitimate code may be flagged"
                    )
                warnings.append(
                    f"Codebase scan: {cb_fp} hit(s) in {cb_files} project files. "
                    "Review these occurrences to confirm they are real secrets, not legitimate code."
                )
            elif cb_fp == 0 and cb_files > 0:
                # Good result — pattern is clean on user's own code
                if fp == "MEDIUM":
                    fp = "LOW"
                    fp_reason = (
                        f"0 false positives across {cb_files} scanned project files"
                    )

    return GeneratedPattern(
        name=name,
        pattern=primary,
        confidence=conf,
        confidence_reason=conf_reason,
        severity=severity.upper(),
        false_positive_risk=fp,
        fp_risk_reason=fp_reason,
        alternatives=alts,
        examples_matched=matched,
        examples_total=n,
        warnings=warnings,
        codebase_fp_count=codebase_fp_count,
        codebase_files_checked=codebase_files_checked,
    )
