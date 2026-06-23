"""
PyPI Detector.

PyPI tokens are Macaroon tokens starting with "pypi-" followed by base64-encoded
content. Minimum real token length is ~170 chars; we match 100+ to be safe.
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


@register_detector
class PyPIDetector(BaseDetector):
    CATEGORY           = "PyPI"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "PyPI package upload tokens (pypi- prefix)"
    DOMAIN             = "Developer Tools"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Upload Token",
            label="PYPI_UPLOAD_TOKEN",
            severity="HIGH",
            detection="prefix",
            # Full macaroon prefix: pypi-AgEIcHlwaS5vcmc is the base64 encoding
            # of the "pypi.org" location identifier present in ALL PyPI tokens.
            # Using the full prefix eliminates false positives from any string
            # that merely starts with "pypi-".
            pattern=BaseDetector.prefix_pattern(
                "pypi-AgEIcHlwaS5vcmc", r"[a-zA-Z0-9\-_]", 50, 1000, word_boundary=False
            ),
            description="PyPI API token - used to upload Python packages to PyPI or TestPyPI",
            example="pypi-AgEIcHlwaS5vcmc[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["pypi-AgEIcHlwaS5vcmc"]
