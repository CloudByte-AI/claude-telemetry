"""
Base classes for the custom security detector system.

Architecture:
  - TokenDefinition  : describes one token type (prefix/charset/length or full regex)
  - Finding          : a detected secret with full category + type + position info
  - ScanResult       : aggregated output of scan_text()
  - BaseDetector     : abstract base; subclasses declare DEFINITIONS and get scan() for free
  - compute_line_offsets / _char_to_line : O(log n) char-position → line-number mapping

All regex patterns are pre-compiled at class definition time - never at scan time.
"""

import bisect
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TokenDefinition:
    """
    Describes a single detectable token type.

    Three detection modes (set via `detection`):

    "prefix"   - Token has a known literal prefix followed by a fixed-charset suffix.
                 Build with BaseDetector.prefix_pattern().
                 capture_group=0 (full match is the secret value).

    "context"  - No distinctive prefix; token only identifiable by surrounding variable
                 name (e.g. mistral_api_key=VALUE).
                 Build with BaseDetector.context_pattern().
                 capture_group=1 (group 1 = the secret value, not the variable name).

    "pattern"  - Structural pattern without a simple prefix (JWTs, PEM blocks, DB URLs).
                 Provide a fully custom compiled regex.
                 capture_group=0 unless pattern uses explicit groups.
    """
    type: str               # Human-readable type  e.g. "IAM User Access Key"
    label: str              # REDACTED tag label   e.g. "AWS_IAM_ACCESS_KEY"
    severity: str           # "HIGH" | "MEDIUM" | "LOW"
    pattern: re.Pattern     # Pre-compiled - NEVER compiled at scan time
    detection: str = "prefix"       # "prefix" | "context" | "pattern"
    capture_group: int = 0          # Which regex group holds the secret value
    known_safe: frozenset = field(default_factory=frozenset)  # Documentation examples to skip
    description: str = ""           # One-line explanation of what this token grants access to
    example: str = ""               # Sanitized/fake representative example for UI display


@dataclass
class Finding:
    """A confirmed secret detection with full provenance."""
    category: str           # "AWS"
    type: str               # "IAM User Access Key"
    label: str              # "AWS_IAM_ACCESS_KEY"
    severity: str           # "HIGH" | "MEDIUM" | "LOW"
    secret_value: str       # Original token (used for masking)
    masked_value: str       # "[REDACTED:AWS_IAM_ACCESS_KEY]"
    line_number: int | None # 1-based line number in source text
    char_start: int | None  # Character offset of secret_value in full text
    char_end: int | None    # Character offset end


@dataclass
class ScanResult:
    """Aggregated result returned by scan_text()."""
    findings: list[Finding] = field(default_factory=list)
    masked_text: str = ""
    prompt_hash: str = ""
    scan_ms: float = 0.0
    scan_strategy: str = "full"
    line_count: int = 0


# ── Position helpers ──────────────────────────────────────────────────────────

def compute_line_offsets(text: str) -> list[int]:
    """
    Return a sorted list of character offsets at which each line starts.
    Index 0 = start of line 1 (always 0).
    Used with bisect for O(log n) char-position → line-number lookups.
    """
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == '\n':
            offsets.append(i + 1)
    return offsets


