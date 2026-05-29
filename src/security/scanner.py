"""
Security scanner — detect-secrets + custom regex.

Three scan strategies based on prompt size:
  full     : sequential, every line          (≤ 500 lines)
  chunked  : parallel chunked                (501–5000 lines)
  filtered : pre-filter then chunked         (> 5000 lines)

Custom regex always runs on the full text regardless of strategy.
"""

import hashlib
import re
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.logging import get_logger
from src.security.config import ScanConfig

logger = get_logger(__name__)

# ── Size thresholds ──────────────────────────────────────────────────────────
MAX_LINES_FULL = 500
MAX_LINES_CHUNK = 5000
CHUNK_SIZE = 100
MAX_WORKERS = 4
MAX_LINE_LENGTH = 2000   # skip minified / bundled lines

# ── Pre-filter — only lines with these patterns can contain a known secret ──
_QUICK_FILTER = re.compile(
    r'[A-Z0-9]{16,}'
    r'|sk[-_][a-zA-Z0-9]{20,}'
    r'|ghp_[a-zA-Z0-9]+'
    r'|glpat-[a-zA-Z0-9]+'
    r'|eyJ[a-zA-Z0-9]+'
    r'|-----BEGIN'
    r'|[a-zA-Z0-9+/]{40,}={0,2}'
    r'|(?:password|passwd|pwd|secret|api.?key|auth.?token)\s*[:=]'
    r'|[a-z]+://[^:]+:[^@]+@'
    r'|xox[baprs]-'
    r'|SG\.[a-zA-Z0-9]'
    r'|AKIA[0-9A-Z]'
    r'|rzp_(live|test)_'
    r'|hf_[a-zA-Z0-9]'
    r'|r8_[a-zA-Z0-9]'
    r'|dop_v1_'
    r'|gsk_[a-zA-Z0-9]'
    r'|pk\.eyJ1'
    r'|AIza[0-9A-Za-z]'
    r'|sk-ant-'
    r'|pypi-'
    r'|npm_'
)

# ── detect-secrets built-in detector names (all 28) ─────────────────────────
# IpPublicDetector is listed separately — it is off by default in profiles
# because public IPs in code rarely indicate a secret.
_ALL_DS_DETECTORS = [
    "AWSKeyDetector",
    "ArtifactoryDetector",
    "AzureStorageKeyDetector",
    "BasicAuthDetector",
    "CloudantDetector",
    "DiscordBotTokenDetector",
    "GitHubTokenDetector",
    "GitLabTokenDetector",
    "IbmCloudIamDetector",
    "IbmCosHmacDetector",
    "JwtTokenDetector",
    "KeywordDetector",
    "MailchimpDetector",
    "NpmDetector",
    "OpenAIDetector",
    "PrivateKeyDetector",
    "PypiTokenDetector",
    "SendGridDetector",
    "SlackDetector",
    "SoftlayerDetector",
    "SquareOAuthDetector",
    "StripeDetector",
    "TelegramBotTokenDetector",
    "TwilioKeyDetector",
    # IpPublicDetector intentionally excluded from auto-enable — add via config
]

