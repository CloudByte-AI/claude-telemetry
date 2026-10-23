"""
Security matchers.

Deliberately narrow. `sec.credential.read` fires on commands whose whole purpose
is to print a secret - `aws sts get-session-token`, `gcloud auth print-access-token`
- rather than on anything that merely touches a credentials file, which is what
`fs.sensitive` already covers.
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall


# A secret's value fields in a kubectl output expression. Annotations count:
# `kubectl apply` keeps the whole manifest, data included, in the
# last-applied-configuration annotation.
_DATA_FIELD = re.compile(r"(?<![A-Za-z0-9_])(?:data|stringData|annotations)\b|last-applied", re.IGNORECASE)


@register_matcher
class CredentialReadMatcher(BaseMatcher):
    OPERATION = "sec.credential.read"
    DOMAIN = "sec"
    DESCRIPTION = "Reads or prints a credential, token or secret value"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("provider",)

    # command name -> token runs whose purpose is to emit a secret
    _SECRET_COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
        "aws": (("sts", "get-session-token"), ("secretsmanager", "get-secret-value"),
                ("ssm", "get-parameter"), ("ecr", "get-login-password")),
        "gcloud": (("auth", "print-access-token"), ("auth", "print-identity-token"),
                   ("secrets", "versions", "access")),
        "az": (("account", "get-access-token"), ("keyvault", "secret", "show")),
        "kubectl": (("get", "secret"), ("get", "secrets")),
        "vault": (("read",), ("kv", "get")),
        "doppler": (("secrets", "download"),),
        "op": (("read",), ("item", "get")),
        "gh": (("auth", "token"),),
        "heroku": (("config",),),
    }
    # `docker login` stores a credential (its own operation) but prints none.

    # Anchored at the subcommand for these, after any global options that take
    # a value; aws/gcloud/az runs are specific enough to find anywhere.
    _ANCHORED: dict[str, frozenset[str]] = {
        "kubectl": frozenset({"-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user",
                              "-s", "--server", "--token", "--as", "--as-group"}),
        "vault": frozenset({"-address", "-namespace"}),
        "op": frozenset({"--account", "--session", "--config"}),
        "doppler": frozenset({"-p", "--project", "-c", "--config", "--token"}),
        "gh": frozenset({"-R", "--repo", "--hostname"}),
        "heroku": frozenset(),
    }
    # `kubectl get secret` alone lists names, types and key counts; only an
    # output format prints the (base64) values.
    _QUIET_OUTPUTS = frozenset({"name", "wide"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            runs = self._SECRET_COMMANDS.get(command.command_name)
            if not runs:
                continue
            value_options = self._ANCHORED.get(command.command_name)
            path = (self.operands_from_subcommand(command, value_options)
                    if value_options is not None else command.subcommand_path)
            for run in runs:
                found = (self.starts_with_run(path, run) if value_options is not None
                         else self.has_token_run(path, run))
                if not found:
                    continue
                if command.command_name == "kubectl" and not self._prints_values(command):
                    continue
                return self.hit(
                    target=f"{command.command_name} {' '.join(run)}",
                    command=command,
                    provider=command.command_name,
                )
        return None

    # Formats that print what an expression selects. They print secret values
    # only when it selects the data (`{.data.password}`), not names or types
    # (`custom-columns=NAME:.metadata.name`); an expression kept in a file
    # cannot be read here, so it counts.
    _SELECTING_OUTPUTS = ("custom-columns", "jsonpath", "go-template")

    def _prints_values(self, command) -> bool:
        """`-o yaml|json`, or an expression output that selects the secret's data."""
        argv = command.argv
        for i, token in enumerate(argv):
            if token in ("-o", "--output"):
                value = argv[i + 1] if i + 1 < len(argv) else ""
            elif token.startswith(("-o=", "--output=")):
                value = token.split("=", 1)[1]
            elif token.startswith("-o") and len(token) > 2 and not token.startswith("--"):
                value = token[2:]
            else:
                continue
            kind, _, expression = value.partition("=")
            kind = kind.lower()
            if kind in self._QUIET_OUTPUTS:
                continue
            if kind.startswith(self._SELECTING_OUTPUTS) and not kind.endswith("-file"):
                # `.data` / `.stringData` as a field, not inside `.metadata`.
                selects_data = bool(_DATA_FIELD.search(expression)) or expression.strip() in ("", "{}", "{.}", "{@}")
                if not selects_data:
                    continue
            return True
        return False


