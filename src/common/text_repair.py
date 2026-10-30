"""
Repair of garbled prompt text (mojibake, such as UTF-8 decoded as cp1252).

fix_text() uses the ftfy package when it can be imported. When it cannot, the
text is returned unchanged and a background package repair is requested, so a
missing package never stops a hook.
"""

from __future__ import annotations

import functools
from typing import Callable


@functools.cache
def _ftfy_fix_text() -> Callable[[str], str] | None:
    """ftfy.fix_text, or None when ftfy cannot be imported (reported once per process)."""
    try:
        from ftfy import fix_text
    except Exception as exc:  # ImportError, or a damaged install
        from src.common.package_repair import report_missing

        report_missing("ftfy", exc)
        return None
    return fix_text


def fix_text(text: str) -> str:
    """`text` with mojibake repaired, or unchanged when ftfy is unavailable or text is empty."""
    fixer = _ftfy_fix_text()
    return fixer(text) if fixer is not None and text else text
