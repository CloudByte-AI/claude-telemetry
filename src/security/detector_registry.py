"""
Detector Registry — metadata facade over the live plugin registry.

Derives all metadata from the registered detector classes instead of a
hardcoded static list. Adding a new service means creating one new detector
file — no changes here needed.

Usage (UI / settings layer):
    from src.security.detector_registry import get_all_detector_meta, DetectorMeta

    meta = get_all_detector_meta()  # list[DetectorMeta], sorted by domain then category
"""

from __future__ import annotations

from dataclasses import dataclass

# Trigger all @register_detector decorators
import src.security.detectors  # noqa: F401

from src.security.registry import DetectorRegistry


@dataclass(frozen=True)
class DetectorMeta:
    """UI-facing descriptor for one detector."""
    key: str         # == detector.CATEGORY — used as the categories: YAML key
    name: str        # Human-readable name (same as key for now)
    description: str # One-line plain-English description
    domain: str      # Folder grouping: "Cloud & Infrastructure", "AI & ML Platforms", etc.
    default: bool    # True if enabled in standard preset


def get_all_detector_meta() -> list[DetectorMeta]:
    """
    Return DetectorMeta for every registered detector, sorted by domain then name.
    """
    result: list[DetectorMeta] = []
    for cls in DetectorRegistry.all_detectors():
        result.append(DetectorMeta(
            key=cls.CATEGORY,
            name=cls.CATEGORY,
            description=cls.DESCRIPTION,
            domain=cls.DOMAIN,
            default=cls.ENABLED_BY_DEFAULT,
        ))

    result.sort(key=lambda m: (m.domain, m.name))
    return result


def get_default_categories() -> dict[str, bool]:
    """Return {CATEGORY: ENABLED_BY_DEFAULT} for every registered detector."""
    return DetectorRegistry.default_categories()


def get_categories_by_domain() -> dict[str, list[DetectorMeta]]:
    """Return meta grouped by domain, sorted for display."""
    by_domain: dict[str, list[DetectorMeta]] = {}
    for m in get_all_detector_meta():
        by_domain.setdefault(m.domain, []).append(m)
    return dict(sorted(by_domain.items()))


# Keep a simple DETECTORS list for backward compat with any UI code that
# iterates it. Populated on first import.
DETECTORS: list[DetectorMeta] = []
_populated = False


def _ensure_populated() -> None:
    global DETECTORS, _populated
    if not _populated:
        DETECTORS = get_all_detector_meta()
        _populated = True


_ensure_populated()


# ── Module-level helpers used by the app layer ────────────────────────────────

def all_categories() -> list[str]:
    """Return all registered CATEGORY names (same order as registration)."""
    return DetectorRegistry.all_categories()


def get_detector_info_by_category() -> dict[str, dict]:
    """
    Return {CATEGORY: {types, example, type_details}} for every detector.
    Instantiates each detector once to pull TokenDefinition data for the UI.
    type_details is a list of {type, example} dicts for the rich tooltip.
    """
    result: dict[str, dict] = {}
    for cls in DetectorRegistry.all_detectors():
        try:
            defs = cls().definitions
            result[cls.CATEGORY] = {
                "types":        [d.type for d in defs],
                "example":      defs[0].example if defs else "",
                "type_details": [
                    {"type": d.type, "example": d.example}
                    for d in defs
                ],
            }
        except Exception:
            result[cls.CATEGORY] = {"types": [], "example": "", "type_details": []}
    return result
