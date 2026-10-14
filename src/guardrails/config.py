"""
Profile loading and validation.

Same posture as the security scanner:

    ships disabled -> absence of the file means off, not on
    never raises   -> a broken profile degrades, it does not break the agent
    read per call  -> a config change takes effect immediately

The global policy lives at ~/.cloudbyte/guardrails/global_profile.yaml (the
pre-workspace name, guardrails_profile.yaml, is still read when the new one is
absent). A workspace can add its own file under guardrails/workspaces/ - see
src/guardrails/workspaces.py. Every file has the same format:

    schema_version: 1
    enabled: false
    plan: standard
    tables:
      bash: [ ...ordered rules, explicit fall-through last... ]
      file: [ ... ]
      mcp:  [ ... ]
      web:  [ ... ]
    allowlist: []                 # exact commands/paths that never match
    mcp_allowlist: []             # MCP tools considered governed
    known_hosts: []
    ui_categories: {...}          # any `ui_` key: an editor's own data, never read here

Validation is the save gate for any editor: validate_profile_text() takes the
YAML text, validate_profile() an already-parsed mapping. Both run the same
parsing as the engine and return problems as sentences; empty means safe to save.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.common.paths import get_guardrails_dir
from src.guardrails.decision import ASK
from src.guardrails.rules import BUILTIN_ID_PREFIX, Table, ensure_unique_ids, parse_table
from src.guardrails.toolcall import (
    KIND_AGENT,
    KIND_DELETE,
    KIND_EDIT,
    KIND_MCP,
    KIND_READ,
    KIND_SEARCH,
    KIND_SHELL,
    KIND_WEB,
    KIND_WRITE,
)

def _log():
    """
    The logger, imported on first use.

    src.common.logging is slow to import, and this path runs before every
    governed tool call while logging nothing when things are working.
    """
    from src.common.logging import get_logger
    return get_logger(__name__)


PROFILES_DIR = Path(__file__).parent / "profiles"
# The global policy - every session on this machine. When the organisation
# manages guardrails centrally, the CloudByte sync agent writes it; otherwise
# it is the user's own file.
PROFILE_FILENAME = "global_profile.yaml"
# Its name before workspace policies existed. Read when the new name is absent,
# so a machine (or an older sync agent) that still has only the old file keeps
# its policy. This plugin never renames it: the sync agent owns that move.
LEGACY_PROFILE_FILENAME = "guardrails_profile.yaml"

# Bumped whenever the profile format changes in a way an older plugin cannot
# read correctly.
CURRENT_SCHEMA_VERSION = 1

# The documented kill switch. Set this and guardrails are inert on the next
# tool call - no file edit, no restart, no uninstall.
KILL_SWITCH_ENV = "CLOUDBYTE_GUARDRAILS_DISABLED"

# The same switch, carried to the dashboard. With the HTTP transport the policy
# is evaluated inside the long-running dashboard process, whose environment was
# fixed when it started, so the PreToolUse hook forwards the variable as this
# header (hooks/hooks.json, `headers` + `allowedEnvVars`) and the route honours
# it on every call.
KILL_SWITCH_HEADER = "X-CloudByte-Guardrails-Disabled"
_KILL_SWITCH_WORDS = frozenset({"1", "true", "yes", "on"})


def kill_switch_set(value: str | None) -> bool:
    """True when a kill-switch value (from the environment or the header) means off."""
    return (value or "").strip().lower() in _KILL_SWITCH_WORDS

# Which decision table governs which kind of operation.
TABLE_FOR_KIND: dict[str, str] = {
    KIND_SHELL: "bash",
    KIND_READ: "file",
    KIND_WRITE: "file",
    KIND_EDIT: "file",
    KIND_DELETE: "file",
    KIND_SEARCH: "file",
    KIND_MCP: "mcp",
    KIND_WEB: "web",
    KIND_AGENT: "mcp",
}

TABLE_NAMES = ("bash", "file", "mcp", "web")

# The top-level settings the engine reads. Any other key is reported, because
# a typo like `tabels:` otherwise loads cleanly with nothing enforced. Keys
# starting with EDITOR_KEY_PREFIX belong to whatever editor wrote the file, and
# the engine never reads them.
KNOWN_SETTINGS = (
    "schema_version", "enabled", "plan", "tables",
    "allowlist", "mcp_allowlist", "known_hosts",
)
LIST_SETTINGS = ("allowlist", "mcp_allowlist", "known_hosts")
EDITOR_KEY_PREFIX = "ui_"

_TRUE_WORDS = frozenset({"true", "yes", "on", "1"})
_FALSE_WORDS = frozenset({"false", "no", "off", "0"})


@dataclass
class GuardrailsProfile:
    """The loaded policy. Immutable in practice - reloaded, never mutated."""

    enabled: bool = False
    plan: str = "standard"
    schema_version: int = CURRENT_SCHEMA_VERSION
    tables: dict[str, Table] = field(default_factory=dict)
    allowlist: frozenset[str] = frozenset()
    mcp_allowlist: tuple[str, ...] = ()
    known_hosts: tuple[str, ...] = ()
    # Non-fatal problems found while loading. Surfaced by validate_profile()
    # so an editor can refuse to save; logged once at load on the hot path.
    errors: tuple[str, ...] = ()
    source: str = "absent"
    # Identifies this policy; recorded on every audit row. See _fingerprint().
    fingerprint: str | None = None

    def table_for(self, kind: str) -> Table | None:
        return self.tables.get(TABLE_FOR_KIND.get(kind, ""))

    def is_allowlisted(self, *values: str | None) -> bool:
        """True when any of these exact strings is on the allowlist."""
        if not self.allowlist:
            return False
        return any(v and v in self.allowlist for v in values)


def _load_yaml(path: Path, what: str = "guardrails profile") -> dict | None:
    """
    Parse a YAML mapping. `what` names the file in log messages.

    Uses CSafeLoader when libyaml is available, which is much faster, and falls
    back silently, since the only difference is speed.
    """
    try:
        import yaml
        try:
            from yaml import CSafeLoader as _Loader   # type: ignore[attr-defined]
        except ImportError:
            from yaml import SafeLoader as _Loader    # type: ignore[assignment]

        with open(path, encoding="utf-8") as handle:
            raw = yaml.load(handle, Loader=_Loader)
    except Exception as exc:
        _log().warning(f"{what} unreadable at {path}: {exc}")
        return None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _log().warning(f"{what} at {path} is not a mapping")
        return None
    return raw


def read_yaml_mapping(path: Path, what: str = "guardrails profile") -> dict | None:
    """
    A YAML mapping from disk, through the parsed-mapping cache below. None when
    the file is unreadable or not a mapping; an empty file is {}. Never raises.

    Used for every hand-edited YAML file in the guardrails directory.
    """
    raw = _read_cache(path)
    if raw is None:
        raw = _load_yaml(path, what)
        if raw is not None:
            _write_cache(path, raw)
    return raw


# ── Parsed-profile cache ──────────────────────────────────────────────────────
#
# Most of the cost of reading the profile on the hot path is `import yaml`, not
# the parse, so the parsed mapping is cached as JSON (stdlib) beside the YAML.
#
#   - it caches the RAW mapping, not a compiled policy, so validation and rule
#     construction still run on every call and behaviour is identical either way
#   - it is keyed on the source file's mtime and size, so a hand-edit can never
#     be served stale and there is no invalidation step to forget
#   - a miss, a corrupt cache or an unwritable directory just means reading the
#     YAML

CACHE_SUFFIX = ".cache.json"
_CACHE_FORMAT = 1


def _cache_path(source: Path) -> Path:
    return source.with_name(source.name + CACHE_SUFFIX)


def _read_cache(source: Path) -> dict | None:
    """The cached mapping if it is current for `source`, else None."""
    try:
        import json

        cache = _cache_path(source)
        stat = source.stat()
        with open(cache, encoding="utf-8") as handle:
            payload = json.load(handle)

        if (
            payload.get("format") == _CACHE_FORMAT
            and payload.get("source_mtime_ns") == stat.st_mtime_ns
            and payload.get("source_size") == stat.st_size
            and isinstance(payload.get("profile"), dict)
        ):
            return payload["profile"]
    except Exception:
        pass
    return None


def _write_cache(source: Path, raw: dict) -> None:
    """
    Write the cache atomically. Never raises - it is only an optimisation.

    Several processes can miss the cache at once (every Cursor hook is its own
    process). Each writes its own temp file and renames it into place; on
    Windows the rename fails if another process holds the target open, which is
    fine since an identical copy won. The temp file is removed on every failure
    path.
    """
    import os as _os

    temporary = None
    try:
        import json

        stat = source.stat()
        cache = _cache_path(source)
        payload = {
            "format": _CACHE_FORMAT,
            "source": str(source),
            "source_mtime_ns": stat.st_mtime_ns,
            "source_size": stat.st_size,
            "profile": raw,
        }
        temporary = cache.with_name(cache.name + f".{_os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        _os.replace(temporary, cache)
        temporary = None
    except Exception:
        pass
    finally:
        if temporary is not None:
            try:
                _os.remove(temporary)
            except Exception:
                pass


def load_preset(name: str) -> dict:
    """Load a shipped preset. Returns {} when the name is unknown."""
    path = PROFILES_DIR / f"{name}.yaml"
    if not path.exists():
        _log().warning(f"unknown guardrails preset '{name}'")
        return {}
    return _load_yaml(path) or {}


def shipped_rule_ids() -> frozenset[str]:
    """
    Every id the shipped presets use. Editor-time only (profile_notes), never
    the hot path: it reads every preset.
    """
    ids: set[str] = set()
    for path in sorted(PROFILES_DIR.glob("*.yaml")):
        for rows in (load_preset(path.stem).get("tables") or {}).values():
            for row in rows or ():
                if isinstance(row, dict) and row.get("id"):
                    ids.add(str(row["id"]))
    return frozenset(ids)


def _describe(value: Any) -> str:
    """How a wrongly-typed value reads in an error message."""
    if value is None:
        return "empty"
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, (int, float)):
        return f"the number {value}"
    if isinstance(value, str):
        return f"the text '{value}'"
    return {"list": "a list", "dict": "a mapping"}.get(type(value).__name__, type(value).__name__)


def _read_enabled(value: Any, errors: list[str]) -> bool:
    """
    `enabled` as a real boolean (`bool("false")` is True).

    An unambiguous word is read as meant and still reported, so an editor
    refuses to save it; anything else is refused outright and guardrails stay
    off, the same "do not guess at the policy" rule as a too-new schema.
    """
    if isinstance(value, bool):
        return value
    word = str(value).strip().lower()
    if word in _TRUE_WORDS or word in _FALSE_WORDS:
        meant = word in _TRUE_WORDS
        errors.append(
            f"enabled must be true or false, not {_describe(value)} - read as {str(meant).lower()}"
        )
        return meant
    errors.append(
        f"enabled must be true or false, not {_describe(value)} - guardrails stay off until it is fixed"
    )
    return False


def _read_list(raw: dict[str, Any], name: str, errors: list[str]) -> list[str]:
    """
    A list-of-text setting. A bare string is read as a one-item list and
    reported, rather than iterated character by character.
    """
    value = raw.get(name)
    if value is None:
        return []
    if isinstance(value, str):
        errors.append(
            f"{name} must be a list - write it as `{name}: [\"{value}\"]`; read as a one-item list"
        )
        return [value] if value else []
    if not isinstance(value, list):
        errors.append(f"{name} must be a list of text values, not {_describe(value)} - ignored")
        return []

    items: list[str] = []
    for index, item in enumerate(value):
        if item is None or item == "":
            continue
        if isinstance(item, (dict, list)):
            errors.append(f"{name}[{index}] must be text, not {_describe(item)} - ignored")
            continue
        items.append(str(item))
    return items


def _fingerprint(raw: dict) -> str | None:
    """
    First 16 hex characters of the SHA256 of the policy as loaded. Never raises.

    Hashed from the parsed mapping, not the file bytes: editing a comment or the
    layout leaves the hash unchanged, while reordering rules changes it. Editor
    data (`ui_` keys) is left out because it never affects a decision.
    """
    try:
        import hashlib
        import json

        policy = {k: v for k, v in raw.items() if not str(k).startswith(EDITOR_KEY_PREFIX)}
        text = json.dumps(policy, sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


def _unknown_setting(key: Any) -> str:
    import difflib

    close =difflib.get_close_matches(str(key), KNOWN_SETTINGS, n=1)
    hint = f" - did you mean '{close[0]}'?" if close else ""
    return (
        f"unknown setting '{key}'{hint} (known: {', '.join(KNOWN_SETTINGS)}; an editor's own "
        f"data belongs under a key starting with '{EDITOR_KEY_PREFIX}')"
    )


def parse_profile(raw: Any, source: str = "user") -> GuardrailsProfile:
    """
    Build a profile from a raw mapping. Never raises.

    A structural problem, including a profile that is not a mapping at all,
    produces a profile carrying `errors` rather than an exception, so the
    caller decides whether to degrade or to refuse.
    """
    errors: list[str] = []

    if not isinstance(raw, dict):
        what = "empty" if raw is None else _describe(raw)
        return GuardrailsProfile(
            enabled=False,
            errors=(f"the profile must be a mapping of settings (schema_version, enabled, "
                    f"tables, ...), but it is {what}",),
            source=source,
        )

    for key in raw:
        if key not in KNOWN_SETTINGS and not str(key).startswith(EDITOR_KEY_PREFIX):
            errors.append(_unknown_setting(key))

    schema_version = raw.get("schema_version", CURRENT_SCHEMA_VERSION)
    try:
        if isinstance(schema_version, bool):
            raise TypeError
        schema_version = int(schema_version)
    except (TypeError, ValueError):
        errors.append(f"schema_version '{schema_version}' is not an integer")
        schema_version = CURRENT_SCHEMA_VERSION
    if schema_version > CURRENT_SCHEMA_VERSION:
        # load_profile() turns guardrails off for this - so it must be an error
        # here, or an editor could save a file that silently removes protection.
        errors.append(
            f"schema_version {schema_version} is newer than this plugin supports "
            f"({CURRENT_SCHEMA_VERSION}) - guardrails stay off until the plugin is updated"
        )
    elif schema_version < 1:
        errors.append(f"schema_version must be {CURRENT_SCHEMA_VERSION}, not {schema_version}")

    enabled = _read_enabled(raw.get("enabled", False), errors)

    plan = raw.get("plan", "standard")
    if isinstance(plan, (dict, list)) or plan is None:
        errors.append(f"plan must be a name, not {_describe(plan)}")
        plan = "standard"

    raw_tables = raw.get("tables") or {}
    if not isinstance(raw_tables, dict):
        errors.append("tables: must be a mapping of table name to rule list")
        raw_tables = {}

    tables: dict[str, Table] = {}
    for name in TABLE_NAMES:
        if name in raw_tables:
            tables[name] = parse_table(name, raw_tables[name], errors)
    ensure_unique_ids(tables, errors)

    for unknown in set(raw_tables) - set(TABLE_NAMES):
        errors.append(f"tables: unknown table '{unknown}' (known: {', '.join(TABLE_NAMES)})")

    allowlist = _read_list(raw, "allowlist", errors)
    mcp_allowlist = _read_list(raw, "mcp_allowlist", errors)
    known_hosts = _read_list(raw, "known_hosts", errors)

    return GuardrailsProfile(
        enabled=enabled,
        plan=str(plan),
        schema_version=schema_version,
        tables=tables,
        allowlist=frozenset(allowlist),
        mcp_allowlist=tuple(mcp_allowlist),
        known_hosts=tuple(known_hosts),
        errors=tuple(errors),
        source=source,
        fingerprint=_fingerprint(raw),
    )


def user_profile_path() -> Path:
    """
    The global profile's path: the current name, or the legacy one when only
    that exists. When neither exists, the current name (where one would go).
    """
    directory = get_guardrails_dir()
    current = directory / PROFILE_FILENAME
    if current.exists():
        return current
    legacy = directory / LEGACY_PROFILE_FILENAME
    return legacy if legacy.exists() else current


def load_profile(path: Path | None = None) -> GuardrailsProfile:
    """
    Load the global profile from disk. Never raises.

    Degradation ladder:

      kill switch set        -> disabled
      file absent            -> disabled  (feature ships off)
      schema too new         -> disabled  (refuse to guess at a policy we cannot read)
      unreadable / not a map -> shipped preset
      individual bad rows    -> those rows dropped, the rest kept
    """
    if kill_switch_set(os.environ.get(KILL_SWITCH_ENV)):
        _log().info(f"guardrails disabled via {KILL_SWITCH_ENV}")
        return GuardrailsProfile(enabled=False, source="kill_switch")

    target = path or user_profile_path()
    if not target.exists():
        return GuardrailsProfile(enabled=False, source="absent")
    return load_profile_file(target, source="user")


def load_profile_file(target: Path, source: str, raw: dict | None = None) -> GuardrailsProfile:
    """
    One existing profile file through the degradation ladder (everything in
    load_profile() after the kill switch and the existence check). Shared by
    the global profile and every workspace profile, so a workspace file can
    never be read more loosely than the global one. Never raises.

    `raw` is the already-read mapping when the caller has it, so the file is
    not read twice.
    """
    if raw is None:
        raw = read_yaml_mapping(target)

    if raw is None:
        fallback_name = "standard"
        _log().warning(
            f"guardrails profile {target.name} unusable - falling back to shipped "
            f"'{fallback_name}' preset"
        )
        return parse_profile(load_preset(fallback_name), source=f"preset:{fallback_name}")

    profile = parse_profile(raw, source=source)

    if profile.schema_version > CURRENT_SCHEMA_VERSION:
        _log().error(
            f"guardrails profile {target.name} schema_version {profile.schema_version} is newer "
            f"than this plugin supports ({CURRENT_SCHEMA_VERSION}) - it is inert until the "
            f"plugin is updated"
        )
        return GuardrailsProfile(enabled=False, source="schema_too_new")

    for problem in profile.errors:
        _log().warning(f"guardrails profile {target.name}: {problem}")

    return profile


def validate_profile(raw: Any) -> list[str]:
    """
    Validate an already-parsed profile, for an editor or a pre-save check.

    Returns the list of problems - empty means the profile is clean. The strict
    counterpart to load_profile()'s tolerant degradation. Never raises,
    whatever it is given.
    """
    return list(parse_profile(raw, source="validation").errors)


def validate_profile_text(text: str) -> list[str]:
    """
    Validate a profile exactly as it would be saved - the YAML text itself.

    The entry point an editor should call before writing the file. On top of
    validate_profile() it reports YAML syntax errors (with line and column) and
    duplicate keys, which YAML resolves silently by keeping the LAST value and
    the runtime loader cannot see.
    """
    import yaml

    try:
        raw = yaml.load(text, Loader=_strict_loader())
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        return [f"YAML error at {where}{exc.problem or exc}"]
    except Exception as exc:
        return [f"YAML error: {exc}"]
    if raw is None:
        return ["the profile is empty - write `enabled: false` to keep guardrails off"]
    return validate_profile(raw)


def profile_notes(raw: Any) -> list[str]:
    """
    Things a profile author should know that are not problems; these never
    block a save.

      - ask rules: Cursor shows no approval prompt for a hook's `ask`, so on
        Cursor an ask rule lets the call run.
      - a rule that can never fire, because a rule above it in the same table
        checks exactly the same thing and the first match wins.
      - a `cb.` id that is not one of the shipped rules.
    """
    profile = raw if isinstance(raw, GuardrailsProfile) else parse_profile(raw, source="validation")
    return _ask_notes(profile) + _unreachable_rule_notes(profile) + _builtin_prefix_notes(profile)


def _ask_notes(profile: GuardrailsProfile) -> list[str]:
    asks = [
        rule.rule_id
        for table in profile.tables.values()
        for rule in table.rules
        if rule.action == ASK
    ]
    if not asks:
        return []
    shown = ", ".join(asks[:5]) + (f" and {len(asks) - 5} more" if len(asks) > 5 else "")
    return [
        f"{len(asks)} rule(s) use ask ({shown}). Claude Code shows an approval prompt for "
        f"these; Cursor cannot, so on Cursor they let the call run. Use deny for anything "
        f"that must never run unreviewed."
    ]


def _unreachable_rule_notes(profile: GuardrailsProfile) -> list[str]:
    """
    Exact duplicates only: same table, same conditions. A broader rule hiding a
    narrower one is not reported, because with regex conditions that cannot be
    decided reliably, and a note must only claim what it can prove.
    """
    notes = []
    for table in profile.tables.values():
        first: dict[tuple, str] = {}
        for rule in table.rules:
            conditions = (frozenset(rule.constraints), frozenset(rule.booleans))
            if conditions in first:
                notes.append(
                    f"{rule.rule_id} can never fire: {first[conditions]} above it in the "
                    f"{table.name} table checks exactly the same thing, and the first match "
                    f"wins. Remove one of them, or change what it checks."
                )
            else:
                first[conditions] = rule.rule_id
    return notes


def _builtin_prefix_notes(profile: GuardrailsProfile) -> list[str]:
    shipped = None
    notes = []
    for table in profile.tables.values():
        for rule in table.rules:
            if not rule.rule_id.startswith(BUILTIN_ID_PREFIX):
                continue
            shipped = shipped if shipped is not None else shipped_rule_ids()
            if rule.rule_id not in shipped:
                notes.append(
                    f"{rule.rule_id} uses the {BUILTIN_ID_PREFIX} prefix, which names the "
                    f"plugin's built-in rules, but it is not one of them. Give your own rules "
                    f"your own prefix, for example acme.prod-deploy."
                )
    return notes


def _strict_loader():
    """A safe YAML loader that refuses duplicate keys. Validation only - never the hot path."""
    import yaml

    try:
        from yaml import CSafeLoader as _Base      # type: ignore[attr-defined]
    except ImportError:
        from yaml import SafeLoader as _Base       # type: ignore[assignment]

    class _NoDuplicatesLoader(_Base):              # type: ignore[misc, valid-type]
        pass

    def construct_mapping(loader, node, deep=False):
        first_seen: dict[Any, int] = {}
        for key_node, _ in node.value:
            key = loader.construct_object(key_node, deep=True)
            try:
                if key in first_seen:
                    raise yaml.constructor.ConstructorError(
                        None, None,
                        f"duplicate key '{key}' (first defined on line {first_seen[key] + 1}) - "
                        f"YAML would silently keep only the last one",
                        key_node.start_mark,
                    )
                first_seen[key] = key_node.start_mark.line
            except TypeError:
                continue                            # an unhashable key; the parser reports it
        return loader.construct_mapping(node, deep=deep)

    _NoDuplicatesLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
    return _NoDuplicatesLoader
