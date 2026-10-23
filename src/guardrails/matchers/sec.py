"""
Security matchers.

Deliberately narrow. `sec.credential.read` fires on commands whose whole purpose
is to print a secret - `aws sts get-session-token`, `gcloud auth print-access-token`
- rather than on anything that merely touches a credentials file, which is what
`fs.sensitive` already covers.
"""

from __future__ import annotations

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall


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

    def _prints_values(self, command) -> bool:
        """`-o yaml|json|jsonpath=...|go-template=...` (any form but name/wide)."""
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
            if value.split("=", 1)[0].lower() not in self._QUIET_OUTPUTS:
                return True
        return False
