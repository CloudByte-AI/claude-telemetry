"""
The matcher library.

Importing this package registers every matcher. One module per domain;
matchers self-register by decorator, so adding one needs no registration step
anywhere else. Import order is not significant: the registry keys on OPERATION
and rejects duplicates.
"""

from src.guardrails.matchers import (  # noqa: F401
    db,
    devflow,
    exec,
    fs,
    infra,
    mcp,
    net,
    sec,
    sys,
    vcs,
)

# Taxonomy names with no implementation yet. Imported last so a name that later
# gains a matcher fails loudly as a duplicate rather than silently shadowing it.
from src.guardrails.matchers import reserved  # noqa: F401

__all__ = [
    "db", "devflow", "exec", "fs", "infra", "mcp", "net", "sec", "sys", "vcs",
    "reserved",
]
