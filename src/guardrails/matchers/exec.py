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
        # PowerShell: `Start-Process <program> -Verb RunAs` starts it elevated.
        for command in call.commands:
            program = command.raw.split(None, 1)[0].lower() if command.raw else ""
            if (program in ("start-process", "saps", "start") and "-verb" in {f.lower() for f in command.flags}
                    and "runas" in {o.lower() for o in command.subcommand_path}):
                wrapped = next((o for o in command.subcommand_path if o.lower() != "runas"), None)
                return self.hit(target=wrapped, command=command, escalator="runas", wrapped_command=wrapped)
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


# PowerShell scheduled-task cmdlets (the parser lower-cases cmdlet names).
_PS_SCHEDULE = {
    "register-scheduledtask": "create", "register-scheduledjob": "create",
    "set-scheduledtask": "modify", "set-scheduledjob": "modify",
    "unregister-scheduledtask": "remove", "unregister-scheduledjob": "remove",
}
_SCHTASKS = {"/create": "create", "/change": "modify", "/delete": "remove"}
_LAUNCHCTL = {"load": "create", "bootstrap": "create", "submit": "create", "enable": "create",
              "unload": "remove", "bootout": "remove", "remove": "remove"}
_SYSTEMD_RUN_TIMERS = ("--on-calendar", "--on-active", "--on-boot", "--on-startup",
                       "--on-unit-active", "--on-unit-inactive", "--on-clock-change", "--on-timezone-change")


def _schedule(command) -> tuple[str, str] | None:
    """(scheduler, action) when this command adds, changes or removes a schedule."""
    name = command.command_name.lower()
    flags = command.flags
    operands = command.subcommand_path
    if name == "crontab":
        # crontab FILE / crontab - (or a pipe) installs; -e edits; -r removes;
        # -l only lists. `-u user` names whose table, so its value is not a file.
        files = [o for o in operands if o != _value_of(command, "-u")]
        if {"-r", "-ir"} & flags or "-r" in command.argv:
            return "cron", "remove"
        if "-e" in flags:
            return "cron", "modify"
        if "-l" in flags and not files:
            return None
        return "cron", "install"
    if name in ("at", "batch"):
        if {"-l", "-c", "-r", "-d"} & flags:
            return ("at", "remove") if {"-r", "-d"} & flags else None
        return "at", "create"
    if name == "atrm":
        return "at", "remove"
    if name in ("schtasks", "schtasks.exe"):
        # Git Bash users double the slash (`//create`) to stop path conversion.
        switches = [("/" + o.lstrip("/")).lower() for o in operands if o.startswith("/")]
        if "/?" in switches:
            return None   # help text: `schtasks /change /?`
        action = next((_SCHTASKS[s] for s in switches if s in _SCHTASKS), None)
        return ("schtasks", action) if action else None
    if name in _PS_SCHEDULE:
        return "powershell", _PS_SCHEDULE[name]
    if name == "systemd-run" and any(f.startswith(_SYSTEMD_RUN_TIMERS) for f in command.argv):
        return "systemd", "create"
    if name == "systemctl" and operands and operands[0] in ("enable", "start", "restart", "reenable") \
            and any(o.endswith(".timer") for o in operands[1:]):
        return "systemd", "create"
    if name == "launchctl" and operands and operands[0] in _LAUNCHCTL:
        return "launchd", _LAUNCHCTL[operands[0]]
    return None


def _value_of(command, flag: str) -> str | None:
    argv = command.argv
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


@register_matcher
class ScheduledExecutionMatcher(BaseMatcher):
    OPERATION = "exec.scheduled"
    DOMAIN = "exec"
    DESCRIPTION = "Schedules future execution (cron, at, Task Scheduler, systemd timer, launchd)"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    # scheduler: cron, at, schtasks, powershell, systemd, launchd.
    # action: install / create / modify / remove. Listing a schedule
    # (crontab -l, atq, schtasks /query) is not this operation.
    FACTS = ("scheduler", "action")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _schedule(command)
            if found:
                scheduler, action = found
                return self.hit(target=scheduler, command=command, scheduler=scheduler, action=action)
        return None
