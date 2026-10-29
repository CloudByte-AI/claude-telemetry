"""
Command-execution matchers.

`exec.encoded` targets the standard evasion of a best-effort parser: stop
being a shell command at all (`echo <base64> | base64 -d | sh`, or
PowerShell's `-EncodedCommand`). Intent cannot be decoded, but the SHAPE is
visible and has almost no legitimate use in an agent session.
"""

from __future__ import annotations

from src.guardrails.matchers.base import (SSH_VALUE_OPTIONS, BaseMatcher, MatchResult, option_of, summary_value,
                                          targets_production)
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

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        wrapped = result.facts.get("wrapped_command")
        return f"run {wrapped} as administrator" if wrapped else "run a command as administrator"

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

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        if result.facts.get("technique") == "powershell_encodedcommand":
            return "run an encoded PowerShell command"
        decoder = result.facts.get("decoder")
        return f"run code decoded by {decoder}" if decoder else "run encoded code"

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


_SCHEDULER_LABELS = {"schtasks": "Task Scheduler", "powershell": "Task Scheduler", "at": "at",
                     "systemd": "systemd timer", "launchd": "launchd"}


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

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        scheduler, action = result.facts.get("scheduler"), result.facts.get("action")
        if scheduler == "cron":
            # crontab replaces or removes the user's whole table, never one job.
            if action == "remove":
                return "remove all cron jobs"
            if action == "modify":
                return "edit the cron jobs"
            source = next((o for o in (result.command.subcommand_path if result.command else ()) if o != "-"), None)
            return f"replace all cron jobs with {summary_value(source)}" if source else "replace all cron jobs"
        verb = {"modify": "change", "install": "install"}.get(str(action), str(action))
        name = summary_value(option_of(result.command, "/tn", "//tn", "-taskname", "-name"))
        label = _SCHEDULER_LABELS.get(str(scheduler), str(scheduler))
        return f"{verb} scheduled task {name} ({label})" if name else f"{verb} a scheduled task ({label})"

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _schedule(command)
            if found:
                scheduler, action = found
                return self.hit(target=scheduler, command=command, scheduler=scheduler, action=action)
        return None


# Options that take a value, ahead of the subcommand, per remote-shell tool.
_KUBE_VALUE_OPTIONS = frozenset({
    "-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "-s", "--server",
    "--token", "--as", "--as-group", "--request-timeout",
})
_CLOUD_VALUE_OPTIONS = frozenset({
    "--profile", "--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle",
    "--subscription", "-o", "--project", "--zone", "--account", "--configuration",
})
_WINDOWS_REMOTING = frozenset({"invoke-command", "icm", "enter-pssession", "etsn", "new-pssession", "nsn"})