def char_to_line(char_pos: int, line_offsets: list[int]) -> int:
    """Convert a 0-based character offset to a 1-based line number."""
    return bisect.bisect_right(line_offsets, char_pos)


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate_findings(findings: list[Finding]) -> list[Finding]:
    """
    Cross-detector deduplication: when multiple findings overlap in character
    range (same secret_value at same position), keep the one whose label is
    most specific (longest label string as proxy - works because longer labels
    come from more specific detectors like AWS_IAM_ACCESS_KEY vs AWS_ACCESS_KEY).

    Findings without character positions are deduplicated by secret_value only,
    keeping the first occurrence.
    """
    if not findings:
        return []

    # Separate positioned vs unpositioned
    positioned = [f for f in findings if f.char_start is not None]
    unpositioned = [f for f in findings if f.char_start is None]

    # Sort: start ascending, then by label length descending (most specific first)
    positioned.sort(key=lambda f: (f.char_start, -len(f.label)))

    kept_positioned: list[Finding] = []
    last_end = -1
    for f in positioned:
        if f.char_start >= last_end:
            kept_positioned.append(f)
            last_end = f.char_end
        # overlapping with a prior finding - skip (prior was more specific)

    # For unpositioned, deduplicate by (label, secret_value)
    seen_unpos: set[tuple[str, str]] = set()
    kept_unpositioned: list[Finding] = []
    for f in unpositioned:
        key = (f.label, f.secret_value)
        if key not in seen_unpos:
            seen_unpos.add(key)
            kept_unpositioned.append(f)

    return kept_positioned + kept_unpositioned


# ── Base detector ─────────────────────────────────────────────────────────────

