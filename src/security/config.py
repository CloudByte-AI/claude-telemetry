"""
Security scanning configuration loader.

Reads ~/.cloudbyte/security/security_profile.yaml on every invocation.
Falls back to standard preset if the file is missing or malformed.
A strict load (load_security_config(strict=True)) raises SecurityConfigError
instead of falling back or skipping, for callers that must not scan with less
than the user configured.

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

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common import safe_regex
from src.common.logging import get_logger
from src.common.paths import get_security_dir

logger = get_logger(__name__)

PROFILES_DIR    = Path(__file__).parent / "profiles"
USER_CONFIG_PATH = get_security_dir() / "security_profile.yaml"


class SecurityConfigError(Exception):
    """
    Raised by a strict load when the profile cannot be used exactly as written.

    `reason` is a short stable code for callers ('profile_unreadable',
    'profile_not_mapping', 'pattern_invalid'). The message carries the detail
    and is meant for logs only: a YAML error can quote a line of the profile,
    and the profile's allowlist holds real secret values.
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass
class ScanConfig:
    """
    Scan settings applied to one scan point (prompt or response).

    categories:       CATEGORY → enabled bool.
                      Missing keys fall back to the detector's ENABLED_BY_DEFAULT.
    custom_patterns:  list of [{name, pattern, severity}] dicts (user-defined extra regex,
                      RE2 syntax: no lookaround or backreferences). A finding is group 1
                      when the pattern has one, else the whole match.
    keyword_blocklist: list of literal strings to flag.
    allowlist:        exact secret values that should NEVER be flagged or block a prompt.
                      Checked before any Finding is created - zero detector overhead.
    """
    categories: dict = field(default_factory=dict)
    custom_patterns: list = field(default_factory=list)
    keyword_blocklist: list = field(default_factory=list)
    allowlist: list = field(default_factory=list)


@dataclass
class SecurityConfig:
    """Top-level security config loaded from security_profile.yaml."""
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


def _skip_pattern(strict: bool, message: str) -> None:
    """Log a custom pattern being skipped, or refuse it under a strict load."""
    if strict:
        raise SecurityConfigError("pattern_invalid", message)
    logger.warning(message)


def _unusable_regex(label: str, name: str, reason: str) -> str:
    """The warning for a custom pattern the scanner's regex engine cannot compile."""
    return (
        f"{label}: custom pattern '{name}' skipped - RE2 cannot compile it "
        f"({reason.rstrip('.')}). Custom patterns run on RE2 so that no pattern can "
        "take unbounded time in a hook."
    )


def _validate_patterns(patterns: list, label: str, cwd: str | None = None, strict: bool = False) -> list:
    """
    Validate and normalise custom patterns.

    Mode A - examples-based: system generates a regex automatically.
    Mode B - manual regex: compiled and used as-is.

    Both modes are checked against RE2 (src.common.safe_regex), the engine the
    scanner runs custom patterns on. A pattern that cannot be used is skipped
    with a warning, or raises SecurityConfigError when strict.
    """
    valid = []
    for p in patterns:
        if not isinstance(p, dict):
            if strict:
                raise SecurityConfigError("pattern_invalid", f"{label}: custom pattern entry is not a mapping")
            continue

        name = p.get("name", "?")
        if strict and "name" not in p:
            # The scanner labels findings by name and skips nameless patterns.
            raise SecurityConfigError("pattern_invalid", f"{label}: custom pattern has no name")

        # ── Mode A: examples-based pattern generation ──────────────────────
        if "examples" in p and "pattern" not in p:
            _MAX_EXAMPLES = 5
            examples = p.get("examples") or []
            if not examples:
                _skip_pattern(strict, f"{label}: pattern '{name}' has empty examples list - skipped")
                continue
            if len(examples) > _MAX_EXAMPLES:
                logger.warning(
                    f"{label} [{name}]: {len(examples)} examples provided - "
                    f"only the first {_MAX_EXAMPLES} are used"
                )
                examples = examples[:_MAX_EXAMPLES]
            entry = None
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
                    f"{label} [{name}]: generated pattern - "
                    f"confidence={generated.confidence} "
                    f"fp_risk={generated.false_positive_risk} "
                    f"validated={generated.examples_matched}/{generated.examples_total}"
                    f"{codebase_note}"
                )
                for line in generated.summary().splitlines():
                    logger.debug(f"  {line}")
                entry = generated.to_scan_config_entry()
            except Exception as e:
                if strict:
                    raise SecurityConfigError(
                        "pattern_invalid", f"{label}: pattern builder failed for '{name}': {e}"
                    ) from e
                logger.warning(f"{label}: pattern builder failed for '{name}': {e} - skipped")
            if entry is not None:
                # What the builder generates must run on the scanner's engine too.
                reason = safe_regex.check(entry["pattern"], finder=True)
                if reason is None:
                    valid.append(entry)
                else:
                    _skip_pattern(strict, _unusable_regex(label, name, reason))
            continue

        # ── Mode B: manual regex ───────────────────────────────────────────
        if "pattern" not in p:
            _skip_pattern(strict, f"{label}: pattern '{name}' has neither 'pattern' nor 'examples' - skipped")
            continue
        reason = safe_regex.check(p["pattern"], finder=True)
        if reason is None:
            valid.append(p)
        else:
            _skip_pattern(strict, _unusable_regex(label, name, reason))

    return valid


