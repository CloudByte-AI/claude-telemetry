"""
Security scanner - plugin-based custom detector system.

No dependency on detect-secrets or any third-party scanning library.
All detectors live in src/security/detectors/ and self-register via
@register_detector.

Three scan strategies based on prompt size:
  full     : sequential, single-threaded          (≤ 500 lines)
  chunked  : detectors run in parallel threads    (501–5000 lines)
  filtered : quick-filter pre-pass, then parallel (> 5000 lines)

The strategy label is stored in ScanResult.scan_strategy for telemetry.
"""

import hashlib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Trigger all @register_detector decorators
import src.security.detectors  # noqa: F401

from src.common.logging import get_logger
from src.security.config import ScanConfig
from src.security.detectors.base import (
    Finding,
    ScanResult,
    compute_line_offsets,
    deduplicate_findings,
)
from src.security.registry import DetectorRegistry

logger = get_logger(__name__)

# ── Size thresholds ──────────────────────────────────────────────────────────
MAX_LINES_FULL  = 500
MAX_LINES_CHUNK = 5000
MAX_WORKERS     = 6
MAX_LINE_LENGTH = 2000  # skip minified / bundled lines

# ── Pre-filter - lines matching this are "high-risk" and retained in filtered mode ──
_QUICK_FILTER = re.compile(
    # Cloud / infra prefixes
    r'AKIA[0-9A-Z]'
    r'|ASIA[0-9A-Z]'
    r'|AIza[0-9A-Za-z]'
    r'|dop_v1_'
    r'|cfk_|cfut_|cfat_'
    r'|ya29\.'
    r'|GOCSPX-'
    # AI / ML prefixes
    r'|sk-proj-|sk-svcacct-|sk-admin-|sk-ant-|sk-None-'
    r'|sk-[a-zA-Z0-9]{20,}'
    r'|gsk_[a-zA-Z0-9]'
    r'|hf_[a-zA-Z0-9]'
    r'|r8_[a-zA-Z0-9]'
    r'|whsec_'
    # Developer tools
    r'|ghp_|gho_|ghu_|ghs_|ghr_|github_pat_'
    r'|glpat-|gldt-|glrt-|gloas-|glagent-'
    r'|npm_[a-zA-Z0-9]'
    r'|pypi-AgEIcHlwaS5vcmc'
    # Payment gateways
    r'|sk_live_|sk_test_|rk_live_|rk_test_|sk_org_'
    r'|rzp_live_|rzp_test_'
    # Communication
    r'|SK[0-9a-fA-F]{32}|AC[0-9a-fA-F]{32}'
    # Auth / JWT
    r'|eyJ[a-zA-Z0-9\-_]'
    r'|-----BEGIN'
    # Connection strings
    r'|(?:mongodb|postgresql|postgres|mysql|redis|mssql|amqp)://'
    r'|jdbc:[a-z]'
    # Generic credential assignments
    r'|(?:password|passwd|secret|api.?key|token|auth.?token)\s*[:=]'
    r'|[a-z]+://[^:]+:[^@]+@'
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _normalize_label(raw: str) -> str:
    return raw.upper().replace(" ", "_").replace("-", "_")


# ── Custom-pattern scanning (user-defined patterns from YAML config) ──────────
#
# User patterns run on RE2 (src/common/safe_regex.py), whose match time grows
# only with the text, whatever the pattern - so one bad pattern cannot stall a
# prompt. A pattern RE2 rejects never got this far (config._validate_patterns).
# The text is capped and the patterns share a time budget per scan: past it,
# the remaining patterns are skipped for this scan and logged, rather than
# holding the prompt.

MAX_CUSTOM_PATTERN_INPUT = 2_000_000   # characters
CUSTOM_PATTERN_BUDGET_SECS = 0.25
MAX_MATCHES_PER_PATTERN = 1000


def _scan_custom_patterns(
    text: str, cfg: ScanConfig, line_offsets: list[int], allowlist: frozenset
) -> list[Finding]:
    """Scan user-defined custom patterns and keyword blocklist."""
    from src.security.detectors.base import char_to_line

    from src.common import safe_regex

    findings: list[Finding] = []
    subject = text[:MAX_CUSTOM_PATTERN_INPUT]
    started = time.perf_counter()

    for index, p in enumerate(cfg.custom_patterns):
        if not isinstance(p, dict) or "pattern" not in p or "name" not in p:
            continue
        if time.perf_counter() - started > CUSTOM_PATTERN_BUDGET_SECS:
            skipped = len(cfg.custom_patterns) - index
            logger.warning(
                f"Custom patterns over their {CUSTOM_PATTERN_BUDGET_SECS}s budget - "
                f"{skipped} pattern(s) skipped for this scan"
            )
            break
        label    = _normalize_label(p["name"])
        severity = str(p.get("severity", "HIGH")).upper()
        try:
            compiled = safe_regex.compile_capturing(p["pattern"])
        except safe_regex.RegexError as exc:
            logger.warning(f"Custom pattern '{p['name']}' cannot be used - skipped: {exc}")
            continue
        for char_start, char_end, secret_val in compiled.spans(subject, MAX_MATCHES_PER_PATTERN):
            if not secret_val or secret_val.startswith("[REDACTED:"):
                continue
            if secret_val in allowlist:
                continue
            findings.append(Finding(
                category=p.get("category") or "Custom",
                type=p["name"],
                label=label,
                severity=severity,
                secret_value=secret_val,
                masked_value=f"[REDACTED:{label}]",
                line_number=char_to_line(char_start, line_offsets),
                char_start=char_start,
                char_end=char_end,
                custom=True,
            ))

    for keyword in cfg.keyword_blocklist:
        if not keyword:
            continue
        if keyword in allowlist:
            continue
        if keyword.lower() in text.lower():
            label = f"KEYWORD_{_normalize_label(keyword)}"
            findings.append(Finding(
                category="Custom",
                type="Keyword",
                label=label,
                severity="MEDIUM",
                secret_value=keyword,
                masked_value=f"[REDACTED:{label}]",
                line_number=None,
                char_start=None,
                char_end=None,
                custom=True,
            ))

    return findings


# ── Core scan dispatch ────────────────────────────────────────────────────────

def _run_detector_safe(
    detector,
    text: str,
    line_offsets: list[int],
    allowlist: frozenset,
) -> list[Finding]:
    """Run one detector, swallowing any unexpected exceptions."""
    try:
        return detector.scan(text, line_offsets, allowlist=allowlist)
    except Exception as exc:
        logger.warning(f"Detector {detector.CATEGORY} raised: {exc}")
        return []


def _scan_sequential(
    text: str,
    detectors: list,
    line_offsets: list[int],
    allowlist: frozenset,
) -> list[Finding]:
    results: list[Finding] = []
    for det in detectors:
        results.extend(_run_detector_safe(det, text, line_offsets, allowlist))
    return results


def _scan_parallel(
    text: str,
    detectors: list,
    line_offsets: list[int],
    allowlist: frozenset,
) -> list[Finding]:
    results: list[Finding] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(_run_detector_safe, det, text, line_offsets, allowlist): det.CATEGORY
            for det in detectors
        }
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                cat = futures.get(future, "?")
                logger.warning(f"Parallel scan thread [{cat}] failed: {exc}")
    return results