def _remote_channel(command) -> tuple[str, str | None] | None:
    """(channel, host or target) when this command runs a command on another machine or in a pod."""
    name = command.command_name.lower()
    argv = command.argv
    if name == "ssh":
        operands = BaseMatcher.operands_from_subcommand(command, SSH_VALUE_OPTIONS)
        # `ssh host` alone opens an interactive shell; with a command or a
        # script on its input it runs one there. -N forwards ports only.
        if operands and (len(operands) > 1 or command.stdin) and "-N" not in command.flags:
            return "ssh", operands[0].rsplit("@", 1)[-1]
        return None
    if name in ("kubectl", "oc"):
        sub = BaseMatcher.operands_from_subcommand(command, _KUBE_VALUE_OPTIONS)
        if sub and sub[0] in ("exec", "cp", "debug", "attach", "rsh", "rsync"):
            return f"kubectl_{sub[0]}", _kube_target(argv, sub[0])
        return None
    if name == "aws":
        sub = BaseMatcher.operands_from_subcommand(command, _CLOUD_VALUE_OPTIONS)
        if BaseMatcher.starts_with_run(sub, ("ssm", "send-command")):
            return "aws_ssm", _flag_value(argv, ("--instance-ids", "--instance-id", "--targets"))
        if BaseMatcher.starts_with_run(sub, ("ssm", "start-session")) and "portforwarding" not in " ".join(argv).lower():
            return "aws_ssm", _flag_value(argv, ("--target",))
        if BaseMatcher.starts_with_run(sub, ("ecs", "execute-command")):
            return "aws_ecs_exec", _flag_value(argv, ("--task",))
        return None
    if name == "az":
        sub = BaseMatcher.operands_from_subcommand(command, _CLOUD_VALUE_OPTIONS)
        if BaseMatcher.starts_with_run(sub, ("vm", "run-command")) and len(sub) > 2 and sub[2] in ("invoke", "create"):
            return "az_run_command", _flag_value(argv, ("--name", "-n"))
        for run in (("containerapp", "exec"), ("webapp", "ssh"), ("ssh", "vm"), ("ssh", "arc")):
            if BaseMatcher.starts_with_run(sub, run):
                return "az_" + run[0], _flag_value(argv, ("--name", "-n"))
        return None
    if name == "gcloud":
        sub = BaseMatcher.operands_from_subcommand(command, _CLOUD_VALUE_OPTIONS)
        if BaseMatcher.starts_with_run(sub, ("compute", "ssh")):
            return "gcloud_ssh", sub[2] if len(sub) > 2 else None
        return None
    if name == "heroku" and command.subcommand_path[:1] == ("run",):
        return "heroku_run", _flag_value(argv, ("-a", "--app"))
    if name in ("fly", "flyctl") and command.subcommand_path[:2] == ("ssh", "console"):
        return "fly_ssh", _flag_value(argv, ("-a", "--app"))
    if name in _WINDOWS_REMOTING:
        if name in ("invoke-command", "icm") and not ({"-computername", "-cn", "-session", "-hostname",
                                                      "-vmname", "-containerid"} & {f.lower() for f in command.flags}):
            return None   # without a target, Invoke-Command runs locally
        return "powershell_remoting", _flag_value(argv, ("-ComputerName", "-HostName", "-cn"), case_insensitive=True)
    if name in ("psexec", "psexec64", "paexec"):
        return "psexec", next((a.lstrip("\\") for a in argv if a.startswith("\\\\")), None)
    if name == "winrs":
        return "winrs", next((a.partition(":")[2] for a in argv if a.lower().startswith(("-r:", "/r:"))), None)
    return None


# Options of `kubectl exec` / `cp` / `debug` that take a value, after the subcommand.
_KUBE_EXEC_VALUE_OPTIONS = _KUBE_VALUE_OPTIONS | {"-c", "--container", "--image", "--target", "-f", "--filename",
                                                  "--pod-running-timeout", "--retries"}


def _kube_target(argv, subcommand: str) -> str | None:
    """
    The pod a `kubectl exec` / `cp` names, past its options and their values.
    For `cp` it is the `[namespace/]pod:path` operand, source or destination.
    """
    if subcommand not in argv:
        return None
    i = argv.index(subcommand) + 1
    operands: list[str] = []
    while i < len(argv):
        token = argv[i]
        if token == "--":
            break
        if token.startswith("-"):
            i += 2 if token in _KUBE_EXEC_VALUE_OPTIONS and "=" not in token else 1
            continue
        if subcommand != "cp":
            return token
        operands.append(token)
        i += 1
    # `C:\dir` is a local Windows path, not a one-letter pod.
    remote = next((o for o in operands if ":" in o and len(o.split(":", 1)[0]) > 1), None)
    if remote:
        return remote.split(":", 1)[0]
    return operands[0] if operands else None


def _flag_value(argv, names: tuple[str, ...], case_insensitive: bool = False) -> str | None:
    """The value of the first of `names` in argv (`--name x` or `--name=x`)."""
    wanted = {n.lower() for n in names} if case_insensitive else set(names)
    for i, token in enumerate(argv):
        option, eq, value = token.partition("=")
        key = option.lower() if case_insensitive else option
        if key in wanted:
            if eq:
                return value
            if i + 1 < len(argv):
                return argv[i + 1]
    return None