def _parse_scan_section(
    raw: dict, defaults: ScanConfig, cwd: str | None = None, strict: bool = False
) -> ScanConfig:
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
        custom_patterns=_validate_patterns(patterns, "section", cwd=cwd, strict=strict),
        keyword_blocklist=raw.get("keyword_blocklist") or defaults.keyword_blocklist,
        allowlist=defaults.allowlist,  # allowlist is global - always inherited, no per-section override
    )


def _parse_config(raw: dict[str, Any], cwd: str | None = None, strict: bool = False) -> SecurityConfig:
    """Parse raw YAML dict into a SecurityConfig."""
    base = ScanConfig(
        categories=raw.get("categories") or {},
        custom_patterns=_validate_patterns(raw.get("custom_patterns") or [], "top-level", cwd=cwd, strict=strict),
        keyword_blocklist=raw.get("keyword_blocklist") or [],
        allowlist=[str(v) for v in (raw.get("allowlist") or []) if v],
    )

    prompt_raw   = raw.get("prompt")
    response_raw = raw.get("response")

    prompt_config = (
        _parse_scan_section(prompt_raw, base, cwd=cwd, strict=strict) if isinstance(prompt_raw, dict) else base
    )
    response_config = (
        _parse_scan_section(response_raw, base, cwd=cwd, strict=strict) if isinstance(response_raw, dict) else base
    )

    return SecurityConfig(
        enabled=bool(raw.get("enabled", False)),
        plan=str(raw.get("plan", "standard")),
        scope=str(raw.get("scope", "both")),
        prompt_config=prompt_config,
        response_config=response_config,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def load_security_config(cwd: str | None = None, strict: bool = False) -> SecurityConfig:
    """
    Load user security config from disk.

    Returns SecurityConfig(enabled=False) if the config file does not exist.

    Not strict (the default): never raises. An unreadable or malformed file
    falls back to the standard preset, and unusable custom patterns are
    skipped with a warning.

    Strict: raises SecurityConfigError instead of either. Without PyYAML the
    fallback preset is empty too, so a non-strict load can quietly lose the
    user's custom patterns and keyword blocklist.
    """
    if not USER_CONFIG_PATH.exists():
        return SecurityConfig(enabled=False)

    try:
        import yaml
        with open(USER_CONFIG_PATH, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        if strict:
            raise SecurityConfigError("profile_unreadable", f"security_profile.yaml unreadable: {e}") from e
        logger.warning(f"security_profile.yaml unreadable - falling back to standard preset: {e}")
        raw = _load_preset("standard")

    if not isinstance(raw, dict):
        if strict:
            raise SecurityConfigError("profile_not_mapping", "security_profile.yaml is not a mapping")
        logger.warning("security_profile.yaml is not a mapping - falling back to standard preset")
        raw = _load_preset("standard")

    return _parse_config(raw, cwd=cwd, strict=strict)
