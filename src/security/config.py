"""
Security scanning configuration loader.

Reads ~/.cloudbyte/security_profile.yaml on every invocation.
Falls back to standard preset if the file is missing or malformed.

Config supports two formats:

  Flat (backward-compatible) — shared config for both scan points:
    enabled: true
    detectors: {AWSKeyDetector: true, ...}
    pii: {ssn: true}

  Per-scan-point — separate config for prompt and response:
    enabled: true
    detectors: {AWSKeyDetector: true}   # top-level defaults
    prompt:
      pii: {ssn: true, credit_card: true}
    response:
      entropy: {enabled: false}
      pii: {email: false}

When prompt:/response: sections are present they override top-level defaults
for that scan point only. Missing keys fall through to top-level values.
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.logging import get_logger

logger = get_logger(__name__)

PROFILES_DIR = Path(__file__).parent / "profiles"
USER_CONFIG_PATH = Path.home() / ".cloudbyte" / "security_profile.yaml"


@dataclass
class ScanConfig:
    """
    Scan-specific settings — applies to one scan point (prompt or response).
    Shared between both points unless the user defines per-point overrides.
    """
    # detector name → enabled bool (empty dict = all on)
    detectors: dict = field(default_factory=dict)
    # {enabled, hex_limit, base64_limit}
    entropy: dict = field(default_factory=dict)
    # pii type → enabled bool  (credit_card, ssn, email, phone)
    pii: dict = field(default_factory=dict)
    # [{name, pattern, severity}]
    custom_patterns: list = field(default_factory=list)
    keyword_blocklist: list = field(default_factory=list)


@dataclass
class SecurityConfig:
    """Top-level security config loaded from security_profile.yaml."""
    enabled: bool = False
    plan: str = "standard"
    # 'prompt_only' | 'both'
    scope: str = "both"
    # Per-scan-point configs (may be the same object if no overrides)
    prompt_config: ScanConfig = field(default_factory=ScanConfig)
    response_config: ScanConfig = field(default_factory=ScanConfig)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _load_preset(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Could not load preset '{name}': {e}")
        return {}


def _check_example_overlap(examples: list[str], name: str) -> list[str]:
    """
    Run examples through every detect-secrets built-in detector.
    Returns the list of detector types that already catch these examples,
    indicating the user may not need a custom pattern for this format.
    """
    overlapping: list[str] = []
    try:
        from detect_secrets.core.scan import scan_line
        from detect_secrets.settings import transient_settings

        all_ds_plugins = [
            {"name": n} for n in [
                "AWSKeyDetector", "ArtifactoryDetector", "AzureStorageKeyDetector",
                "BasicAuthDetector", "CloudantDetector", "DiscordBotTokenDetector",
                "GitHubTokenDetector", "GitLabTokenDetector", "IbmCloudIamDetector",
                "IbmCosHmacDetector", "JwtTokenDetector", "KeywordDetector",
                "MailchimpDetector", "NpmDetector", "OpenAIDetector", "PrivateKeyDetector",
                "PypiTokenDetector", "SendGridDetector", "SlackDetector", "SoftlayerDetector",
                "SquareOAuthDetector", "StripeDetector", "TelegramBotTokenDetector",
                "TwilioKeyDetector",
            ]
        ]
        with transient_settings({"plugins_used": all_ds_plugins}):
            for ex in examples:
                for secret in scan_line(ex):
                    det = secret.type
                    if det not in overlapping:
                        overlapping.append(det)
    except Exception:
        pass
    return overlapping


def _validate_patterns(patterns: list, label: str, cwd: str | None = None) -> list:
    """
    Validate and normalise custom patterns.

    Supports two modes:
      Mode A — examples list: system generates a regex pattern automatically.
      Mode B — manual regex: validated and used as-is.
    """
    valid = []
    for p in patterns:
        if not isinstance(p, dict):
            continue

        name = p.get("name", "?")

        # ── Mode A: examples-based pattern generation ──────────────────────
        if "examples" in p and "pattern" not in p:
            _MAX_EXAMPLES = 5  # 3–5 is the sweet spot; beyond this accuracy does not improve
            examples = p.get("examples") or []
            if not examples:
                logger.warning(f"{label}: pattern '{name}' has empty examples list — skipped")
                continue
            if len(examples) > _MAX_EXAMPLES:
                logger.warning(
                    f"{label} [{name}]: {len(examples)} examples provided — "
                    f"only the first {_MAX_EXAMPLES} are used "
                    f"(maximum is {_MAX_EXAMPLES}; accuracy does not improve beyond this)"
                )
                examples = examples[:_MAX_EXAMPLES]
            try:
                # Overlap check — warn if a built-in detector already covers this format
                overlapping = _check_example_overlap(examples, name)
                if overlapping:
                    overlap_names = ", ".join(overlapping[:3])
                    logger.warning(
                        f"{label} [{name}]: built-in detector(s) already catch this format "
                        f"({overlap_names}) — you may not need a custom pattern. "
                        "If your format is different, the custom pattern still adds coverage."
                    )

                from src.security.pattern_builder import analyze_examples
                generated = analyze_examples(
                    name=name,
                    examples=examples,
                    severity=p.get("severity", "HIGH"),
                    cwd=cwd,
                )
                for w in generated.warnings:
                    logger.warning(f"{label} [{name}]: {w}")
                codebase_note = ""
                if generated.codebase_fp_count >= 0:
                    codebase_note = (
                        f" | codebase={generated.codebase_fp_count} FP(s) "
                        f"in {generated.codebase_files_checked} files"
                    )
                logger.info(
                    f"{label} [{name}]: generated pattern — "
                    f"confidence={generated.confidence} "
                    f"fp_risk={generated.false_positive_risk} "
                    f"validated={generated.examples_matched}/{generated.examples_total}"
                    f"{codebase_note}"
                )
                # Log full summary at DEBUG so users can inspect alternatives
                for line in generated.summary().splitlines():
                    logger.debug(f"  {line}")
                valid.append(generated.to_scan_config_entry())
            except Exception as e:
                logger.warning(f"{label}: pattern builder failed for '{name}': {e} — skipped")
            continue

        # ── Mode B: manual regex ───────────────────────────────────────────
        if "pattern" not in p:
            logger.warning(f"{label}: pattern '{name}' has neither 'pattern' nor 'examples' — skipped")
            continue
        try:
            re.compile(p["pattern"])
            valid.append(p)
        except re.error:
            logger.warning(f"{label}: custom pattern '{name}' has invalid regex — skipped")

    return valid


def _parse_scan_section(raw: dict, defaults: ScanConfig, cwd: str | None = None) -> ScanConfig:
    """
    Parse a scan-point section (prompt: or response:), falling back to
    the top-level defaults for any key not present in the section.
    """
    patterns = raw.get("custom_patterns") or defaults.custom_patterns
    return ScanConfig(
        detectors=raw.get("detectors") or defaults.detectors,
        entropy=raw.get("entropy") or defaults.entropy,
        pii=raw.get("pii") or defaults.pii,
        custom_patterns=_validate_patterns(patterns, "section", cwd=cwd),
        keyword_blocklist=raw.get("keyword_blocklist") or defaults.keyword_blocklist,
    )


def _parse_config(raw: dict[str, Any], cwd: str | None = None) -> SecurityConfig:
    """Parse raw YAML dict into a SecurityConfig."""
    # Build top-level defaults (used for both scan points unless overridden)
    _default_entropy = {"enabled": True, "hex_limit": 3.0, "base64_limit": 4.5}
    base = ScanConfig(
        detectors=raw.get("detectors") or {},
        entropy=raw.get("entropy") or _default_entropy,
        pii=raw.get("pii") or {},
        custom_patterns=_validate_patterns(raw.get("custom_patterns") or [], "top-level", cwd=cwd),
        keyword_blocklist=raw.get("keyword_blocklist") or [],
    )

    # Per-scan-point overrides (optional)
    prompt_raw = raw.get("prompt")
    response_raw = raw.get("response")

    prompt_config = _parse_scan_section(prompt_raw, base, cwd=cwd) if isinstance(prompt_raw, dict) else base
    response_config = _parse_scan_section(response_raw, base, cwd=cwd) if isinstance(response_raw, dict) else base

    return SecurityConfig(
        enabled=bool(raw.get("enabled", False)),
        plan=str(raw.get("plan", "standard")),
        scope=str(raw.get("scope", "both")),
        prompt_config=prompt_config,
        response_config=response_config,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def load_security_config(cwd: str | None = None) -> SecurityConfig:
    """
    Load user security config from disk.

    Args:
        cwd: Project working directory. When provided and the config has
             examples-based custom patterns, the generated patterns are tested
             against real project files (cached for 24 hours per examples set).

    Returns SecurityConfig(enabled=False) if the config file does not exist
    (feature not yet set up by the user). Never raises.
    """
    if not USER_CONFIG_PATH.exists():
        return SecurityConfig(enabled=False)

    try:
        import yaml
        with open(USER_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"security_profile.yaml unreadable — falling back to standard preset: {e}")
        raw = _load_preset("standard")

    if not isinstance(raw, dict):
        logger.warning("security_profile.yaml is not a mapping — falling back to standard preset")
        raw = _load_preset("standard")

    return _parse_config(raw, cwd=cwd)