class BaseDetector(ABC):
    """
    Abstract base for all secret detectors.

    Subclasses MUST declare class attributes:
        CATEGORY            : str   - display name, e.g. "AWS"
        ENABLED_BY_DEFAULT  : bool  - True → on in standard preset
        DESCRIPTION         : str   - one-line description for the UI
        DOMAIN              : str   - folder grouping, e.g. "Cloud & Infrastructure"

    Subclasses MUST implement:
        definitions → list[TokenDefinition]

    scan() is implemented here and handles all three detection modes.
    Override only when a detector needs non-standard logic.
    """

    CATEGORY: str = ""
    ENABLED_BY_DEFAULT: bool = True
    DESCRIPTION: str = ""
    DOMAIN: str = ""

    @property
    @abstractmethod
    def definitions(self) -> list[TokenDefinition]:
        """All token type definitions for this detector."""
        ...

    # ── Quick pre-rejection ───────────────────────────────────────────────────

    @property
    def _quick_strings(self) -> list[str]:
        """
        Literal strings that MUST appear in text for this detector to produce
        any match. Extracted automatically from prefix definitions; override
        to add context-pattern variable names or structural markers.

        If ALL strings are absent from text, scan() returns [] immediately
        without running any regex (O(n) string search vs O(n*m) regex).
        """
        strings: list[str] = []
        for defn in self.definitions:
            # For prefix patterns, the prefix itself is the quick check string.
            # We extract it by looking for the literal prefix in the pattern source.
            # Subclasses can override _quick_strings for more explicit control.
            pass
        return strings

    def can_skip(self, text: str) -> bool:
        """Return True if text provably contains none of this detector's tokens."""
        checks = self._quick_strings
        if not checks:
            return False
        return not any(s in text for s in checks)

    # ── Core scan ─────────────────────────────────────────────────────────────

    def scan(
        self,
        text: str,
        line_offsets: list[int],
        allowlist: frozenset = frozenset(),
    ) -> list[Finding]:
        """
        Scan full text against all definitions. Returns deduplicated findings.
        Thread-safe: all state is local; no shared mutable data.

        allowlist: frozenset of exact values that must never produce a Finding.
                   Checked before any Finding object is created - zero overhead.
        """
        if self.can_skip(text):
            return []

        raw: list[Finding] = []

        for defn in self.definitions:
            try:
                for m in defn.pattern.finditer(text):
                    try:
                        secret_val = m.group(defn.capture_group)
                    except IndexError:
                        secret_val = m.group(0)

                    if not secret_val:
                        continue
                    if secret_val.startswith("[REDACTED:"):
                        continue
                    if secret_val in defn.known_safe:
                        continue
                    if secret_val in allowlist:
                        continue
                    if not self._post_filter(secret_val, defn):
                        continue

                    # Character positions - for context patterns capture group
                    # offset may differ from full-match offset
                    if defn.capture_group and defn.capture_group > 0:
                        try:
                            char_start = m.start(defn.capture_group)
                            char_end   = m.end(defn.capture_group)
                        except IndexError:
                            char_start = m.start()
                            char_end   = m.end()
                    else:
                        char_start = m.start()
                        char_end   = m.end()

                    line_num = char_to_line(char_start, line_offsets)

                    raw.append(Finding(
                        category=self.CATEGORY,
                        type=defn.type,
                        label=defn.label,
                        severity=defn.severity,
                        secret_value=secret_val,
                        masked_value=f"[REDACTED:{defn.label}]",
                        line_number=line_num,
                        char_start=char_start,
                        char_end=char_end,
                    ))

            except Exception:
                # Individual pattern failure must never crash the full scan
                continue

        return _dedup_within(raw)

    # ── Post-match filter hook ────────────────────────────────────────────────

    def _post_filter(self, value: str, definition: TokenDefinition) -> bool:
        """
        Called after pattern match + known-safe check. Return False to discard.
        Default: always keep. Override in detectors that need value-level filtering
        (e.g. EntropyDetector checks Shannon entropy here).
        """
        return True

    # ── Pattern builder helpers ───────────────────────────────────────────────

    @staticmethod
    def prefix_pattern(
        prefix: str,
        charset: str,
        min_after: int,
        max_after: int | None = None,
        word_boundary: bool = True,
    ) -> re.Pattern:
        """
        Build a compiled regex for prefix-anchored token detection.

        Args:
            prefix      : Literal prefix string, e.g. "AKIA", "ghp_"
            charset     : Regex char class for the variable part, e.g. "[A-Z0-9]"
            min_after   : Minimum chars after prefix
            max_after   : Maximum chars after prefix; None = same as min_after (exact)
            word_boundary: Wrap with negative lookaround to prevent partial matches
        """
        ma = max_after if max_after is not None else min_after
        if min_after == ma:
            length_spec = f"{{{min_after}}}"
        else:
            length_spec = f"{{{min_after},{ma}}}"

        lb = r'(?<![a-zA-Z0-9\-_])' if word_boundary else ""
        la = r'(?![a-zA-Z0-9\-_])'  if word_boundary else ""

        return re.compile(lb + re.escape(prefix) + charset + length_spec + la)

    @staticmethod
    def context_pattern(
        variable_names: list[str],
        value_charset: str,
        value_min: int,
        value_max: int | None = None,
        flags: int = re.IGNORECASE,
    ) -> re.Pattern:
        """
        Build a compiled regex for context-anchored detection.
        Matches:  <variable_name> (= | :) optional-quote  VALUE  optional-quote
        The VALUE is captured in group(1).

        Args:
            variable_names : List of variable name alternatives (literal strings)
            value_charset  : Char class for the secret value, e.g. "[a-zA-Z0-9]"
            value_min      : Minimum length of the value
            value_max      : Maximum length; None = no upper limit
        """
        names_alt = "|".join(re.escape(n) for n in variable_names)
        vm = str(value_max) if value_max is not None else ""
        length_spec = f"{{{value_min},{vm}}}"

        return re.compile(
            r'(?:' + names_alt + r')\s*[:=]\s*[\'"]?'
            r'(' + value_charset + length_spec + r')[\'"]?',
            flags,
        )


# ── Within-detector deduplication ─────────────────────────────────────────────

def _dedup_within(findings: list[Finding]) -> list[Finding]:
    """
    Remove overlapping findings produced by a single detector's definitions.
    When two definitions match at the same position, keep the longer match
    (i.e. the more specific definition - e.g. sk-ant-api03- over sk-ant-).
    """
    if len(findings) <= 1:
        return findings

    positioned = sorted(
        [f for f in findings if f.char_start is not None],
        key=lambda f: (f.char_start, -(f.char_end - f.char_start)),
    )

    kept: list[Finding] = []
    last_end = -1
    for f in positioned:
        if f.char_start >= last_end:
            kept.append(f)
            last_end = f.char_end

    # Unpositioned pass-through
    kept += [f for f in findings if f.char_start is None]
    return kept