# ── Custom regex patterns ─────────────────────────────────────────────────────
# Covers: PII (email, phone), DB connections, AI/dev platform keys not in
# detect-secrets, and generic credential patterns.
# Maps label → (pattern, severity)
_CUSTOM_PATTERNS: dict[str, tuple[str, str]] = {

    # ── PII ───────────────────────────────────────────────────────────────────
    "EMAIL_ADDRESS": (
        r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b',
        "MEDIUM",
    ),
    "PHONE_NUMBER": (
        r'\b(\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b',
        "MEDIUM",
    ),

    # ── Database / broker connection strings ──────────────────────────────────
    # Covers embedded-credential URIs across all common protocols.
    "DB_CONNECTION": (
        r'(?:mongodb(?:\+srv)?|postgresql|postgres|mysql|mariadb|redis(?:s)?|'
        r'cassandra|oracle|mssql|sqlserver|elasticsearch|rabbitmq|amqp(?:s)?|'
        r'smtp(?:s)?|ftp(?:s)?|sftp|ldap(?:s)?|clickhouse|couchdb|neo4j(?:\+s)?|'
        r'influxdb)://[^:@\s]+:[^@\s]+@[^\s,\'")\]]+',
        "HIGH",
    ),
    # JDBC connection strings (Java ecosystem)
    "JDBC_CONNECTION": (
        r'jdbc:(?:mysql|postgresql|oracle|sqlserver|db2|h2|mariadb)://[^\s;]+;'
        r'(?:user|username)=[^;\s]+;password=[^;\s]+',
        "HIGH",
    ),

    # ── AI / LLM platform keys ────────────────────────────────────────────────
    # OpenAI is now covered by the native OpenAIDetector — removed from here.
    "ANTHROPIC_KEY": (
        r'\bsk-ant-[a-zA-Z0-9\-]{90,}\b',
        "HIGH",
    ),
    "GROQ_API_KEY": (
        r'\bgsk_[a-zA-Z0-9_]{52}\b',
        "HIGH",
    ),
    "HUGGING_FACE_TOKEN": (
        r'\bhf_[a-zA-Z0-9]{34,}\b',
        "HIGH",
    ),
    "REPLICATE_TOKEN": (
        r'\br8_[a-zA-Z0-9]{38,}\b',
        "HIGH",
    ),
    "COHERE_API_KEY": (
        # Cohere keys are 40-char alphanumeric. Require context to reduce FP.
        r'(?i)(?:cohere[_\-.]?(?:api[_\-.]?)?key|co[_\-.]api[_\-.]key)\s*[:=]\s*[\'"]?([a-zA-Z0-9]{40})[\'"]?',
        "HIGH",
    ),
    "MISTRAL_API_KEY": (
        r'(?i)(?:mistral[_\-.]?(?:api[_\-.]?)?key)\s*[:=]\s*[\'"]?([a-zA-Z0-9]{32,})[\'"]?',
        "HIGH",
    ),

    # ── Cloud / infrastructure keys ───────────────────────────────────────────
    "GCP_API_KEY": (
        r'\bAIza[0-9A-Za-z\-_]{35}\b',
        "HIGH",
    ),
    "FIREBASE_URL": (
        r'https://[a-z0-9\-]+\.firebaseio\.com',
        "MEDIUM",
    ),
    "DIGITAL_OCEAN_TOKEN": (
        r'\bdop_v1_[a-zA-Z0-9]{64}\b',
        "HIGH",
    ),
    "CLOUDFLARE_API_TOKEN": (
        # Cloudflare tokens: 40-char. Require variable context.
        r'(?i)(?:cloudflare[_\-.]?(?:api[_\-.]?)?token|cf[_\-.]api[_\-.]token)\s*[:=]\s*[\'"]?([a-zA-Z0-9_\-]{40})[\'"]?',
        "HIGH",
    ),

    # ── Mapping / geo keys ────────────────────────────────────────────────────
    "MAPBOX_TOKEN": (
        r'\bpk\.eyJ1[a-zA-Z0-9\-_\.]{20,}\b',
        "HIGH",
    ),

    # ── Payment gateways ─────────────────────────────────────────────────────
    "RAZORPAY_KEY": (
        r'\brzp_(live|test)_[a-zA-Z0-9]{20,}\b',
        "HIGH",
    ),
    "PAYU_KEY": (
        # PayU merchant keys are alphanumeric, 6 chars
        r'(?i)(?:payu[_\-.]?(?:merchant[_\-.]?)?key)\s*[:=]\s*[\'"]?([a-zA-Z0-9]{6})[\'"]?',
        "HIGH",
    ),

    # ── Error tracking ────────────────────────────────────────────────────────
    "SENTRY_DSN": (
        r'https://[a-f0-9]{32}@[a-z0-9]+(?:\.ingest(?:[a-z0-9\-]+)?)?\.sentry\.io/[0-9]+',
        "HIGH",
    ),

    # ── PyPI tokens (also covered by PypiTokenDetector natively) ─────────────
    "PYPI_TOKEN": (
        r'\bpypi-[a-zA-Z0-9\-_]{100,}\b',
        "HIGH",
    ),

    # ── Generic credential assignments ────────────────────────────────────────
    "INLINE_PASSWORD": (
        r'(?i)(?:password|passwd|pwd|pass)\s*[:=]\s*[\'"]?([^\s\'"]{8,})[\'"]?',
        "HIGH",
    ),
    "BEARER_TOKEN": (
        r'(?i)bearer\s+[a-zA-Z0-9\-_\.]{20,}',
        "HIGH",
    ),
}

