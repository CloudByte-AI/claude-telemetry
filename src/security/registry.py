"""
Detector Registry - single source of truth for all registered detectors.

Usage:
    from src.security.registry import register_detector, DetectorRegistry

    @register_detector
    class MyDetector(BaseDetector):
        CATEGORY = "MyService"
        ...

Detectors self-register when their module is imported.
The detectors/__init__.py imports all modules, so importing that package
is sufficient to populate the registry before using DetectorRegistry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.security.detectors.base import BaseDetector

# Module-level registry: CATEGORY → detector class
_REGISTRY: dict[str, type[BaseDetector]] = {}


def register_detector(cls: type) -> type:
    """
    Class decorator that registers a detector in the global registry.
    Must be applied to classes that subclass BaseDetector and declare CATEGORY.
    """
    if not cls.CATEGORY:
        raise ValueError(f"Detector {cls.__name__} must declare a non-empty CATEGORY")
    if cls.CATEGORY in _REGISTRY:
        raise ValueError(
            f"Duplicate detector category '{cls.CATEGORY}': "
            f"already registered by {_REGISTRY[cls.CATEGORY].__name__}"
        )
    _REGISTRY[cls.CATEGORY] = cls
    return cls


class DetectorRegistry:
    """
    Read-only interface over the global detector registry.
    All methods are class methods - no instance needed.
    """

    @classmethod
    def get_enabled(cls, categories: dict[str, bool]) -> list[BaseDetector]:
        """
        Return instantiated detectors that are enabled in the given config.

        Args:
            categories: mapping of CATEGORY → bool; missing key = use detector's
                        ENABLED_BY_DEFAULT value.
        """
        result: list[BaseDetector] = []
        for category, detector_cls in _REGISTRY.items():
            enabled = categories.get(category, detector_cls.ENABLED_BY_DEFAULT)
            if enabled:
                result.append(detector_cls())
        return result

    @classmethod
    def all_detectors(cls) -> list[type[BaseDetector]]:
        """Return all registered detector classes in registration order."""
        return list(_REGISTRY.values())

    @classmethod
    def all_categories(cls) -> list[str]:
        """Return all registered category names."""
        return list(_REGISTRY.keys())

    @classmethod
    def get_detector(cls, category: str) -> type[BaseDetector] | None:
        """Look up a detector class by category name."""
        return _REGISTRY.get(category)

    @classmethod
    def default_categories(cls) -> dict[str, bool]:
        """
        Return the default enabled/disabled state for every registered category.
        Used to build the base ScanConfig when no user config exists.
        """
        return {cat: det_cls.ENABLED_BY_DEFAULT for cat, det_cls in _REGISTRY.items()}

    @classmethod
    def detector_count(cls) -> int:
        return len(_REGISTRY)
