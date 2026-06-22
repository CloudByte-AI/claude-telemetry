"""
Security scanning configuration loader.

Reads ~/.cloudbyte/security_profile_v2.yaml on every invocation.
Falls back to standard preset if the file is missing or malformed.

Config format:
  enabled: true
  scope: prompt_only   # 'prompt_only' | 'both'
  plan: standard       # 'minimal' | 'standard' | 'strict'
  categories:
    AWS: true
    OpenAI: true
    Email Address: false   # each key is the CATEGORY of a registered detector
  custom_patterns: []
  keyword_blocklist: []

Per-scan-point overrides (optional):
  prompt:
    categories:
      Email Address: true
  response:
    categories:
      JWT: false
"""

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.logging import get_logger

logger = get_logger(__name__)

PROFILES_DIR    = Path(__file__).parent / "profiles"
USER_CONFIG_PATH = Path.home() / ".cloudbyte" / "security_profile_v2.yaml"


@dataclass
class ScanConfig:
    """
    Scan settings applied to one scan point (prompt or response).

    categories:       CATEGORY → enabled bool.
                      Missing keys fall back to the detector's ENABLED_BY_DEFAULT.
    custom_patterns:  list of [{name, pattern, severity}] dicts (user-defined extra regex).
    keyword_blocklist: list of literal strings to flag.
    allowlist:        exact secret values that should NEVER be flagged or block a prompt.
                      Checked before any Finding is created — zero detector overhead.
    """
    categories: dict = field(default_factory=dict)
    custom_patterns: list = field(default_factory=list)
    keyword_blocklist: list = field(default_factory=list)
    allowlist: list = field(default_factory=list)


@dataclass
class SecurityConfig:
    """Top-level security config loaded from security_profile_v2.yaml."""
    enabled: bool = False
    plan: str = "standard"
    scope: str = "both"
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


def _validate_patterns(patterns: list, label: str, cwd: str | None = None) -> list:
    """
    Validate and normalise custom patterns.

    Mode A — examples-based: system generates a regex automatically.
    Mode B — manual regex: compiled and used as-is.
    """
    valid = []
    for p in patterns:
        if not isinstance(p, dict):
            continue

        name = p.get("name", "?")

        # ── Mode A: examples-based pattern generation ──────────────────────
        if "examples" in p and "pattern" not in p:
            _MAX_EXAMPLES = 5
            examples = p.get("examples") or []
            if not examples:
                logger.warning(f"{label}: pattern '{name}' has empty examples list — skipped")
                continue
            if len(examples) > _MAX_EXAMPLES:
                logger.warning(
                    f"{label} [{name}]: {len(examples)} examples provided — "
                    f"only the first {_MAX_EXAMPLES} are used"
                )
                examples = examples[:_MAX_EXAMPLES]
            try:
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
    # Merge category overrides on top of defaults
    merged_categories = dict(defaults.categories)
    if isinstance(raw.get("categories"), dict):
        merged_categories.update(raw["categories"])
    return ScanConfig(
        categories=merged_categories,
        custom_patterns=_validate_patterns(patterns, "section", cwd=cwd),
        keyword_blocklist=raw.get("keyword_blocklist") or defaults.keyword_blocklist,
        allowlist=defaults.allowlist,  # allowlist is global — always inherited, no per-section override
    )


def _parse_config(raw: dict[str, Any], cwd: str | None = None) -> SecurityConfig:
    """Parse raw YAML dict into a SecurityConfig."""
    base = ScanConfig(
        categories=raw.get("categories") or {},
        custom_patterns=_validate_patterns(raw.get("custom_patterns") or [], "top-level", cwd=cwd),
        keyword_blocklist=raw.get("keyword_blocklist") or [],
        allowlist=[str(v) for v in (raw.get("allowlist") or []) if v],
    )

    prompt_raw   = raw.get("prompt")
    response_raw = raw.get("response")

    prompt_config   = _parse_scan_section(prompt_raw, base, cwd=cwd)   if isinstance(prompt_raw, dict)   else base
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

    Returns SecurityConfig(enabled=False) if the config file does not exist.
    Never raises.
    """
    if not USER_CONFIG_PATH.exists():
        return SecurityConfig(enabled=False)

    try:
        import yaml
        with open(USER_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        logger.warning(f"security_profile_v2.yaml unreadable — falling back to standard preset: {e}")
        raw = _load_preset("standard")

    if not isinstance(raw, dict):
        logger.warning("security_profile_v2.yaml is not a mapping — falling back to standard preset")
        raw = _load_preset("standard")

    return _parse_config(raw, cwd=cwd)
