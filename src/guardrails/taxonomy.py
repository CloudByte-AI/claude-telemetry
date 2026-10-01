"""
taxonomy.json generator.

The operation vocabulary is a public interface. This generates the committed
file from the registry, and a CI test asserts the file matches, so the
interface cannot silently drift.

Regenerate after adding or reserving an operation:

    uv run --no-sync python -m src.guardrails.taxonomy
"""

from __future__ import annotations

import json
from pathlib import Path

TAXONOMY_PATH = Path(__file__).parent / "taxonomy.json"

# Bumped when an operation is RENAMED or REMOVED - both break existing
# profiles. Adding one is additive and does not bump it.
TAXONOMY_VERSION = 1


def build() -> dict:
    """The taxonomy as a plain dict, generated from the live registry."""
    import src.guardrails.matchers  # noqa: F401  - triggers registration
    from src.guardrails.registry import MatcherRegistry

    operations = MatcherRegistry.describe()
    for row in operations:
        row.setdefault("facts", [])

    # Each matcher's declared facts: the extra fields a rule may scope on.
    for row in operations:
        matcher_cls = MatcherRegistry.get(row["operation"])
        if matcher_cls is not None:
            row["facts"] = sorted(getattr(matcher_cls, "FACTS", ()) or ())

    implemented = sum(1 for row in operations if row["implemented"])
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "generated_from": "src/guardrails/registry.py - DO NOT HAND-EDIT",
        "operation_count": len(operations),
        "implemented_count": implemented,
        "reserved_count": len(operations) - implemented,
        "operations": operations,
    }


def write(path: Path | None = None) -> Path:
    target = path or TAXONOMY_PATH
    target.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    return target


def read(path: Path | None = None) -> dict:
    target = path or TAXONOMY_PATH
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}


if __name__ == "__main__":
    written = write()
    data = build()
    print(
        f"wrote {written}\n"
        f"  {data['operation_count']} operations "
        f"({data['implemented_count']} implemented, {data['reserved_count']} reserved)"
    )
