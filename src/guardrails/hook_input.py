"""
Hook input - the one way a guardrail entry point turns a payload into a dict.

Cursor's hook runner prefixes stdin with a UTF-8 byte-order mark, which makes
json.loads fail, and an entry point that answers unreadable input with "no
opinion" would then allow every call unchecked.

So input is read as BYTES and decoded with utf-8-sig, which strips a leading BOM
and is identical to utf-8 otherwise. The same decoding serves stdin (command
hooks) and a request body (the HTTP transport), so the two can never disagree.

Standard library only: this runs in front of every governed tool call.
"""

from __future__ import annotations

import json
import sys

_BOM = "﻿"


class HookInputError(ValueError):
    """The hook input could not be read or is not JSON. The message says why."""


def parse_payload(raw: bytes | bytearray | str | None) -> dict:
    """
    Decode one hook payload. Empty input, or JSON that is not an object, is {}.

    Undecodable bytes are replaced rather than rejected: a command containing
    one stray byte should still be evaluated, not waved through unchecked.
    Raises HookInputError when the text is not JSON at all.
    """
    if isinstance(raw, (bytes, bytearray)):
        text = bytes(raw).decode("utf-8-sig", errors="replace")
    else:
        text = raw or ""
    text = text.lstrip(_BOM).strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except ValueError as exc:
        raise HookInputError(f"not valid JSON ({exc})") from None
    return payload if isinstance(payload, dict) else {}


def read_stdin_payload() -> dict:
    """
    Read and decode the payload on stdin. Raises HookInputError if it cannot.

    Reads the underlying byte stream when there is one: text-mode stdin uses the
    platform's default encoding (cp1252 on Windows), which turns a BOM into
    three stray characters no JSON parser accepts.
    """
    stream = sys.stdin
    buffer = getattr(stream, "buffer", None)
    try:
        raw = buffer.read() if buffer is not None else stream.read()
    except Exception as exc:
        raise HookInputError(f"could not read stdin ({type(exc).__name__}: {exc})") from None
    return parse_payload(raw)