# PII label → config key in ScanConfig.pii
# SSN and credit_card removed — not relevant to this use case.
_PII_CONFIG_KEY: dict[str, str] = {
    "EMAIL_ADDRESS": "email",
    "PHONE_NUMBER":  "phone",
}


@dataclass
class Finding:
    detector: str
    detector_src: str    # 'detect-secrets' | 'custom'
    severity: str
    secret_value: str    # original secret text (for masking)
    masked_value: str    # [REDACTED:LABEL]
    line_number: Optional[int] = None


@dataclass
class ScanResult:
    findings: list[Finding] = field(default_factory=list)
    masked_text: str = ""
    prompt_hash: str = ""
    scan_ms: int = 0
    scan_strategy: str = "full"
    line_count: int = 0


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _normalize_label(raw: str) -> str:
    """'AWS Access Key' → 'AWS_ACCESS_KEY'"""
    return raw.upper().replace(" ", "_").replace("-", "_")


def _build_ds_config(cfg: ScanConfig) -> dict:
    """
    Build detect-secrets plugin config from ScanConfig.
    All 24 named detectors (excluding IpPublicDetector) are on by default.
    IpPublicDetector can be enabled via cfg.detectors['IpPublicDetector'] = True.
    """
    plugins = []

    for name in _ALL_DS_DETECTORS:
        if not cfg.detectors or cfg.detectors.get(name, True):
            plugins.append({"name": name})

    # IpPublicDetector — off by default, opt-in only
    if cfg.detectors and cfg.detectors.get("IpPublicDetector", False):
        plugins.append({"name": "IpPublicDetector"})

    if cfg.entropy.get("enabled", True):
        plugins.append({
            "name": "HexHighEntropyString",
            "limit": float(cfg.entropy.get("hex_limit", 3.0)),
        })
        plugins.append({
            "name": "Base64HighEntropyString",
            "limit": float(cfg.entropy.get("base64_limit", 4.5)),
        })

    return {"plugins_used": plugins}


def _scan_lines_batch(lines: list[tuple[int, str]], ds_config: dict) -> list[Finding]:
    """Scan a batch of (line_number, text) pairs with detect-secrets."""
    findings: list[Finding] = []
    try:
        from detect_secrets.core.scan import scan_line
        from detect_secrets.settings import transient_settings

        with transient_settings(ds_config):
            for lineno, line in lines:
                stripped = line.strip()
                if not stripped or len(line) > MAX_LINE_LENGTH:
                    continue
                try:
                    for secret in scan_line(line):
                        label     = _normalize_label(secret.type)
                        secret_val = secret.secret_value or ""

                        # ── Entropy post-filter ─────────────────────────────
                        # detect-secrets entropy scans every word-level token
                        # independently and flags common English words at any
                        # practical threshold. Apply two extra guards here so
                        # we benefit from detect-secrets' entropy algorithm
                        # without the false positives:
                        #   1. Minimum 20 chars — English words are rarely this long
                        #   2. Must contain BOTH digits AND letters — real API keys
                        #      have this mix; natural-language words are pure alpha
                        if label in _DS_ENTROPY_LABELS:
                            if len(secret_val) < _ENTROPY_MIN_LEN:
                                continue
                            has_digit = any(c.isdigit() for c in secret_val)
                            has_alpha = any(c.isalpha() for c in secret_val)
                            if not (has_digit and has_alpha):
                                continue

                        tag = f"[REDACTED:{label}]"
                        findings.append(Finding(
                            detector=label,
                            detector_src="detect-secrets",
                            severity="HIGH",
                            secret_value=secret_val,
                            masked_value=tag,
                            line_number=lineno,
                        ))
                except Exception as line_err:
                    logger.debug(f"Line {lineno} scan error: {line_err}")

    except ImportError:
        logger.warning("detect-secrets not importable — custom regex only")
    except Exception as e:
        logger.warning(f"detect-secrets batch failed: {e}")

    return findings


_PATTERN_TIMEOUT_SECS = 3.0  # per-pattern execution budget


