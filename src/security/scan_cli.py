"""
Command-line secret scan: text on stdin, one JSON verdict on stdout.

    uv run --no-sync --directory <plugin root> -m src.security.scan_cli [--strict]

For callers outside the hooks that need to know whether a piece of text is
safe to send somewhere before they send it. It uses the same detectors and the
same ~/.cloudbyte/security/security_profile.yaml as the prompt guardrail.
stdout carries nothing but this object:

    {"status": "ok" | "defaults" | "fallback" | "error",
     "complete": bool,
     "incomplete_reasons": [str, ...],
     "blocked": bool | null,
     "categories": [str, ...],
     "profile": str | null}

status
    ok        the profile loaded as written and was used
    defaults  there is no profile; the detectors that are on by default were used
    fallback  the profile exists but cannot be used as written, so the hooks'
              fallback was used instead (without --strict only; complete is false)
    error     nothing was scanned: --strict and the profile cannot be used as
              written, or the scan itself failed
complete
    false when any part of the scan was skipped; incomplete_reasons says what
    ("detector_error:AWS", "pattern_timeout:<name>", "filtered_strategy",
    "profile_fallback", or on error the cause, e.g. "profile_unreadable")
blocked
    true when anything was found - the rule the prompt guardrail blocks on.
    null when nothing was scanned
categories
    the distinct categories found. Matched values are never printed
profile
    the profile's plan ("standard", ...), "default" without a profile, null on error

The profile's own `enabled` switch is not consulted: it turns the prompt
guardrail on and off, and says nothing about whether a given text is clean.

Exit status: 0 with a verdict, 1 when the scan failed, 2 for usage errors,
3 when --strict refused the profile.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.common.logging import get_logger, setup_logging
from src.security import config as security_config
from src.security.config import SecurityConfigError, load_security_config

logger = get_logger(__name__)

EXIT_OK = 0
EXIT_SCAN_FAILED = 1
EXIT_STRICT_REFUSED = 3


def _error(reason: str) -> dict:
    return {
        "status": "error",
        "complete": False,
        "incomplete_reasons": [reason],
        "blocked": None,
        "categories": [],
        "profile": None,
    }


def scan(text: str, strict: bool) -> tuple[dict, int]:
    """Scan `text` and return (verdict, exit status). Never raises."""
    has_profile = security_config.USER_CONFIG_PATH.exists()
    reasons: list[str] = []
    try:
        cfg = load_security_config(strict=True)
        status = "ok" if has_profile else "defaults"
    except SecurityConfigError as exc:
        # The detail stays in the log: a YAML error can quote a profile line,
        # and the profile's allowlist holds real secret values.
        logger.warning(f"scan_cli: profile refused ({exc.reason}): {exc}")
        if strict:
            return _error(exc.reason), EXIT_STRICT_REFUSED
        cfg = load_security_config()
        status = "fallback"
        reasons.append("profile_fallback")

    try:
        from src.security.scanner import scan_text
        result = scan_text(text, cfg.prompt_config)
    except Exception as exc:
        logger.error(f"scan_cli: scan failed: {exc}")
        return _error("scan_failed"), EXIT_SCAN_FAILED

    reasons.extend(r for r in result.incomplete_reasons if r not in reasons)
    return {
        "status": status,
        "complete": not reasons,
        "incomplete_reasons": reasons,
        "blocked": bool(result.findings),
        "categories": sorted({f.category for f in result.findings}),
        "profile": cfg.plan if has_profile else "default",
    }, EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.security.scan_cli",
        description="Scan text on stdin for secrets and print a JSON verdict.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="refuse to scan (exit 3) when the security profile cannot be used exactly as written",
    )
    args = parser.parse_args(argv)

    try:
        setup_logging(log_level="WARNING", log_to_file=True, log_to_console=False)
    except Exception:
        # No log directory: stay silent rather than write to stdout or stderr.
        logging.getLogger().addHandler(logging.NullHandler())

    # Bytes, not text mode: Windows decodes text-mode stdin with the console code page.
    text = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace")
    verdict, code = scan(text, strict=args.strict)
    sys.stdout.write(json.dumps(verdict) + "\n")
    sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