@register_matcher
class RemoteExecutionMatcher(BaseMatcher):
    OPERATION = "exec.remote"
    DOMAIN = "exec"
    DESCRIPTION = "Executes a command on a remote host"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # channel: ssh, kubectl_exec (also cp, debug, attach, rsh), aws_ssm,
    # aws_ecs_exec, az_run_command, az_containerapp, az_webapp, az_ssh,
    # gcloud_ssh, heroku_run, fly_ssh, powershell_remoting, psexec, winrs.
    # A local container (`docker exec`) is this machine, so it is not one.
    # remote_host: the machine, pod or instance named. production: the command names
    # a production target (context, profile, host, resource group).
    FACTS = ("channel", "remote_host", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _remote_channel(command)
            if found:
                channel, host = found
                return self.hit(target=host or channel, command=command, channel=channel, remote_host=host,
                                production=targets_production(command, call))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        host = result.facts.get("remote_host")
        where = f"on {host}" if host else "on another machine"
        if str(result.facts.get("channel", "")).startswith("kubectl_"):
            where = f"in pod {host}" if host else "in a pod"
        prod = " (production)" if result.facts.get("production") else ""
        return f"run a command {where}{prod}"


# Programs a by-name kill most often takes down whole: every Node, Python or
# Java process on the machine, not just the one the agent started.
_RUNTIMES = frozenset({
    "node", "python", "python3", "pythonw", "java", "javaw", "dotnet", "ruby", "php", "bun", "deno",
    "code", "chrome", "msedge", "firefox", "bash", "powershell", "pwsh", "cmd", "claude", "cursor",
    "explorer", "docker", "wsl",
})


# pkill / killall options that take a value (a signal, a parent id, a user...).
_KILL_VALUE_OPTIONS = frozenset({
    "-P", "--parent", "-g", "--pgroup", "-G", "--group", "-s", "--session", "-t", "--terminal", "-u", "--euid",
    "-U", "--uid", "--signal", "-F", "--pidfile", "--ns", "--nslist", "-o", "--older-than", "-y", "--younger-than",
})


def _kill_operands(argv) -> list[str]:
    """pkill / killall operands: the names or patterns, without option values."""
    operands: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("-"):
            i += 2 if token in _KILL_VALUE_OPTIONS else 1
            continue
        operands.append(token)
        i += 1
    return operands


def _kill_target(command) -> tuple[str, str | None] | None:
    """(method, program) when this command ends processes. method: pid, name or pattern."""
    name = command.command_name.lower()
    argv = command.argv
    if name == "taskkill":
        image = _flag_value(argv, ("/im", "//im", "-im"), case_insensitive=True)
        return ("name", image) if image else ("pid", None)
    if name == "kill":
        program = _flag_value(argv, ("-name", "-processname"), case_insensitive=True)
        return ("name", program) if program else ("pid", None)
    if name in ("pkill", "killall"):
        operands = _kill_operands(argv)
        if not operands:
            return "pid", None          # `pkill -P 123`: the children of one process
        # `pkill -f "next dev"` matches the whole command line: a narrow pattern.
        method = "pattern" if name == "pkill" and {"-f", "--full"} & command.flags else "name"
        return method, operands[-1]
    if name == "fuser" and "-k" in command.flags:
        return "pid", None
    if name in ("kill-port", "killport"):
        return "pid", None
    return None


@register_matcher
class ProcessKillMatcher(BaseMatcher):
    OPERATION = "exec.process.kill"
    DOMAIN = "exec"
    DESCRIPTION = "Terminates a running process"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    # by_name: processes chosen by program name (taskkill /IM, pkill, killall,
    # Stop-Process -Name), which ends every process of that program, not one
    # process id. pattern: chosen by a command-line pattern (pkill -f), which
    # is usually one program the agent started. program: the name or pattern
    # given. all_instances: a by-name kill of a common runtime (node, python,
    # java, a browser, a shell), which also ends the user's other programs.
    FACTS = ("by_name", "pattern", "program", "all_instances")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _kill_target(command)
            if found is None:
                continue
            method, program = found
            by_name = method == "name"
            # `Stop-Process -Name python3,python` names several programs.
            names = {n.strip().lower().removesuffix(".exe") for n in (program or "").strip("\"'").split(",")}
            return self.hit(target=program or command.command_name, command=command, by_name=by_name,
                            pattern=method == "pattern", program=program,
                            all_instances=by_name and bool(names & _RUNTIMES))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        if not result.facts.get("by_name"):
            return None
        program = result.facts.get("program") or "a program"
        return f"end every {program} process" if result.facts.get("all_instances") else f"end processes named {program}"
