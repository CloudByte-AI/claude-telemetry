"""
HTTP client for pushing telemetry data to the CloudByte central server.

Uses only Python stdlib (urllib) — no extra dependencies needed.
"""

import hashlib
import json
import platform
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from src.common.logging import get_logger

logger = get_logger(__name__)

_VERSION = "0.1.4"


def _machine_id() -> str:
    try:
        hostname = socket.gethostname()
        return hashlib.sha256(hostname.encode()).hexdigest()[:16]
    except Exception:
        return "unknown"


def _strip_internal(rows: list[dict]) -> list[dict]:
    """Remove internal _rowid key before sending."""
    return [{k: v for k, v in r.items() if k != "_rowid"} for r in rows]


def build_payload(data: dict) -> dict:
    return {
        "sentAt":        datetime.now(timezone.utc).isoformat(),
        "agentVersion":  _VERSION,
        "machineId":     _machine_id(),
        "osPlatform":    platform.system().lower(),
        "sessions":      _strip_internal(data.get("sessions",    [])),
        "prompts":       _strip_internal(data.get("prompts",     [])),
        "responses":     _strip_internal(data.get("responses",   [])),
        "io_tokens":     _strip_internal(data.get("io_tokens",   [])),
        "tool_calls":    _strip_internal(data.get("tool_calls",  [])),
        "tool_tokens":   _strip_internal(data.get("tool_tokens", [])),
        "thinking":      _strip_internal(data.get("thinking",    [])),
        "observations":  _strip_internal(data.get("observations",[])),
        "summaries":     _strip_internal(data.get("summaries",   [])),
    }


def post_telemetry(central_url: str, api_key: str, payload: dict) -> dict:
    """
    POST payload to {central_url}/ingest/telemetry.

    Returns:
        dict with keys: ok (bool), status (int), body (dict|str)
    """
    url = central_url.rstrip("/") + "/ingest/telemetry"
    body = json.dumps(payload, default=str).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent":    f"CloudByte-Plugin/{_VERSION}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
            raw = resp.read().decode("utf-8")
            try:
                body_parsed = json.loads(raw)
            except Exception:
                body_parsed = raw
            logger.info(f"Telemetry sync: {status} — {body_parsed}")
            return {"ok": status in (200, 207), "status": status, "body": body_parsed}

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        logger.warning(f"Telemetry sync HTTP error {e.code}: {raw[:200]}")
        return {"ok": False, "status": e.code, "body": raw}

    except Exception as e:
        logger.warning(f"Telemetry sync failed: {e}")
        return {"ok": False, "status": 0, "body": str(e)}