def _run_pattern_with_timeout(
    pattern: str,
    text: str,
    timeout: float = _PATTERN_TIMEOUT_SECS,
) -> list[re.Match]:
    """
    Run re.finditer with a hard timeout to protect against catastrophic
    backtracking. Returns matches collected before the timeout expires.
    A valid regex that has exponential worst-case (e.g. (a+)+b) would
    otherwise hang indefinitely on adversarial or large input.
    """
    results: list[re.Match] = []

    def _collect() -> list[re.Match]:
        return list(re.finditer(pattern, text))

    with ThreadPoolExecutor(max_workers=1) as ex:
        future: Future = ex.submit(_collect)
        try:
            return future.result(timeout=timeout)
        except TimeoutError:
            logger.warning(
                f"Custom pattern timed out after {timeout}s — skipped. "
                "Consider simplifying the regex to avoid catastrophic backtracking."
            )
            return []


# Entropy labels produced by detect-secrets — these need extra filtering
_DS_ENTROPY_LABELS = frozenset({"HEX_HIGH_ENTROPY_STRING", "BASE64_HIGH_ENTROPY_STRING"})

# Minimum length for entropy findings to be considered real secrets.
# Real API keys are almost always ≥ 20 chars; English words almost never reach that.
_ENTROPY_MIN_LEN = 20


def _scan_custom(text: str, cfg: ScanConfig) -> list[Finding]:
    """Scan full text with custom regex patterns + user keyword blocklist."""
    findings: list[Finding] = []

    # Merge built-in custom patterns with user-defined ones.
    # User patterns with the same label override built-in ones.
    active: dict[str, tuple[str, str]] = dict(_CUSTOM_PATTERNS)
    for p in cfg.custom_patterns:
        if isinstance(p, dict) and "pattern" in p and "name" in p:
            label = _normalize_label(p["name"])
            severity = str(p.get("severity", "HIGH")).upper()
            active[label] = (p["pattern"], severity)

    for label, (pattern, severity) in active.items():
        # Allow user to disable any built-in custom pattern via the detectors: section.
        # Label names match the keys in _CUSTOM_PATTERNS (e.g. DB_CONNECTION, ANTHROPIC_KEY).
        # Default is enabled (True) when not mentioned in config — same behaviour as before.
        if cfg.detectors and label in cfg.detectors:
            if not cfg.detectors.get(label, True):
                continue

        # Skip PII types disabled by user config
        if label in _PII_CONFIG_KEY:
            config_key = _PII_CONFIG_KEY[label]
            if not cfg.pii.get(config_key, True):
                continue

        # Built-in patterns are trusted — run directly without timeout overhead.
        # User-defined patterns (from cfg.custom_patterns) use timeout protection
        # to guard against catastrophically-backtracking user-written regex.
        is_user_pattern = any(
            isinstance(p2, dict) and _normalize_label(p2.get("name", "")) == label
            for p2 in cfg.custom_patterns
        )

        try:
            matches = (
                _run_pattern_with_timeout(pattern, text)
                if is_user_pattern
                else list(re.finditer(pattern, text))
            )
            for m in matches:
                secret_val = m.group()
                if secret_val.startswith("[REDACTED:"):
                    continue
                tag = f"[REDACTED:{label}]"
                findings.append(Finding(
                    detector=label,
                    detector_src="custom",
                    severity=severity,
                    secret_value=secret_val,
                    masked_value=tag,
                    line_number=None,
                ))
        except re.error:
            pass

    # Keyword blocklist
    for keyword in cfg.keyword_blocklist:
        if not keyword:
            continue
        if keyword.lower() in text.lower():
            label = f"KEYWORD_{_normalize_label(keyword)}"
            findings.append(Finding(
                detector=label,
                detector_src="custom",
                severity="MEDIUM",
                secret_value=keyword,
                masked_value=f"[REDACTED:{label}]",
                line_number=None,
            ))

    return findings


def _is_worth_scanning(line: str) -> bool:
    return bool(_QUICK_FILTER.search(line))


def _full_scan(lines: list[tuple[int, str]], ds_config: dict) -> list[Finding]:
    return _scan_lines_batch(lines, ds_config)


def _chunked_scan(lines: list[tuple[int, str]], ds_config: dict) -> list[Finding]:
    chunks = [lines[i:i + CHUNK_SIZE] for i in range(0, len(lines), CHUNK_SIZE)]
    findings: list[Finding] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(_scan_lines_batch, chunk, ds_config): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            try:
                findings.extend(future.result())
            except Exception as e:
                logger.warning(f"Chunk scan thread failed: {e}")
    return findings


