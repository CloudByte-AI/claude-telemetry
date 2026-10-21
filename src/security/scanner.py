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

Anything skipped along the way (a detector that raised, a custom pattern that
timed out or failed, the partial filtered strategy) is recorded in
ScanResult.incomplete_reasons, and ScanResult.complete is then False.
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

from src.common import safe_regex
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
# The text is capped, each pattern reports at most MAX_MATCHES_PER_PATTERN hits,
# and all patterns share one time budget per scan, checked between hits. Past
# it, the pattern in progress keeps the hits it found (they still block) and the
# remaining patterns are skipped. Each of these is recorded in `issues`, so the
# result says the scan was incomplete instead of holding the prompt.

MAX_CUSTOM_PATTERN_INPUT = 2_000_000   # characters
MAX_MATCHES_PER_PATTERN = 1000
# Time budget shared by all custom patterns in one scan.
_PATTERN_TIMEOUT_SECS = 3.0


def _run_timed(
    pattern_str: str,
    text: str,
    hits: list[safe_regex.Hit] | None = None,
    deadline: float | None = None,
    limit: int | None = None,
) -> list[safe_regex.Hit] | None:
    """
    Find every occurrence of a custom pattern in text, on RE2.

    RE2 never backtracks, so one pattern cannot hang the scan however it is
    written (Python's `re` holds the GIL while it matches, so no timeout around
    it could fire). Each step is linear, which makes the clock reachable between
    hits: a pattern still hitting at the deadline is a match flood on a huge
    text, and is stopped.

    `deadline` is a time.monotonic() value, by default _PATTERN_TIMEOUT_SECS from
    now. Hits are appended to `hits` when given, so a caller keeps the ones found
    before a timeout. At most `limit` hits are collected.

    Returns None when the deadline passed, so the caller can tell a stopped
    pattern from one that found nothing. Raises safe_regex.RegexError for a
    pattern RE2 cannot compile.
    """
    finder = safe_regex.compile_finder(pattern_str)
    if deadline is None:
        deadline = time.monotonic() + _PATTERN_TIMEOUT_SECS
    found: list[safe_regex.Hit] = [] if hits is None else hits
    for hit in finder.finditer(text):
        found.append(hit)
        if limit is not None and len(found) >= limit:
            break
        if time.monotonic() >= deadline:
            logger.warning(
                f"Custom pattern still matching when the {_PATTERN_TIMEOUT_SECS}s budget ran out - "
                "stopped. It matches too much of the text; narrow the regex."
            )
            return None
    return found


def _scan_custom_patterns(
    text: str, cfg: ScanConfig, line_offsets: list[int], allowlist: frozenset, issues: list[str]
) -> list[Finding]:
    """Scan user-defined custom patterns and keyword blocklist."""
    from src.security.detectors.base import char_to_line

    findings: list[Finding] = []
    subject = text[:MAX_CUSTOM_PATTERN_INPUT]
    if len(text) > MAX_CUSTOM_PATTERN_INPUT:
        issues.append("custom_input_truncated")
    deadline = time.monotonic() + _PATTERN_TIMEOUT_SECS
    over_budget = 0

    for p in cfg.custom_patterns:
        if not isinstance(p, dict) or "pattern" not in p or "name" not in p:
            name = p.get("name", "?") if isinstance(p, dict) else "?"
            issues.append(f"pattern_skipped:{name}")
            continue
        if time.monotonic() >= deadline:
            over_budget += 1
            issues.append(f"pattern_timeout:{p['name']}")
            continue
        label    = _normalize_label(p["name"])
        severity = str(p.get("severity", "HIGH")).upper()
        hits: list[safe_regex.Hit] = []
        try:
            matches = _run_timed(
                p["pattern"], subject, hits=hits, deadline=deadline, limit=MAX_MATCHES_PER_PATTERN
            )
        except safe_regex.RegexError:
            issues.append(f"pattern_error:{p['name']}")
            continue
        if matches is None:
            issues.append(f"pattern_timeout:{p['name']}")
        elif len(hits) >= MAX_MATCHES_PER_PATTERN:
            issues.append(f"pattern_limit:{p['name']}")
        # Hits found before a timeout are kept, so what was found still blocks.
        for hit in hits:
            # A hit is group(1) when the pattern has one (context patterns),
            # else the full match; offsets are characters, not bytes.
            secret_val = hit.value
            char_start = hit.start
            char_end   = hit.end

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

    if over_budget:
        logger.warning(
            f"Custom patterns over their {_PATTERN_TIMEOUT_SECS}s budget - "
            f"{over_budget} pattern(s) skipped for this scan"
        )

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
    issues: list[str],
) -> list[Finding]:
    """Run one detector, swallowing any unexpected exceptions (recorded in issues)."""
    try:
        return detector.scan(text, line_offsets, allowlist=allowlist)
    except Exception as exc:
        logger.warning(f"Detector {detector.CATEGORY} raised: {exc}")
        issues.append(f"detector_error:{detector.CATEGORY}")
        return []


def _scan_sequential(
    text: str,
    detectors: list,
    line_offsets: list[int],
    allowlist: frozenset,
    issues: list[str],
) -> list[Finding]:
    results: list[Finding] = []
    for det in detectors:
        results.extend(_run_detector_safe(det, text, line_offsets, allowlist, issues))
    return results


def _scan_parallel(
    text: str,
    detectors: list,
    line_offsets: list[int],
    allowlist: frozenset,
    issues: list[str],
) -> list[Finding]:
    results: list[Finding] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {
            ex.submit(_run_detector_safe, det, text, line_offsets, allowlist, issues): det.CATEGORY
            for det in detectors
        }
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                cat = futures.get(future, "?")
                logger.warning(f"Parallel scan thread [{cat}] failed: {exc}")
                issues.append(f"detector_error:{cat}")
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
    and returns a ScanResult. ScanResult.complete is False when any of that
    was skipped; ScanResult.incomplete_reasons says what.
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
    # What was skipped. Appended from detector threads too; list.append is atomic.
    issues: list[str] = []

    if count <= MAX_LINES_FULL:
        strategy = "full"
        findings = _scan_sequential(text, detectors, line_offsets, allowlist_set, issues)

    elif count <= MAX_LINES_CHUNK:
        strategy = "chunked"
        findings = _scan_parallel(text, detectors, line_offsets, allowlist_set, issues)

    else:
        strategy = "filtered"
        # Detectors see only the high-risk lines, so the scan is partial by design.
        issues.append("filtered_strategy")
        compact_text, orig_linenos, compact_offsets = _build_filtered_text(lines_raw)
        logger.debug(
            f"Filtered: {count} lines → {len(orig_linenos)} high-risk lines retained"
        )
        if compact_text:
            raw = _scan_parallel(compact_text, detectors, compact_offsets, allowlist_set, issues)
            findings = _remap_line_numbers(raw, orig_linenos)
        else:
            findings = []

    # Per-profile allowlists before deduplication, so an allowed match never
    # displaces an overlapping one that some profile still asks for.
    findings = _attribute(findings, cfg.sources)

    # Cross-detector deduplication (keeps most-specific match at each position)
    findings = deduplicate_findings(findings)

    # User-defined custom patterns and keyword blocklist run on full text always.
    custom = _scan_custom_patterns(text, cfg, line_offsets, allowlist_set, issues)
    findings.extend(_attribute(custom, cfg.sources))
    incomplete_reasons = list(dict.fromkeys(issues))

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
        complete=not incomplete_reasons,
        incomplete_reasons=incomplete_reasons,
    )
