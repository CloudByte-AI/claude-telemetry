"""
Text masker — replaces detected secret values with [REDACTED:LABEL] tags.
Processes longest matches first to avoid partial replacement issues.
Idempotent: already-redacted placeholders are never double-redacted.
"""

from src.security.detectors.base import Finding


def mask_text(text: str, findings: list[Finding]) -> str:
    """
    Apply all findings to text, replacing each secret value with its tag.
    Returns the fully masked string.
    """
    if not findings or not text:
        return text

    # Longest secret values first — avoids a short value masking part of a longer one
    ordered = sorted(
        [f for f in findings if f.secret_value],
        key=lambda f: len(f.secret_value),
        reverse=True,
    )

    result = text
    for finding in ordered:
        sv = finding.secret_value
        if sv and sv in result and not sv.startswith("[REDACTED:"):
            result = result.replace(sv, finding.masked_value)

    return result
