"""
Matcher registry - single source of truth for the operation taxonomy.

Same pattern as src/security/registry.py: one file per unit, self-registering
on import.

    @register_matcher
    class RecursiveDeleteMatcher(BaseMatcher):
        OPERATION = "fs.delete.recursive"
        ...

`OPERATION` names are a PUBLIC INTERFACE. They land in user profiles, in
taxonomy.json and in audit rows, so renaming one is a breaking change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.guardrails.matchers.base import BaseMatcher

# OPERATION -> matcher class, in registration order.
_REGISTRY: dict[str, type["BaseMatcher"]] = {}

# Reserved taxonomy names with no implementation yet. Published in
# taxonomy.json with implemented=false so profiles and external tooling can
# target the full vocabulary before every matcher exists. An operation whose
# matcher is absent simply never fires.
_RESERVED: dict[str, dict] = {}


def register_matcher(cls: type) -> type:
    """
    Class decorator registering a matcher. Applied to BaseMatcher subclasses.

    Fails loudly on a duplicate or missing OPERATION: a silently dropped
    matcher is a silently missing guardrail.
    """
    operation = getattr(cls, "OPERATION", "")
    if not operation:
        raise ValueError(f"Matcher {cls.__name__} must declare a non-empty OPERATION")
    if operation in _REGISTRY:
        raise ValueError(
            f"Duplicate matcher operation '{operation}': "
            f"already registered by {_REGISTRY[operation].__name__}"
        )
    if not getattr(cls, "APPLIES_TO", ()):
        raise ValueError(f"Matcher {cls.__name__} must declare a non-empty APPLIES_TO")
    _REGISTRY[operation] = cls
    return cls


def reserve_operation(
    operation: str,
    domain: str,
    description: str,
    applies_to: tuple[str, ...],
    default_action: str = "allow",
    default_alert: str = "info",
) -> None:
    """
    Reserve a taxonomy name whose matcher is not written yet.

    Profiles and tooling target these strings, so they must be stable before
    the implementations land. Reserving is additive; implementing one later is
    not a breaking change.
    """
    if operation in _REGISTRY:
        raise ValueError(f"'{operation}' is implemented - remove it from the reserved list")
    _RESERVED[operation] = {
        "operation": operation,
        "domain": domain,
        "description": description,
        "applies_to": tuple(applies_to),
        "default_action": default_action,
        "default_alert": default_alert,
        "implemented": False,
    }


class MatcherRegistry:
    """Read-only interface over the registry. All class methods - no instance."""

    @classmethod
    def for_kind(cls, kind: str) -> list["BaseMatcher"]:
        """
        Instantiate every matcher that can fire for this operation kind.

        Dispatches on KIND, not on platform tool names (Bash vs Shell, Agent vs
        Task), so the registry never knows which platform is calling. Adapters
        map their own tool names onto kinds; see toolcall.ALL_KINDS.
        """
        return [
            matcher_cls()
            for matcher_cls in _REGISTRY.values()
            if kind in matcher_cls.APPLIES_TO
        ]

    @classmethod
    def get(cls, operation: str) -> type["BaseMatcher"] | None:
        return _REGISTRY.get(operation)

    @classmethod
    def all_matchers(cls) -> list[type["BaseMatcher"]]:
        return list(_REGISTRY.values())

    @classmethod
    def implemented_operations(cls) -> list[str]:
        return list(_REGISTRY)

    @classmethod
    def reserved_operations(cls) -> list[str]:
        return list(_RESERVED)

    @classmethod
    def all_operations(cls) -> list[str]:
        """Every taxonomy name, implemented or reserved, sorted."""
        return sorted(set(_REGISTRY) | set(_RESERVED))

    @classmethod
    def describe(cls) -> list[dict]:
        """
        The taxonomy as data - what taxonomy.json is generated from.

        Sorted by (domain, operation) so the generated file has a stable order
        and the drift test compares cleanly.
        """
        rows: list[dict] = []
        for operation, matcher_cls in _REGISTRY.items():
            rows.append({
                "operation": operation,
                "domain": matcher_cls.DOMAIN,
                "description": matcher_cls.DESCRIPTION,
                "applies_to": list(matcher_cls.APPLIES_TO),
                "default_action": matcher_cls.DEFAULT_ACTION,
                "default_alert": matcher_cls.DEFAULT_ALERT,
                "implemented": True,
            })
        for row in _RESERVED.values():
            rows.append({**row, "applies_to": list(row["applies_to"])})
        rows.sort(key=lambda r: (r["domain"], r["operation"]))
        return rows

    @classmethod
    def count(cls) -> int:
        return len(_REGISTRY) + len(_RESERVED)
