"""
MCP matchers.

An MCP tool is opaque, so these match on the verb in the tool name, which is a
convention rather than a guarantee - `create_issue` writes, `get_file` reads.
Reported as a fact (`verb`) so a rule author can see why it fired.

`mcp.ungoverned` ("not on the allowlist") is deliberately NOT a matcher: a
matcher sees only the ToolCall, never the profile. An allowlist is written as
rules, narrower allow first:

    - { mcp_tool: {equals: read_file},  decision: allow }
    - { kind: mcp,                      decision: ask, reason: "MCP tool not allow-listed" }
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_MCP, ToolCall

_WRITE_VERBS = (
    "create", "update", "write", "set", "add", "post", "put", "patch",
    "insert", "upload", "send", "publish", "merge", "push", "edit", "modify",
)
_DELETE_VERBS = ("delete", "remove", "destroy", "drop", "purge", "truncate", "revoke")


# A tool whose name STARTS with one of these reads: `get_merge_request`,
# `list_push_rules`, `get_post_comments` carry a write word but write nothing.
_READ_VERBS = frozenset({
    "get", "list", "read", "search", "query", "fetch", "find", "describe", "show", "count",
    "view", "check", "lookup", "browse", "inspect", "preview", "download", "export", "status",
})


def _verb_in(tool: str | None, verbs: tuple[str, ...]) -> str | None:
    """
    The verb found in a tool name, matched on whole name segments so that
    `undelete_file` does not read as a delete.
    """
    if not tool:
        return None
    segments = tool.replace("-", "_").lower().split("_")
    if segments and segments[0] in _READ_VERBS:
        return None
    for verb in verbs:
        if verb in segments:
            return verb
    return None


@register_matcher
class McpWriteMatcher(BaseMatcher):
    OPERATION = "mcp.write"
    DOMAIN = "mcp"
    DESCRIPTION = "MCP tool whose name indicates it writes or sends data"
    APPLIES_TO = (KIND_MCP,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    FACTS = ("verb", "mcp_server")

    def match(self, call: ToolCall) -> MatchResult | None:
        verb = _verb_in(call.mcp_tool, _WRITE_VERBS)
        if not verb:
            return None
        return self.hit(
            target=call.tool_name,
            evidence=call.tool_name,
            verb=verb,
            mcp_server=call.mcp_server,
        )


@register_matcher
class McpDeleteMatcher(BaseMatcher):
    OPERATION = "mcp.delete"
    DOMAIN = "mcp"
    DESCRIPTION = "MCP tool whose name indicates it deletes data"
    APPLIES_TO = (KIND_MCP,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("verb", "mcp_server")

    def match(self, call: ToolCall) -> MatchResult | None:
        verb = _verb_in(call.mcp_tool, _DELETE_VERBS)
        if not verb:
            return None
        return self.hit(
            target=call.tool_name,
            evidence=call.tool_name,
            verb=verb,
            mcp_server=call.mcp_server,
        )


# Words that name a credential on their own, and two-word names. A bare
# `token` is not one: `count_tokens` counts model tokens, `next_page_token`
# pages. `access_token`, `api_key`, `client_secret` are.
_CREDENTIAL_WORDS = frozenset({
    "secret", "secrets", "credential", "credentials", "password", "passwords", "passwd",
    "apikey", "apikeys", "privatekey", "keychain", "passphrase",
})
_CREDENTIAL_PAIRS = frozenset({
    ("api", "key"), ("api", "keys"), ("api", "token"), ("access", "token"), ("access", "tokens"),
    ("refresh", "token"), ("auth", "token"), ("bearer", "token"), ("session", "token"),
    ("oauth", "token"), ("id", "token"), ("personal", "access"), ("private", "key"),
    ("ssh", "key"), ("ssh", "keys"), ("signing", "key"), ("client", "secret"), ("access", "key"),
    ("secret", "key"), ("connection", "string"),
})
# Verbs that hand a value back.
_CREDENTIAL_READ_VERBS = frozenset({
    "get", "read", "fetch", "retrieve", "reveal", "show", "export", "view", "decrypt",
    "copy", "download", "print", "lookup", "list", "describe", "find", "search", "load",
})
# MCP servers that ARE a secret store: any read from them is a credential read.
_SECRET_STORES = frozenset({
    "1password", "onepassword", "op", "bitwarden", "bw", "lastpass", "keeper", "vault",
    "hashicorpvault", "doppler", "infisical", "akeyless", "conjur", "keychain",
    "secretsmanager", "awssecretsmanager", "secretmanager", "gcpsecretmanager",
    "keyvault", "azurekeyvault",
})


def _server_key(server: str | None) -> str:
    """`mcp__1password__` -> 1password; `mcp__plugin_x_vault__` -> vault; letters and digits only."""
    name = (server or "").lower()
    name = name.removeprefix("mcp__").removesuffix("__")
    if name.startswith("plugin_"):
        name = name.rsplit("_", 1)[-1]
    name = re.sub(r"[^a-z0-9]", "", name)
    # `vault-mcp-server`, `1password-mcp`, `mcp-bitwarden` name the store.
    for affix in ("mcpserver", "server", "mcp"):
        name = name.removesuffix(affix)
    return name.removeprefix("mcp")


def _name_words(name: str | None) -> list[str]:
    """`getAPIKey`, `get_api_key`, `get-api-key` -> ['get', 'api', 'key']."""
    if not name:
        return []
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return [w for w in re.split(r"[\s_.\-:/]+", spaced.lower()) if w]


# A word after the credential that makes the name ABOUT credentials rather than
# one: `secret_scanning_alerts`, `password_policy`, `api_key_usage`.
_ABOUT_WORDS = frozenset({
    "scanning", "scan", "scans", "alert", "alerts", "policy", "policies", "rotation", "metadata",
    "settings", "requirements", "strength", "usage", "count", "expiry", "expiration", "names",
    "status", "audit", "history", "rules", "types", "schema",
})


def _credential_named(words: list[str]) -> str | None:
    for i, word in enumerate(words):
        if word in _CREDENTIAL_WORDS and (i + 1 >= len(words) or words[i + 1] not in _ABOUT_WORDS):
            return word
    for i, pair in enumerate(zip(words, words[1:])):
        if pair in _CREDENTIAL_PAIRS and (i + 2 >= len(words) or words[i + 2] not in _ABOUT_WORDS):
            return "_".join(pair)
    return None


@register_matcher
class McpCredentialAccessMatcher(BaseMatcher):
    OPERATION = "mcp.credential_access"
    DOMAIN = "mcp"
    DESCRIPTION = "MCP tool that reads credentials"
    APPLIES_TO = (KIND_MCP,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # credential: the credential word in the tool name, or `store` when the
    # server itself is a secret store (1Password, Vault...).
    FACTS = ("credential", "verb", "mcp_server")

    def match(self, call: ToolCall) -> MatchResult | None:
        words = _name_words(call.mcp_tool)
        verb = next((w for w in words if w in _CREDENTIAL_READ_VERBS), None)
        if verb is None:
            return None
        credential = _credential_named(words)
        if credential is None and _server_key(call.mcp_server) in _SECRET_STORES:
            credential = "store"
        if credential is None:
            return None
        return self.hit(target=call.tool_name, evidence=call.tool_name,
                        credential=credential, verb=verb, mcp_server=call.mcp_server)
