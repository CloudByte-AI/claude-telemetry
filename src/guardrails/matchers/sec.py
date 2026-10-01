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
        "docker": (("login",),),
    }

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            runs = self._SECRET_COMMANDS.get(command.command_name)
            if not runs:
                continue
            for run in runs:
                if self.has_token_run(command.subcommand_path, run):
                    return self.hit(
                        target=f"{command.command_name} {' '.join(run)}",
                        command=command,
                        provider=command.command_name,
                    )
        return None
