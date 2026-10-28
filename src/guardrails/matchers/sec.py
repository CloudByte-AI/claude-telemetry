"""
Security matchers.

Deliberately narrow. `sec.credential.read` fires on commands whose whole purpose
is to print a secret - `aws sts get-session-token`, `gcloud auth print-access-token`
- rather than on anything that merely touches a credentials file, which is what
`fs.sensitive` already covers.
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult, option_of, remote_runs, summary_value
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall

# Where a secret is read from, for summaries.
_SECRET_STORES = {"aws": "AWS", "az": "Azure", "gcloud": "Google Cloud", "kubectl": "Kubernetes",
                  "oc": "OpenShift", "vault": "Vault", "doppler": "Doppler", "op": "1Password",
                  "bw": "Bitwarden", "infisical": "Infisical"}
# Options naming the secret itself (never one carrying a value).
_SECRET_NAME_OPTIONS = ("--secret-id", "--secret", "--name", "--names", "--path")
# kubectl options that take a value, skipped when finding the secret's name.
_KUBE_VALUE_OPTIONS = frozenset({"-n", "--namespace", "-o", "--output", "-l", "--selector", "--context",
                                 "--kubeconfig", "--field-selector", "--template"})


def _secret_name(result: MatchResult) -> str | None:
    """The secret a read names (`--secret-id prod/db`, `kubectl get secret db`), or None."""
    command, provider = result.command, result.facts.get("provider")
    if command is None or provider not in _SECRET_STORES:
        return None
    if provider in ("aws", "az", "gcloud"):
        return summary_value(option_of(command, *_SECRET_NAME_OPTIONS))
    argv = list(command.argv)
    if provider in ("kubectl", "oc"):
        start = next((i for i, token in enumerate(argv) if token.lower() in ("secret", "secrets")), None)
        if start is None:
            return None
        skip = False
        for token in argv[start + 1:]:
            if skip:
                skip = False
                continue
            if token.startswith("-"):
                skip = "=" not in token and token.lower() in _KUBE_VALUE_OPTIONS
                continue
            return summary_value(token)
        return None
    operands = [token for token in argv if not token.startswith("-")]
    return summary_value(operands[-1]) if len(operands) >= 2 else None


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
    # provider: the tool that prints it (aws, az, kubectl, git...), or
    # `environment` for a dump of every environment variable. scope: one
    # (a named secret or token) or all (every variable at once). filtered: the
    # dump is piped through a filter (grep, findstr, Select-String), so only
    # matching variables are printed.
    FACTS = ("provider", "scope", "filtered")

    # command name -> token runs whose purpose is to emit a secret
    _SECRET_COMMANDS: dict[str, tuple[tuple[str, ...], ...]] = {
        "aws": (("sts", "get-session-token"), ("secretsmanager", "get-secret-value"),
                ("ssm", "get-parameter"), ("ecr", "get-login-password"), ("ssm", "get-parameters"),
                ("ssm", "get-parameters-by-path"), ("sts", "assume-role"), ("sts", "get-federation-token"),
                ("sts", "assume-role-with-web-identity"), ("sts", "assume-role-with-saml"),
                ("configure", "export-credentials"), ("kms", "decrypt"), ("iam", "create-access-key"),
                ("ecr-public", "get-login-password"), ("ecr", "get-authorization-token"),
                ("codeartifact", "get-authorization-token"), ("rds", "generate-db-auth-token"),
                ("sso", "get-role-credentials"), ("lambda", "get-function-configuration")),
        "gcloud": (("auth", "print-access-token"), ("auth", "print-identity-token"),
                   ("secrets", "versions", "access"), ("auth", "application-default", "print-access-token")),
        "az": (("account", "get-access-token"), ("keyvault", "secret", "show"), ("keyvault", "secret", "download"),
               ("storage", "account", "keys", "list"), ("storage", "account", "show-connection-string"),
               ("storage", "account", "generate-sas"), ("storage", "container", "generate-sas"),
               ("storage", "blob", "generate-sas"), ("acr", "credential", "show"), ("redis", "list-keys"),
               ("redisenterprise", "database", "list-keys"), ("cosmosdb", "keys", "list"),
               ("webapp", "config", "appsettings", "list"), ("functionapp", "config", "appsettings", "list"),
               ("webapp", "deployment", "list-publishing-credentials"),
               ("webapp", "deployment", "list-publishing-profiles"), ("containerapp", "secret", "show"),
               ("containerapp", "secret", "list"), ("cognitiveservices", "account", "keys", "list"),
               ("search", "admin-key", "show"), ("appconfig", "credential", "list"),
               ("servicebus", "namespace", "authorization-rule", "keys", "list"),
               ("eventhubs", "namespace", "authorization-rule", "keys", "list")),
        "kubectl": (("get", "secret"), ("get", "secrets"), ("config", "view"), ("create", "token")),
        "vault": (("read",), ("kv", "get")),
        "doppler": (("secrets", "download"), ("secrets", "get")),
        "op": (("read",), ("item", "get")),
        "gh": (("auth", "token"), ("auth", "status")),
        "heroku": (("config",), ("config:get",), ("auth:token",)),
        "supabase": (("projects", "api-keys"),),
        "vercel": (("env", "pull"),),
        "railway": (("variables",),),
        "bw": (("get",),),
        "infisical": (("secrets",), ("export",)),
        "git": (("credential", "fill"),),
        "docker": (("inspect",),),
        "podman": (("inspect",),),
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
        "supabase": frozenset({"--workdir", "--profile"}),
        "vercel": frozenset({"--cwd", "--scope", "--token", "-t", "-A", "--local-config"}),
        "railway": frozenset(),
        "bw": frozenset({"--session"}),
        "infisical": frozenset({"--domain", "--token"}),
        "git": frozenset({"-C", "-c", "--git-dir", "--work-tree"}),
        "docker": frozenset({"-H", "--host", "--context", "--config"}),
        "podman": frozenset({"--url", "--connection"}),
    }
    # `kubectl get secret` alone lists names, types and key counts; only an
    # output format prints the (base64) values.
    _QUIET_OUTPUTS = frozenset({"name", "wide"})
    # A `--query` (aws, az) that selects none of these prints names or ids, not a secret.
    _VALUE_WORDS = re.compile(
        r"value|key|password|secret|token|connection|credential|sas|primary|secondary|env|data|string|plaintext",
        re.IGNORECASE)
    # aws configure get <name>: only these names are secrets.
    _SECRET_SETTING = re.compile(r"secret|session_token|access_key", re.IGNORECASE)

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if command.command_name == "aws" and self._aws_configure_get(command):
                return self.hit(target="aws configure get", command=command, provider="aws",
                                scope="one", filtered=False)
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
                if not self._prints(command, run):
                    continue
                return self.hit(
                    target=f"{command.command_name} {' '.join(run)}",
                    command=command,
                    provider=command.command_name,
                    scope="one",
                    filtered=False,
                )
        for run in remote_runs(call):
            for inner in run.commands:
                if self._dumps_environment(inner) or inner.command_name == "printenv":
                    return self.hit(target=f"{run.wrapper.command_name} exec {inner.command_name}",
                                    command=run.wrapper, provider=run.wrapper.command_name,
                                    scope="all" if not inner.subcommand_path else "one",
                                    filtered=self._filtered(run.commands, inner) or self._filtered(call.commands,
                                                                                                    run.wrapper))
        for command in call.commands:
            if self._dumps_environment(command):
                return self.hit(target="environment", command=command, provider="environment", scope="all",
                                filtered=self._filtered(call.commands, command))
        return None

    def _prints(self, command, run) -> bool:
        """Whether this run, with these options, prints the secret rather than a name or an id."""
        name = command.command_name
        argv = command.argv
        if name == "kubectl":
            if run == ("config", "view"):
                return bool({"--raw", "--flatten"} & set(argv))
            if run == ("create", "token"):
                return True
            return self._prints_values(command)
        if name == "gh" and run == ("auth", "status"):
            return bool({"--show-token", "-t"} & set(argv))
        if name in ("docker", "podman"):
            template = self._value(argv, ("-f", "--format"))
            return bool(template and re.search(r"\.Config\.Env|\.Env\b", template))
        if run == ("containerapp", "secret", "list"):
            return "--show-values" in argv
        if name in ("aws", "az"):
            query = self._value(argv, ("--query",))
            if query is not None and not self._VALUE_WORDS.search(query):
                return False
        return True

    @staticmethod
    def _value(argv, names: tuple[str, ...]) -> str | None:
        for i, token in enumerate(argv):
            option, eq, value = token.partition("=")
            if option in names:
                return value if eq else (argv[i + 1] if i + 1 < len(argv) else None)
        return None

    def _aws_configure_get(self, command) -> bool:
        path = self.operands_from_subcommand(command, frozenset({"--profile", "--region"}))
        return self.starts_with_run(path, ("configure", "get")) and len(path) > 2 \
            and bool(self._SECRET_SETTING.search(path[2]))

    @staticmethod
    def _dumps_environment(command) -> bool:
        """`env`, `printenv`, `export -p`, `Get-ChildItem env:` with nothing narrowing them."""
        name = command.command_name.lower()
        operands = command.subcommand_path
        if name in ("env", "printenv") and not operands and not command.flags - {"-0", "--null"}:
            return True
        if name in ("export", "declare") and not operands and command.flags & {"-p", "-x"}:
            return True
        if name in ("get-childitem", "gci", "dir", "ls") and len(operands) == 1 \
                and operands[0].lower().rstrip("\\/") in ("env:", "env:*"):
            return True
        return False

    @staticmethod
    def _filtered(commands, command) -> bool:
        """Whether a later command in the same pipeline filters or cuts what this one prints."""
        name = command.command_name
        return any(name in later.piped_from and later.command_name.lower() in (
            "grep", "egrep", "rg", "findstr", "select-string", "sls", "awk", "sed", "head", "where-object",
            "cut", "sort", "wc") for later in commands)

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        if facts.get("provider") == "environment":
            return "print environment variables" + (" (filtered)" if facts.get("filtered") else " (all of them)")
        name = _secret_name(result)
        if not name and str(result.target).endswith((" get secret", " get secrets")):
            namespace = summary_value(option_of(result.command, "-n", "--namespace"))
            return f"print every secret{f' in namespace {namespace}' if namespace else ''} (Kubernetes)"
        if not name:
            return f"print a secret ({result.target})"
        vault = summary_value(option_of(result.command, "--vault-name"))
        store = _SECRET_STORES.get(str(facts.get("provider")), str(facts.get("provider")))
        return f"read secret {name}{f' from {vault}' if vault else ''} ({store})"

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

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        area = {"shell_history": "shell history", "audit": "auditing", "system_log": "system logs"}
        verb = {"unset": "turn off", "disable": "turn off", "clear": "clear", "stop": "stop"}
        return f"{verb.get(result.facts.get('technique'), 'turn off')} {area.get(result.facts.get('area'), 'logging')}"

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
