"""
Command-execution matchers.

`exec.encoded` targets the standard evasion of a best-effort parser: stop
being a shell command at all (`echo <base64> | base64 -d | sh`, or
PowerShell's `-EncodedCommand`). Intent cannot be decoded, but the SHAPE is
visible and has almost no legitimate use in an agent session.
"""

from __future__ import annotations

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall

_PRIVILEGE_COMMANDS = frozenset({"sudo", "doas", "su", "runas", "pkexec", "gsudo"})

# Interpreters that take code on the command line - the parser can see that it
# happened, but not what the code does.
_INTERPRETERS: dict[str, tuple[str, ...]] = {
    "python": ("-c",), "python3": ("-c",), "py": ("-c",),
    "node": ("-e", "--eval", "-p", "--print"),
    "ruby": ("-e",), "perl": ("-e",), "php": ("-r",),
    "deno": ("eval",), "bun": ("-e",),
}

_EVAL_COMMANDS = frozenset({"eval", "source", "."})

_DECODERS = frozenset({"base64", "openssl", "xxd", "uudecode", "certutil"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "powershell", "pwsh", "cmd"})


@register_matcher
class PrivilegeEscalationMatcher(BaseMatcher):
    OPERATION = "exec.privilege"
    DOMAIN = "exec"
    DESCRIPTION = "Runs a command with elevated privileges"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    FACTS = ("escalator", "wrapped_command")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, _PRIVILEGE_COMMANDS):
            # The parser already unwrapped the inner command, so name it here:
            # "sudo (rm)" is a far more useful audit row than "sudo".
            wrapped = next(
                (c.command_name for c in call.commands if c.derived and c is not command),
                None,
            )
            return self.hit(
                target=wrapped,
                command=command,
                escalator=command.command_name,
                wrapped_command=wrapped,
            )
        return None


@register_matcher
class InterpreterMatcher(BaseMatcher):
    OPERATION = "exec.interpreter"
    DOMAIN = "exec"
    DESCRIPTION = "Executes inline code through a language interpreter"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    FACTS = ("interpreter",)

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            code_flags = _INTERPRETERS.get(command.command_name)
            if code_flags and (
                {f.lower() for f in command.flags} & set(code_flags)
                or self.has_token_run(command.subcommand_path, ("eval",))
            ):
                return self.hit(
                    target=command.command_name,
                    command=command,
                    interpreter=command.command_name,
                )
            if command.command_name in _EVAL_COMMANDS and command.subcommand_path:
                return self.hit(
                    target=command.command_name,
                    command=command,
                    interpreter=command.command_name,
                )
        return None


@register_matcher
class EncodedExecutionMatcher(BaseMatcher):
    OPERATION = "exec.encoded"
    DOMAIN = "exec"
    DESCRIPTION = "Executes encoded or obfuscated content, defeating inspection"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("technique", "decoder")

    # PowerShell's own flag for exactly this, including its accepted prefixes.
    _ENCODED_FLAGS = frozenset({
        "-encodedcommand", "-enc", "-e", "-ec", "-encoded",
    })

    def match(self, call: ToolCall) -> MatchResult | None:
        names = set(call.command_names)

        # powershell -EncodedCommand <base64>
        for command in call.commands:
            if command.command_name in ("powershell", "pwsh"):
                if {f.lower() for f in command.flags} & self._ENCODED_FLAGS:
                    return self.hit(
                        target="powershell",
                        command=command,
                        technique="powershell_encodedcommand",
                    )

        # A decoder and a shell in the same chain: `... | base64 -d | sh`.
        decoder = names & _DECODERS
        shell = names & _SHELLS
        if decoder and shell:
            command = next(c for c in call.commands if c.command_name in decoder)
            return self.hit(
                target=command.command_name,
                command=command,
                technique="decode_then_execute",
                decoder=command.command_name,
            )
        return None
