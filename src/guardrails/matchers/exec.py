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
# happened, but not what the code does. The flags that introduce code, and the
# options that take a value of their own (so it is not read as a script name).
_INTERPRETERS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "python": (frozenset({"-c"}), frozenset({"-X", "-W", "-Q"})),
    "python3": (frozenset({"-c"}), frozenset({"-X", "-W", "-Q"})),
    "py": (frozenset({"-c"}), frozenset({"-X", "-W", "-Q"})),
    "node": (frozenset({"-e", "--eval", "-p", "--print"}), frozenset({"-r", "--require", "--import", "--loader"})),
    "bun": (frozenset({"-e", "--eval", "-p", "--print"}), frozenset()),
    "ruby": (frozenset({"-e"}), frozenset({"-r", "-I"})),
    "perl": (frozenset({"-e", "-E"}), frozenset({"-I", "-M"})),
    "php": (frozenset({"-r"}), frozenset({"-d", "-c"})),
}
# Ends option parsing for the interpreter itself: what follows belongs to the
# script or module (`python -m pytest -c pytest.ini`).
_STOP_OPTIONS = frozenset({"-m", "-"})

# A shell string built at run time. `source file` / `. file` run a FILE, which
# is not inline code.
_EVAL_COMMANDS = frozenset({"eval"})


def _inline_code_flag(command) -> str | None:
    """The flag that gave this interpreter its code on the command line, if any."""
    spec = _INTERPRETERS.get(command.command_name)
    if spec is None:
        return None
    code_flags, value_flags = spec
    argv = command.argv
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _STOP_OPTIONS or not token.startswith("-") or token == "--":
            return None   # a script, a module or a lone `-` comes first
        name = token.partition("=")[0]
        if name in code_flags:
            return name
        if not name.startswith("--") and len(name) > 2:
            # A value glued to its option: `ruby -ryaml`, `perl -MJSON`.
            if name[:2] in value_flags:
                i += 1
                continue
            # A short-flag bundle (Python only bundles freely): `python -Ic "..."`
            # runs code, `python -Im mod` a module.
            if command.command_name in ("python", "python3", "py") and name[1:].isalpha():
                for letter in name[1:]:
                    if f"-{letter}" in code_flags:
                        return f"-{letter}"
                    if f"-{letter}" in _STOP_OPTIONS:
                        return None
        if name in value_flags and "=" not in token:
            i += 2
            continue
        i += 1
    return None


def _decodes(command) -> bool:
    """A command that decodes data: base64 -d, openssl base64 -d / enc -d, xxd -r, certutil -decode."""
    name, flags = command.command_name, command.flags
    if name == "base64":
        return bool({"-d", "--decode", "-D"} & flags)
    if name == "openssl":
        return bool({"-d"} & flags) and bool({"base64", "enc"} & set(command.subcommand_path[:1]))
    if name == "xxd":
        return "-r" in flags
    if name == "certutil":
        return bool({"-decode", "-decodehex"} & {f.lower() for f in flags})
    return name == "uudecode"


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
    # source: `argument` (python -c "..."), `stdin` (python - <<'EOF' ... EOF)
    # or `eval` (a shell string built at run time).
    FACTS = ("interpreter", "source")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            name = command.command_name
            if _inline_code_flag(command):
                return self.hit(target=name, command=command, interpreter=name, source="argument")
            if name == "deno" and self.starts_with_run(command.subcommand_path, ("eval",)):
                return self.hit(target=name, command=command, interpreter=name, source="argument")
            if name in _EVAL_COMMANDS and command.subcommand_path:
                return self.hit(target=name, command=command, interpreter=name, source="eval")
            # A script written into a here-document, or echoed into a pipe
            # (`printf '...' | python3 -`), is inline code too.
            if name in _INTERPRETERS and self.reads_code_from_stdin(command) and (
                command.stdin or _pipes_text_in(call, command)
            ):
                return self.hit(target=name, command=command, interpreter=name, source="stdin")
        return None


# Commands whose output is text written in the command itself.
_TEXT_WRITERS = frozenset({"echo", "printf"})


def _pipes_text_in(call: ToolCall, command) -> bool:
    """Whether the text piped into `command` was written inline: `echo`/`printf`, or `cat <<EOF`."""
    if _TEXT_WRITERS & set(command.piped_from):
        return True
    return any(c.command_name == "cat" and c.stdin and not c.subcommand_path
               for c in call.commands if c.command_name in command.piped_from)


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
        commands = list(call.commands)

        # powershell -EncodedCommand <base64>. The parser keeps PowerShell's
        # flags whole, so -NoProfile / -ExecutionPolicy are not a stray -e.
        for command in commands:
            if command.command_name in ("powershell", "pwsh"):
                if command.flags & self._ENCODED_FLAGS:
                    return self.hit(
                        target="powershell",
                        command=command,
                        technique="powershell_encodedcommand",
                    )

        decoders = [c for c in commands if _decodes(c)]
        if not decoders:
            return None
        decoder_names = {c.command_name for c in decoders}

        # Decoded data piped into something that runs it: `... | base64 -d | sh`.
        for command in commands:
            if decoder_names & set(command.piped_from) and self.reads_code_from_stdin(command):
                decoder = next(c for c in decoders if c.command_name in command.piped_from)
                return self.hit(target=decoder.command_name, evidence=f"{decoder.raw}  ->  {command.raw}",
                                command=decoder, technique="decode_then_execute", decoder=decoder.command_name)

        # ... or evaluated: eval "$(echo ... | base64 -d)".
        evaluator = next((c for c in commands if c.command_name == "eval" and not c.subcommand_path), None)
        if evaluator is not None:
            decoder = decoders[0]
            return self.hit(target=decoder.command_name, evidence=f"{decoder.raw}  ->  {evaluator.raw}",
                            command=decoder, technique="decode_then_execute", decoder=decoder.command_name)
        return None
