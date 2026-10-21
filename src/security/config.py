"""
Security scanning configuration loader.

Reads, on every invocation:
  ~/.cloudbyte/security/security_profile.yaml           the global profile
  ~/.cloudbyte/security/workspaces/<folder>-<hash>.yaml  per-folder profiles
                                                        (src/common/workspace_profiles.py)

A workspace profile has the global profile's format and can only ADD
scanning: the profiles that apply to a session are merged strictest-wins
(_merge_scan_configs). Only the global file -> exactly the behaviour of the
global file alone.

A missing global file means scanning is off (unless a workspace file turns it
on). An unreadable file falls back to the last copy that parsed cleanly
(src/common/atomic_files.py), and only without one to the standard preset.
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
    # The profiles this config was built from, each with what it asks for and
    # what it allows (ScanSource). Empty for a config built by hand. The scanner
    # uses them to apply each profile's allowlist to that profile's findings
    # only, and to say which profile asked for each finding.
    sources: tuple = ()


@dataclass
class SecurityConfig:
    """Top-level security config loaded from security_profile.yaml."""
    enabled: bool = False
    plan: str = "standard"
    scope: str = "both"
    prompt_config: ScanConfig = field(default_factory=ScanConfig)
    response_config: ScanConfig = field(default_factory=ScanConfig)
    # Every enabled profile that applied (PolicySource), global first.
    sources: tuple = ()

    @property
    def primary(self):
        """The deciding profile by the tie rule: the global one if it applied, else the nearest."""
        return self.sources[0] if self.sources else None


SCOPE_GLOBAL = "global"
SCOPE_WORKSPACE = "workspace"

PLAN_RANK = {"minimal": 1, "standard": 2, "strict": 3}
SCAN_SCOPE_RANK = {"prompt_only": 1, "both": 2}


@dataclass(frozen=True)
class PolicySource:
    """One profile file that applied to a scan."""
    scope: str                   # SCOPE_GLOBAL | SCOPE_WORKSPACE
    path: str
    root: str | None             # the workspace folder (normalised); None for the global file
    profile_hash: str | None     # yaml_cache.fingerprint of the mapping
    restored: str | None = None  # "last_good" | "preset:standard" when the file itself was unusable

    def to_dict(self) -> dict:
        return {"scope": self.scope, "root": self.root, "profile_hash": self.profile_hash}


@dataclass(frozen=True)
class ScanSource:
    """What one profile asks for at one scan point - for per-profile allowlists and attribution."""
    policy: PolicySource
    categories: frozenset        # detector categories this profile turns on
    pattern_names: frozenset     # its custom pattern names
    keywords: frozenset          # its blocklist keywords, lowercased
    allowlist: frozenset         # values it allows


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


CUSTOM_CATEGORY = "Custom"
CUSTOM_CATEGORY_MAX = 60


# The entropy fallback is a heuristic, not a kind of secret: no pattern joins it.
ENTROPY_CATEGORY = "Entropy Secret"


def _pattern_category(p: dict, label: str) -> str | None:
    """
    A custom pattern's category (as guardrails' custom categories): its findings
    are reported under it instead of "Custom". Either a built-in category (any
    case; returned in its exact spelling) - the pattern then follows that
    category's switch, see _pattern_live - or a category of its own. None when
    it has none, or when it is unusable: empty, too long, "Custom" itself, or
    the entropy fallback.
    """
    raw = p.get("category")
    if raw is None:
        return None
    name = str(raw).strip()
    if not name or name.lower() == CUSTOM_CATEGORY.lower():
        return None
    if len(name) > CUSTOM_CATEGORY_MAX:
        logger.warning(f"{label}: pattern '{p.get('name', '?')}' category is over {CUSTOM_CATEGORY_MAX} characters - reported as Custom")
        return None
    if name.lower() == ENTROPY_CATEGORY.lower():
        logger.warning(f"{label}: pattern '{p.get('name', '?')}' cannot join the entropy fallback - reported as Custom")
        return None
    built_in = {c.lower(): c for c in _registered_defaults()}
    return built_in.get(name.lower(), name)


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
    Either keeps the pattern's custom `category` (see _pattern_category).

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
        category = _pattern_category(p, label)

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
                if category:
                    entry["category"] = category
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
        # The same compile the scanner uses, so a pattern that passes here can
        # never fail or hang there. A pattern that is not text is refused.
        reason = safe_regex.check(p["pattern"], finder=True)
        if reason is None:
            entry = {k: v for k, v in p.items() if k != "category"}
            if category:
                entry["category"] = category
            valid.append(entry)
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


# ── Reading one file ──────────────────────────────────────────────────────────

def _read_profile_strict(path: Path, what: str) -> dict:
    """
    One profile file parsed exactly as written, or SecurityConfigError. Reads
    the file itself, not the parsed-mapping cache or the last good copy, and
    writes nothing.
    """
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except Exception as e:
        raise SecurityConfigError("profile_unreadable", f"{what} {path.name} unreadable: {e}") from e
    if not isinstance(raw, dict):
        raise SecurityConfigError("profile_not_mapping", f"{what} {path.name} is not a mapping")
    return raw


def _read_profile_file(path: Path, what: str, strict: bool = False) -> tuple[dict, str | None]:
    """
    (raw mapping, restored-from) for one existing profile file.

    A clean read is remembered as the file's last good copy. An unreadable or
    non-mapping file falls back to that copy, and only without one to the
    standard preset - a half-written or mangled file must not change what is
    enforced. Never raises, unless strict (see _read_profile_strict).
    """
    if strict:
        return _read_profile_strict(path, what), None

    from src.common.atomic_files import load_last_good, save_last_good
    from src.common.yaml_cache import read_yaml_mapping

    raw = read_yaml_mapping(path, what)
    if isinstance(raw, dict):
        save_last_good(path, raw)
        return raw, None
    last_good = load_last_good(path)
    if last_good is not None:
        logger.warning(f"{what} {path.name} unreadable - using its last good copy")
        return last_good, "last_good"
    logger.warning(f"{what} {path.name} unreadable - falling back to standard preset")
    return _load_preset("standard"), "preset:standard"


def _source_for(scope: str, path: Path, raw: dict, root: str | None, restored: str | None) -> PolicySource:
    from src.common.yaml_cache import fingerprint

    return PolicySource(scope=scope, path=str(path), root=root, profile_hash=fingerprint(raw),
                        restored=restored)


# ── Merging (strictest wins) ──────────────────────────────────────────────────

def _registered_defaults() -> dict:
    """CATEGORY -> ENABLED_BY_DEFAULT for every registered detector."""
    import src.security.detectors  # noqa: F401 - registers the detectors
    from src.security.registry import DetectorRegistry

    return {cls.CATEGORY: bool(cls.ENABLED_BY_DEFAULT) for cls in DetectorRegistry.all_detectors()}


def _pattern_live(pattern: dict, cfg: ScanConfig, defaults: dict, scope: str) -> bool:
    """
    Whether a custom pattern runs at this scan point under the profile that
    carries it. A pattern in a built-in category follows that category: off in
    the profile (for this target) = the pattern is off too. In a WORKSPACE
    profile a category it does not mention is "no opinion", so the pattern runs
    unless the file switches the category off. A category of its own always runs.
    """
    category = pattern.get("category") if isinstance(pattern, dict) else None
    if category not in defaults:
        return True
    if scope == SCOPE_GLOBAL:
        return bool(cfg.categories.get(category, defaults[category]))
    return cfg.categories.get(category) is not False


def _live_patterns(cfg: ScanConfig, defaults: dict, scope: str) -> list:
    return [p for p in cfg.custom_patterns if isinstance(p, dict) and _pattern_live(p, cfg, defaults, scope)]


def _scan_source(policy: PolicySource, cfg: ScanConfig, defaults: dict) -> ScanSource:
    """
    What one profile asks for. In the GLOBAL profile a missing category means
    the detector's default; in a WORKSPACE profile it means "no opinion", so a
    sparse workspace file can never switch a detector on everywhere by omission.
    """
    if policy.scope == SCOPE_GLOBAL:
        enabled = {cat for cat, default in defaults.items() if cfg.categories.get(cat, default)}
    else:
        enabled = {cat for cat, value in cfg.categories.items() if value and cat in defaults}
    return ScanSource(
        policy=policy,
        categories=frozenset(enabled),
        pattern_names=frozenset(str(p.get("name")) for p in _live_patterns(cfg, defaults, policy.scope)),
        keywords=frozenset(str(k).lower() for k in cfg.keyword_blocklist if k),
        allowlist=frozenset(str(v) for v in cfg.allowlist if v),
    )


def _merge_scan_configs(parts: list[tuple[PolicySource, ScanConfig]]) -> ScanConfig:
    """
    One ScanConfig from several profiles' configs for the same scan point:
      categories         on when ANY profile turns it on
      custom_patterns    union of each profile's live patterns (_pattern_live),
                         de-duplicated by (name, pattern)
      keyword_blocklist  union, case-insensitive
      allowlist          only values EVERY profile allows (safe to skip inside
                         the detectors); each profile's own allowlist is then
                         applied to that profile's findings by the scanner
    A single profile keeps its config as it is (sources attached), so one
    global file behaves exactly as before.
    """
    import dataclasses

    defaults = _registered_defaults()
    sources = tuple(_scan_source(policy, cfg, defaults) for policy, cfg in parts)
    if len(parts) == 1:
        policy, cfg = parts[0]
        return dataclasses.replace(cfg, custom_patterns=_live_patterns(cfg, defaults, policy.scope), sources=sources)

    turned_on = set().union(*(source.categories for source in sources))
    categories = {cat: (cat in turned_on) for cat in defaults}

    patterns, seen_patterns = [], set()
    keywords, seen_keywords = [], set()
    for policy, cfg in parts:
        for pattern in _live_patterns(cfg, defaults, policy.scope):
            key = (str(pattern.get("name")), str(pattern.get("pattern")))
            if key not in seen_patterns:
                seen_patterns.add(key)
                patterns.append(pattern)
        for keyword in cfg.keyword_blocklist:
            if keyword and str(keyword).lower() not in seen_keywords:
                seen_keywords.add(str(keyword).lower())
                keywords.append(keyword)

    allowed_everywhere = frozenset.intersection(*(source.allowlist for source in sources))
    return ScanConfig(
        categories=categories,
        custom_patterns=patterns,
        keyword_blocklist=keywords,
        allowlist=sorted(allowed_everywhere),
        sources=sources,
    )


def _merge(profiles: list[tuple[PolicySource, "SecurityConfig"]]) -> SecurityConfig:
    """Enabled profiles -> one SecurityConfig, strictest wins. One profile keeps its own settings."""
    plan = max((cfg.plan for _, cfg in profiles), key=lambda p: PLAN_RANK.get(p, 0))
    scope = max((cfg.scope for _, cfg in profiles), key=lambda s: SCAN_SCOPE_RANK.get(s, 0))
    return SecurityConfig(
        enabled=True,
        plan=plan,
        scope=scope,
        prompt_config=_merge_scan_configs([(source, cfg.prompt_config) for source, cfg in profiles]),
        response_config=_merge_scan_configs([(source, cfg.response_config) for source, cfg in profiles]),
        sources=tuple(source for source, _ in profiles),
    )


# ── Public API ────────────────────────────────────────────────────────────────

WORKSPACE_WHAT = "workspace security profile"


def _workspace_profiles(
    workspace_roots: list, cwd: str | None, strict: bool = False
) -> list[tuple[PolicySource, SecurityConfig]]:
    """
    The workspace profiles that apply, nearest first, each read once. Never
    raises, unless strict: then a profile that cannot be found or used exactly
    as written raises SecurityConfigError.
    """
    from src.common import workspace_profiles as ws
    from src.common.yaml_cache import read_yaml_mapping

    try:
        directory = ws.workspaces_dir(get_security_dir())
        if not ws.any_workspace_files(directory):
            return []
        found, seen = [], set()
        for root in (workspace_roots or [None]):
            # Every profile from the session's folder up applies: security
            # profiles only add scanning, so a nearer one never replaces a
            # parent folder's.
            for entry in ws.find_workspace_files(root, cwd, directory, read_yaml_mapping,
                                                 what=WORKSPACE_WHAT, every_ancestor=True):
                if entry[1] not in seen:
                    seen.add(entry[1])
                    found.append(entry)
    except Exception as exc:
        if strict:
            raise SecurityConfigError(
                "profile_unreadable", f"workspace security profiles could not be read: {exc}"
            ) from exc
        logger.warning(f"workspace security profiles could not be read: {exc}")
        return []

    profiles = []
    for root, path, _ in found:
        raw, restored = _read_profile_file(path, WORKSPACE_WHAT, strict=strict)
        source = _source_for(SCOPE_WORKSPACE, path, raw, root, restored)
        profiles.append((source, _parse_config(raw, cwd=cwd, strict=strict)))
    return profiles


def load_security_config(
    cwd: str | None = None,
    workspace_root: str | None = None,
    workspace_roots: list | None = None,
    strict: bool = False,
) -> SecurityConfig:
    """
    The security config for a scan: the global profile plus the workspace
    profiles for the session's folder, merged strictest-wins.

    workspace_root   the session's folder (Claude Code: CLAUDE_PROJECT_DIR)
    workspace_roots  Cursor's roots; a prompt has no cwd, so every root's
                     profiles apply
    Without either, the folder is taken from `cwd`.

    Returns SecurityConfig(enabled=False) when nothing enables scanning.

    Not strict (the default): never raises. An unreadable or malformed file
    falls back to its last good copy or the standard preset, and unusable
    custom patterns are skipped with a warning.

    Strict: raises SecurityConfigError instead of either. Without PyYAML the
    fallback preset is empty too, so a non-strict load can quietly lose the
    user's custom patterns and keyword blocklist.
    """
    profiles: list[tuple[PolicySource, SecurityConfig]] = []
    global_cfg: SecurityConfig | None = None

    if USER_CONFIG_PATH.exists():
        raw, restored = _read_profile_file(USER_CONFIG_PATH, "security_profile.yaml", strict=strict)
        global_cfg = _parse_config(raw, cwd=cwd, strict=strict)
        profiles.append((_source_for(SCOPE_GLOBAL, USER_CONFIG_PATH, raw, None, restored), global_cfg))

    roots = list(workspace_roots or ([workspace_root] if workspace_root else []))
    profiles.extend(_workspace_profiles(roots, cwd, strict=strict))

    enabled = [(source, cfg) for source, cfg in profiles if cfg.enabled]
    if not enabled:
        return global_cfg or SecurityConfig(enabled=False)
    if len(enabled) == 1:
        source, cfg = enabled[0]
        merged = _merge(enabled)
        # One profile: its own plan and scope, configs with sources attached.
        return SecurityConfig(enabled=True, plan=cfg.plan, scope=cfg.scope,
                              prompt_config=merged.prompt_config,
                              response_config=merged.response_config,
                              sources=(source,))
    return _merge(enabled)