def _filtered_scan(lines: list[tuple[int, str]], ds_config: dict) -> list[Finding]:
    high_risk = [(n, l) for n, l in lines if _is_worth_scanning(l)]
    logger.debug(f"Oversized: {len(lines)} lines, {len(high_risk)} passed pre-filter")
    return _chunked_scan(high_risk, ds_config)


def _deduplicate_entropy(findings: list[Finding]) -> list[Finding]:
    """
    Suppress entropy findings (BASE64 / HEX high-entropy) that are redundant
    because a specific or custom detector already caught the same secret.

    Two-pass strategy:

    Pass 1 — line-based (handles detect-secrets specific detectors):
      If line N has at least one non-entropy finding, drop all entropy findings
      on line N. If a line has ONLY entropy findings, keep them.
      Custom regex findings (line_number=None) are never entropy and pass through.

    Pass 2 — value-based (handles custom regex detectors):
      Custom regex findings have no line number so Pass 1 cannot group them
      with entropy findings. After Pass 1, build a set of secret_values already
      covered by any non-entropy finding. Drop any remaining entropy finding
      whose secret_value is in that set.
    """
    # ── Pass 1: line-based ────────────────────────────────────────────────────
    by_line: dict = {}
    for f in findings:
        by_line.setdefault(f.line_number, []).append(f)

    after_pass1: list[Finding] = []
    for line_no, group in by_line.items():
        if line_no is None:
            after_pass1.extend(group)
            continue
        has_specific = any(f.detector not in _DS_ENTROPY_LABELS for f in group)
        if has_specific:
            after_pass1.extend(f for f in group if f.detector not in _DS_ENTROPY_LABELS)
        else:
            after_pass1.extend(group)

    # ── Pass 2: value-based (custom regex vs entropy) ─────────────────────────
    # Entropy detectors often extract just the high-entropy token portion of a
    # key (e.g. "ABC12345678901234567") while the custom regex captures the full
    # match including a prefix (e.g. "rzp_live_ABC12345678901234567"). Exact
    # equality misses this, so we use substring containment both ways.
    covered_values: list[str] = [
        f.secret_value
        for f in after_pass1
        if f.detector not in _DS_ENTROPY_LABELS and f.secret_value
    ]

    def _already_covered(entropy_val: str) -> bool:
        for cv in covered_values:
            if entropy_val in cv or cv in entropy_val:
                return True
        return False

    return [
        f for f in after_pass1
        if f.detector not in _DS_ENTROPY_LABELS
        or not _already_covered(f.secret_value)
    ]


def scan_text(text: str, cfg: ScanConfig) -> ScanResult:
    """
    Master scan function — picks strategy based on line count, runs both
    detect-secrets and custom regex, and returns ScanResult.
    """
    if not text:
        return ScanResult(prompt_hash=_sha256(""), scan_strategy="full")

    start = time.monotonic()
    prompt_hash = _sha256(text)
    lines_raw = text.splitlines()
    numbered = list(enumerate(lines_raw, start=1))
    count = len(numbered)

    ds_config = _build_ds_config(cfg)

    if count <= MAX_LINES_FULL:
        strategy = "full"
        ds_findings = _full_scan(numbered, ds_config)
    elif count <= MAX_LINES_CHUNK:
        strategy = "chunked"
        ds_findings = _chunked_scan(numbered, ds_config)
    else:
        strategy = "filtered"
        ds_findings = _filtered_scan(numbered, ds_config)

    custom_findings = _scan_custom(text, cfg)

    # Merge both layers, then suppress entropy findings on any line where a
    # specific named detector already fired (entropy is redundant there).
    merged: list[Finding] = _deduplicate_entropy(list(ds_findings) + list(custom_findings))

    elapsed_ms = int((time.monotonic() - start) * 1000)
    logger.debug(
        f"Scan complete: {len(merged)} findings, {count} lines, "
        f"{elapsed_ms}ms, strategy={strategy}"
    )

    return ScanResult(
        findings=merged,
        masked_text="",
        prompt_hash=prompt_hash,
        scan_ms=elapsed_ms,
        scan_strategy=strategy,
        line_count=count,
    )