def _build_filtered_text(
    lines_raw: list[str],
) -> tuple[str, list[int]]:
    """
    Select high-risk lines (matching _QUICK_FILTER) + 3-line context window.
    Returns (compact_text, mapping: compact_line_index → original_1based_line_number).
    """
    high_risk: set[int] = set()
    for i, line in enumerate(lines_raw):
        if len(line) <= MAX_LINE_LENGTH and _QUICK_FILTER.search(line):
            for j in range(max(0, i - 3), min(len(lines_raw), i + 4)):
                high_risk.add(j)

    selected = sorted(high_risk)
    filtered_lines = [lines_raw[i] for i in selected]
    # Map compact-text line index (0-based) → original 1-based line number
    orig_linenos = [i + 1 for i in selected]

    compact_text = "\n".join(filtered_lines)
    # Build compact line offsets for char_to_line inside detectors
    compact_offsets = [0]
    for line in filtered_lines[:-1]:
        compact_offsets.append(compact_offsets[-1] + len(line) + 1)

    return compact_text, orig_linenos, compact_offsets


def _remap_line_numbers(findings: list[Finding], orig_linenos: list[int]) -> list[Finding]:
    """Translate compact-text line numbers back to original document line numbers."""
    for f in findings:
        if f.line_number is not None:
            idx = f.line_number - 1  # to 0-based
            if 0 <= idx < len(orig_linenos):
                f.line_number = orig_linenos[idx]
    return findings