# Shell-history variables that, emptied or zeroed, stop history being kept.
_HISTORY_VARS = ("HISTFILE", "HISTSIZE", "HISTFILESIZE", "SAVEHIST")
# `HISTFILE=/dev/null`, `HISTFILE=`, `HISTSIZE=0` at the start of a statement or
# after export/declare/typeset. An assignment alone is not a parsed command, so
# this reads the call's text (here-document bodies removed).
_HISTORY_OFF = re.compile(
    r"(?:^|[\n;&|(]\s*|\b(?:export|declare\s+-x|typeset\s+-x)\s+)"
    r"(HISTFILE=(?:/dev/null\b|\"\"|''|(?=[\s;&|)]|$))|(?:HISTSIZE|HISTFILESIZE|SAVEHIST)=0\b)"
)
_LOG_SERVICES = frozenset({"auditd", "rsyslog", "syslog", "syslog-ng", "systemd-journald", "journald", "eventlog"})
_SERVICE_STOPS = frozenset({"stop", "disable", "mask", "kill"})


def _audit_off(command) -> tuple[str, str] | None:
    """(target, technique) when this command turns off or wipes history, auditing or logs."""
    name = command.command_name.lower()
    operands = list(command.subcommand_path)
    lowered = [o.lower() for o in operands]
    argv = command.argv
    if name == "unset" and any(o in _HISTORY_VARS for o in operands):
        return "shell_history", "unset"
    if name in ("export", "declare", "typeset") and any(_HISTORY_OFF.match(o) for o in operands):
        return "shell_history", "disable"
    if name == "set" and "+o" in operands and "history" in operands:
        return "shell_history", "disable"
    if name == "history" and "-c" in command.flags:
        return "shell_history", "clear"
    if name == "set-psreadlineoption" and "savenothing" in lowered:
        return "shell_history", "disable"
    if name == "clear-history":
        return "shell_history", "clear"
    if name == "auditctl" and ("-D" in argv or any(a == "-e" and b == "0" for a, b in zip(argv, argv[1:]))):
        return "audit", "disable"
    if name == "auditpol" and any(o in ("/clear", "/remove") or o.endswith(":disable") for o in lowered):
        return "audit", "disable"
    if name == "systemctl" and lowered and lowered[0] in _SERVICE_STOPS \
            and any(o.removesuffix(".service") in _LOG_SERVICES for o in lowered[1:]):
        return "system_log", "stop"
    if name == "service" and len(lowered) >= 2 and lowered[0] in _LOG_SERVICES and lowered[1] in _SERVICE_STOPS:
        return "system_log", "stop"
    # Stop-Service / Set-Service resolve to systemctl; the cmdlet as typed says which.
    program = command.raw.split(None, 1)[0].lower() if command.raw else ""
    if program in ("stop-service", "spsv") and "eventlog" in lowered:
        return "system_log", "stop"
    if program == "set-service" and "eventlog" in lowered and "disabled" in lowered:
        return "system_log", "stop"
    if name in ("wevtutil", "wevtutil.exe") and lowered and lowered[0] in ("cl", "clear-log"):
        return "system_log", "clear"
    if name in ("clear-eventlog", "remove-eventlog"):
        return "system_log", "clear"
    if name == "journalctl" and any(a.startswith("--vacuum") for a in argv):
        return "system_log", "clear"
    return None


@register_matcher
class AuditDisableMatcher(BaseMatcher):
    OPERATION = "sec.audit.disable"
    DOMAIN = "sec"
    DESCRIPTION = "Disables logging, history or auditing"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # area: shell_history, audit or system_log. technique: unset, disable,
    # clear or stop. Reading a history setting (`echo $HISTFILE`) is not this.
    FACTS = ("area", "technique")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _audit_off(command)
            if found:
                area, technique = found
                return self.hit(target=area, command=command, area=area, technique=technique)
        text = call.command or ""
        for command in call.commands:
            if command.stdin:
                text = text.replace(command.stdin, "")
        found = _HISTORY_OFF.search(text)
        if found:
            return self.hit(target="shell_history", evidence=found.group(0).strip(),
                            area="shell_history", technique="disable")
        return None
