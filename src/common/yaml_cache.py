"""
YAML profile reading for the hook hot path - shared by guardrails and
security scanning.

    load_yaml           parse a YAML mapping (CSafeLoader when available)
    read_yaml_mapping   the same, through a JSON cache beside the file
    fingerprint         a short, stable hash of a parsed profile
    strict_loader       a loader that refuses duplicate keys (editors only)

Most of the cost of reading a profile on the hot path is `import yaml`, not
the parse, so the parsed mapping is cached as JSON (stdlib) beside the YAML:

  - it caches the RAW mapping, not a compiled policy, so validation still runs
    on every call and behaviour is identical either way
  - it is keyed on the source file's mtime and size, so a hand-edit can never
    be served stale and there is no invalidation step to forget
  - a miss, a corrupt cache or an unwritable directory just means reading the
    YAML

Stdlib only at import time; yaml and the logger are imported on first use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Keys starting with this prefix are editor data: never parsed as policy and
# left out of the fingerprint (see fingerprint()).
EDITOR_KEY_PREFIX = "ui_"

CACHE_SUFFIX = ".cache.json"
_CACHE_FORMAT = 1


def _log():
    """The logger, imported on first use: importing logging setup costs more
    than a cached read."""
    from src.common.logging import get_logger
    return get_logger(__name__)


def load_yaml(path: Path, what: str = "profile") -> dict | None:
    """
    Parse a YAML mapping. `what` names the file in log messages.

    None when the file is unreadable or not a mapping; an empty file is {}.
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
        from src.common.package_repair import report_if_missing

        report_if_missing(exc, "pyyaml")
        _log().warning(f"{what} unreadable at {path}: {exc}")
        return None
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        _log().warning(f"{what} at {path} is not a mapping")
        return None
    return raw


def read_yaml_mapping(path: Path, what: str = "profile") -> dict | None:
    """
    A YAML mapping from disk, through the parsed-mapping cache. None when the
    file is unreadable or not a mapping; an empty file is {}. Never raises.
    """
    raw = _read_cache(path)
    if raw is None:
        raw = load_yaml(path, what)
        if raw is not None:
            _write_cache(path, raw)
    return raw


def cache_path(source: Path) -> Path:
    return source.with_name(source.name + CACHE_SUFFIX)


def _read_cache(source: Path) -> dict | None:
    """The cached mapping if it is current for `source`, else None."""
    try:
        import json

        cache = cache_path(source)
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
    import os

    temporary = None
    try:
        import json

        stat = source.stat()
        cache = cache_path(source)
        payload = {
            "format": _CACHE_FORMAT,
            "source": str(source),
            "source_mtime_ns": stat.st_mtime_ns,
            "source_size": stat.st_size,
            "profile": raw,
        }
        temporary = cache.with_name(cache.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temporary, cache)
        temporary = None
    except Exception:
        pass
    finally:
        if temporary is not None:
            try:
                os.remove(temporary)
            except Exception:
                pass


def fingerprint(raw: dict, editor_prefix: str = EDITOR_KEY_PREFIX) -> str | None:
    """
    First 16 hex characters of the SHA256 of the policy as loaded. Never raises.

    Hashed from the parsed mapping, not the file bytes: editing a comment or the
    layout leaves the hash unchanged, while reordering list items changes it.
    Editor data (keys starting `editor_prefix`) is left out because it never
    affects a decision.
    """
    try:
        import hashlib
        import json

        policy = {k: v for k, v in raw.items() if not str(k).startswith(editor_prefix)}
        text = json.dumps(policy, sort_keys=True, default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return None


def strict_loader():
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