# ── Which profile asked for a finding ────────────────────────────────────────

def _asks_for(source, finding: Finding) -> bool:
    """Whether one profile (config.ScanSource) asks for this finding."""
    # A custom pattern's finding carries its own category (or "Custom").
    if finding.custom or finding.category == "Custom":
        if finding.type == "Keyword":
            return str(finding.secret_value).lower() in source.keywords
        return finding.type in source.pattern_names
    return finding.category in source.categories


def _attribute(findings: list[Finding], sources: tuple) -> list[Finding]:
    """
    Keep a finding only when at least one profile both asks for it and does
    not allow its value; record those profiles on it. So a profile's allowlist
    silences only what that profile asks for - a project's allowlist can never
    silence what the organisation's profile asks for. With one profile this
    changes nothing: the detectors already skipped its allowlist.
    """
    if not sources:
        return findings
    kept: list[Finding] = []
    for finding in findings:
        asking = tuple(
            source.policy for source in sources
            if _asks_for(source, finding) and finding.secret_value not in source.allowlist
        )
        if asking:
            finding.sources = asking
            kept.append(finding)
    return kept


# ── Public API ────────────────────────────────────────────────────────────────

def scan_text(text: str, cfg: ScanConfig) -> ScanResult:
    """
    Master scan function.

    Picks strategy based on line count, runs all enabled detectors, applies
    cross-detector deduplication, appends custom-pattern and keyword findings,
    and returns a ScanResult.
    """
    if not text:
        return ScanResult(prompt_hash=_sha256(""), scan_strategy="full")

    start        = time.perf_counter()
    prompt_hash  = _sha256(text)
    lines_raw    = text.splitlines()
    count        = len(lines_raw)
    detectors    = DetectorRegistry.get_enabled(cfg.categories)
    # Build allowlist frozenset once - passed to every detector and custom-pattern scan.
    # A frozenset lookup is O(1); this is the only place cfg.allowlist is converted.
    allowlist_set = frozenset(v for v in cfg.allowlist if v)
    # Pre-compute line offsets once for the full text - used by custom patterns
    # and by full/chunked strategies. Filtered strategy uses compact_offsets instead.
    line_offsets = compute_line_offsets(text)

    if count <= MAX_LINES_FULL:
        strategy = "full"
        findings = _scan_sequential(text, detectors, line_offsets, allowlist_set)

    elif count <= MAX_LINES_CHUNK:
        strategy = "chunked"
        findings = _scan_parallel(text, detectors, line_offsets, allowlist_set)

    else:
        strategy = "filtered"
        compact_text, orig_linenos, compact_offsets = _build_filtered_text(lines_raw)
        logger.debug(
            f"Filtered: {count} lines → {len(orig_linenos)} high-risk lines retained"
        )
        if compact_text:
            raw = _scan_parallel(compact_text, detectors, compact_offsets, allowlist_set)
            findings = _remap_line_numbers(raw, orig_linenos)
        else:
            findings = []

    # Per-profile allowlists before deduplication, so an allowed match never
    # displaces an overlapping one that some profile still asks for.
    findings = _attribute(findings, cfg.sources)

    # Cross-detector deduplication (keeps most-specific match at each position)
    findings = deduplicate_findings(findings)

    # User-defined custom patterns and keyword blocklist run on full text always.
    custom = _scan_custom_patterns(text, cfg, line_offsets, allowlist_set)
    findings.extend(_attribute(custom, cfg.sources))

    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.debug(
        f"Scan complete: {len(findings)} findings, {count} lines, "
        f"{elapsed_ms}ms, strategy={strategy}, detectors={len(detectors)}"
    )

    return ScanResult(
        findings=findings,
        masked_text="",
        prompt_hash=prompt_hash,
        scan_ms=elapsed_ms,
        scan_strategy=strategy,
        line_count=count,
    )
