"""
Network matchers.

`net.download.exec` is the classic supply-chain shape - `curl ... | sh` - and is
worth a `critical` alert by default: it hands an arbitrary remote host the
ability to run code locally.

`net.web.fetch` and `net.web.search` cover the platforms' own web tools: a
fetch to an arbitrary host is arguably egress, and a search can carry
repository context off-box. They ship with `allow`, so nothing is blocked out
of the box.
"""

from __future__ import annotations

import posixpath
import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult, network_of, url_host
from src.guardrails.parser import Command
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, KIND_WEB, ToolCall

# PowerShell's Invoke-WebRequest / Invoke-RestMethod / wget aliases resolve to curl.
_FETCHERS = frozenset({"curl", "wget", "aria2c", "httpie", "http", "https", "iwr", "irm"})

# Running a script file someone just downloaded.
_SCRIPT_RUNNERS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "python", "python3", "py",
                             "node", "ruby", "perl", "php", "source", ".", "exec", "&",
                             "powershell", "pwsh", "cmd", "start"})
# `eval "$(curl ...)"`, `source <(curl ...)`, PowerShell `iex (iwr ...)`.
_EVALUATORS = frozenset({"eval", "source", "."})

# Flags that send a body, per tool. Case-sensitive: curl's -f is --fail and
# -D is --dump-header, while -F (form) and -d (data) send.
_CURL_SEND = frozenset({
    "-d", "--data", "--data-binary", "--data-raw", "--data-urlencode", "--data-ascii",
    "-F", "--form", "--form-string", "-T", "--upload-file", "--json",
})
_WGET_SEND = frozenset({"--post-data", "--post-file", "--body-data", "--body-file"})
# Invoke-WebRequest / Invoke-RestMethod (PowerShell flags are lower-cased).
_POWERSHELL_SEND = frozenset({"-body", "-infile", "-form"})
_HTTPIE_METHODS = frozenset({"POST", "PUT", "PATCH"})

# Writes the download to a file instead of standard output.
_CURL_OUTPUT = frozenset({"-o", "--output"})
_WGET_OUTPUT = frozenset({"-O", "--output-document"})
_POWERSHELL_OUTPUT = frozenset({"-outfile"})

# `user@host:path` / `host:path` (not a Windows drive like C:/x) and rsync:// URLs.
_REMOTE_PATH = re.compile(r"^(?:rsync://|(?:[^@/\s:]+@)?(?:\[[0-9a-fA-F:]+\]|[A-Za-z0-9._-]{2,}):(?!//))")


def _value_after(command: Command, names: frozenset[str], case_insensitive: bool = False) -> str | None:
    """The value given to the first of `names` (`-o out`, `--output=out`, a bundled `-sSLo out`)."""
    argv = command.argv
    for i, token in enumerate(argv):
        name, eq, value = token.partition("=")
        key = name.lower() if case_insensitive else name
        if key in names:
            if eq:
                return value
            return argv[i + 1] if i + 1 < len(argv) else None
        # A short-flag bundle ending in a value flag: curl -sSLo file.
        if (not case_insensitive and token.startswith("-") and not token.startswith("--")
                and token[1:].isalpha() and len(token) > 2 and f"-{token[-1]}" in names):
            return argv[i + 1] if i + 1 < len(argv) else None
    return None


def _download_file(command: Command) -> str | None:
    """The file a fetcher writes its download to, if it writes one."""
    name = command.command_name
    if command.dialect == "powershell":
        return _value_after(command, _POWERSHELL_OUTPUT, case_insensitive=True)
    if name == "curl":
        out = _value_after(command, _CURL_OUTPUT)
        if out:
            return None if out == "-" else out
        if {"-O", "--remote-name"} & command.flags:
            url = next((o for o in command.subcommand_path if "://" in o), "")
            return posixpath.basename(url.split("?")[0]) or None
    if name == "wget":
        # -O- / -qO- / --output-document=- send the download to standard output.
        if any(re.fullmatch(r"-[A-Za-z]*O-", t) for t in command.argv):
            return None
        out = _value_after(command, _WGET_OUTPUT)
        if out:
            return None if out == "-" else out
        url = next((o for o in command.subcommand_path if "://" in o), "")
        return posixpath.basename(url.split("?")[0]) or None
    if name == "aria2c":
        return _value_after(command, frozenset({"-o", "--out"}))
    target = next((r for r in command.redirects if r and r not in ("/dev/null", "&1", "&2", "1", "2")), None)
    return target


def _writes_to_stdout(command: Command) -> bool:
    """A fetcher whose download goes to standard output, not a file."""
    return _download_file(command) is None


# The evaluated text starting with a fetch: `$(curl`, `<(wget`, `(iwr`, `(New-Object Net.WebClient)`.
_EVALUATED_FETCH = re.compile(
    r"^[^\n]{0,40}?(?:\$\(|<\(|\(|\")\s*(?:sudo\s+)?"
    r"(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|http|aria2c|new-object\s+(?:system\.)?net\.webclient)\b",
    re.IGNORECASE,
)
_FETCHER_ALIASES = {"invoke-webrequest": "curl", "invoke-restmethod": "curl", "iwr": "curl", "irm": "curl"}


def _canonical_fetcher(name: str) -> str:
    """The parsed command name a fetch in evaluated text resolves to."""
    lowered = name.lower()
    if lowered.startswith("new-object"):
        return "curl"
    return _FETCHER_ALIASES.get(lowered, lowered)


def _text_after(text: str, raw: str) -> str:
    """What follows `raw` in the full command (a process substitution `<(...)` is split off)."""
    at = text.find(raw) if raw else -1
    return text[at:at + len(raw) + 80] if at >= 0 else ""


# A shell or interpreter whose script IS a download: `bash <(curl ...)`,
# `sh -c "$(curl -fsSL ...)"`, `python3 <(wget -qO- ...)`. The fetch must open
# the substitution at the script position; `sh -c "curl x | jq"` runs curl, it
# does not run what curl returns.
_RUNS_FETCHED_SCRIPT = re.compile(
    r"^\S+\s+(?:-[-\w]+\s+)*(?:<\(|[\"']?\$\(|[\"']?`)\s*(?:sudo\s+)?"
    r"(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|http|aria2c)\b",
    re.IGNORECASE,
)
_SCRIPT_HOSTS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "python", "python3", "py",
                           "node", "ruby", "perl", "php", "powershell", "pwsh"})


# Where a download is thrown away (`curl -o /dev/null -w '%{http_code}'`): nothing to run later.
_DISCARD = frozenset({"/dev/null", "nul", "$null"})


def _program(command: Command) -> str:
    """The program as typed (`.\\setup.exe`), before the parser normalised its name."""
    return command.raw.split(None, 1)[0] if command.raw else ""


def _same_file(a: str, b: str) -> bool:
    norm = lambda p: p.strip("'\"").replace("\\", "/")  # noqa: E731
    a, b = norm(a), norm(b)
    return bool(a) and (a == b or posixpath.basename(a) == posixpath.basename(b))


@register_matcher
class DownloadAndExecuteMatcher(BaseMatcher):
    OPERATION = "net.download.exec"
    DOMAIN = "net"
    DESCRIPTION = "Runs downloaded content: piped into an interpreter, evaluated, or saved and run"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("fetcher", "host", "technique")

    def match(self, call: ToolCall) -> MatchResult | None:
        commands = list(call.commands)
        fetchers = [c for c in commands if c.command_name in _FETCHERS]

        # 1. curl ... | sh  /  wget -qO- ... | sudo bash  /  iwr ... | iex
        for command in commands:
            source = next((f for f in fetchers if f.command_name in command.piped_from), None)
            if source is not None and _writes_to_stdout(source) and self.reads_code_from_stdin(command):
                found = self._found(call, source, command, "pipe")
                if found:
                    return found

        # 2. eval "$(curl -fsSL ...)" / source <(curl ...) / iex (iwr ...): what
        #    is evaluated is the download itself - not merely an evaluator and a
        #    fetcher somewhere in the same chain (`eval "$(./setup.sh)"; curl x`).
        for evaluator in commands:
            if evaluator.command_name not in _EVALUATORS or evaluator.subcommand_path:
                continue
            evaluated = _EVALUATED_FETCH.search(evaluator.raw) or _EVALUATED_FETCH.search(
                _text_after(call.command, evaluator.raw))
            if evaluated:
                if evaluated.group(1).lower().startswith("new-object"):
                    # iex (New-Object Net.WebClient).DownloadString(url): no fetcher command at all.
                    found = self._found(call, evaluator, evaluator, "evaluate")
                    if found:
                        return found
                source = next((f for f in fetchers if f.command_name == _canonical_fetcher(evaluated.group(1))
                               and _writes_to_stdout(f)), None)
                if source is not None:
                    found = self._found(call, source, evaluator, "evaluate")
                    if found:
                        return found

        # ... or run as a shell's script: bash <(curl ...) / sh -c "$(curl ...)".
        for runner in commands:
            if runner.command_name not in _SCRIPT_HOSTS:
                continue
            evaluated = _RUNS_FETCHED_SCRIPT.search(_text_after(call.command, runner.raw))
            if evaluated:
                source = next((f for f in fetchers if f.command_name == _canonical_fetcher(evaluated.group(1))
                               and _writes_to_stdout(f)), None)
                if source is not None:
                    found = self._found(call, source, runner, "evaluate")
                    if found:
                        return found

        # 3. curl -o install.sh URL && bash install.sh  /  ... -OutFile $exe; Start-Process $exe
        for index, fetcher in enumerate(commands):
            if fetcher.command_name not in _FETCHERS:
                continue
            saved = _download_file(fetcher)
            if not saved or saved.lower() in _DISCARD:
                continue
            for later in commands[index + 1:]:
                runs_it = _same_file(later.command_name, saved) or _same_file(_program(later), saved) or (
                    later.command_name in _SCRIPT_RUNNERS
                    and later.subcommand_path and _same_file(later.subcommand_path[0], saved)
                ) or (
                    later.command_name in ("powershell", "pwsh") and "-file" in later.flags
                    and any(_same_file(o, saved) for o in later.subcommand_path)
                )
                if runs_it:
                    found = self._found(call, fetcher, later, "download_then_run")
                    if found:
                        return found
        return None

    def _found(self, call: ToolCall, fetcher: Command, runner: Command, technique: str) -> MatchResult | None:
        host = next((h for h in (url_host(o, call.command) for o in fetcher.subcommand_path) if h), None)
        host = host or (call.hosts[0] if call.hosts else None)
        # Content served by this machine (localhost, 127.0.0.1) is not remote code.
        if host and network_of(host) == "loopback":
            return None
        return self.hit(
            target=host,
            evidence=fetcher.raw if fetcher is runner else f"{fetcher.raw}  ->  {runner.raw}",
            command=fetcher,
            fetcher=fetcher.command_name,
            host=host,
            technique=technique,
        )


@register_matcher
class UploadMatcher(BaseMatcher):
    OPERATION = "net.upload"
    DOMAIN = "net"
    DESCRIPTION = "Sends local data to another host"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # network: `public`, or `private` for a LAN address. Sending to this
    # machine itself (localhost, 127.0.0.1) is not an upload and never fires.
    FACTS = ("host", "method", "network")

    _COPIERS = frozenset({"scp", "rsync"})
    _INTERACTIVE = frozenset({"sftp", "ftp"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            name = command.command_name
            if name in _FETCHERS and self._sends_body(command):
                hosts = [h for h in (url_host(o, call.command) for o in command.subcommand_path) if h]
                remote = [h for h in hosts if network_of(h) != "loopback"]
                if hosts and not remote:
                    continue   # only to this machine
                host = remote[0] if remote else None
                return self.hit(target=host, command=command, host=host, method="http",
                                network=network_of(host))
            if name in self._COPIERS:
                # An upload copies TO a remote: the destination (last operand)
                # is remote. `scp host:/var/log/x ./` is a download.
                operands = [o for o in command.subcommand_path if o]
                if len(operands) >= 2 and _REMOTE_PATH.match(operands[-1]) and not _REMOTE_PATH.match(operands[0]):
                    host = self._remote_host(operands[-1])
                    return self.hit(target=operands[-1], command=command, host=host, method=name,
                                    network=network_of(host))
            if name in self._INTERACTIVE:
                remote = next((o for o in command.subcommand_path if _REMOTE_PATH.match(o) or "." in o), None)
                if remote:
                    host = self._remote_host(remote)
                    return self.hit(target=remote, command=command, host=host, method=name,
                                    network=network_of(host))
            if self._cloud_upload(command):
                destination = command.subcommand_path[-1]
                return self.hit(target=destination, command=command, host=destination.split("/")[2] or None,
                                method=name, network="public")
        return None

    @staticmethod
    def _sends_body(command: Command) -> bool:
        if command.dialect == "powershell":
            return bool(command.flags & _POWERSHELL_SEND) or bool(command.flags & {"-d", "--data", "--data-binary", "--data-raw", "--form", "--upload-file", "--json"})
        if command.command_name in ("http", "https", "httpie"):
            return bool(command.subcommand_path) and command.subcommand_path[0].upper() in _HTTPIE_METHODS
        if command.command_name == "wget":
            return bool(command.flags & _WGET_SEND)
        return bool(command.flags & _CURL_SEND)

    @staticmethod
    def _remote_host(remote: str) -> str | None:
        if remote.startswith("rsync://"):
            return remote[len("rsync://"):].split("/")[0] or None
        return remote.split(":")[0].split("@")[-1].strip("[]") or None

    def _cloud_upload(self, command: Command) -> bool:
        """aws s3 cp|sync|mv LOCAL s3://..., gsutil / gcloud storage cp LOCAL gs://..."""
        path = command.subcommand_path
        if len(path) < 3:
            return False
        if command.command_name == "aws":
            sub = self.operands_from_subcommand(command, frozenset({"--profile", "--region", "--endpoint-url"}))
            if not (len(sub) >= 4 and sub[0] == "s3" and sub[1] in ("cp", "sync", "mv")):
                return False
            source, destination = sub[2], sub[-1]
            return destination.startswith("s3://") and not source.startswith("s3://")
        if command.command_name in ("gsutil", "gcloud"):
            sub = self.operands_from_subcommand(command, frozenset({"-o", "--project"}))
            cut = 2 if sub[:2] == ("storage", "cp") or sub[:2] == ("storage", "rsync") else (
                1 if sub[:1] in (("cp",), ("rsync",)) else 0)
            if not cut or len(sub) < cut + 2:
                return False
            return sub[-1].startswith("gs://") and not sub[cut].startswith("gs://")
        return False


@register_matcher
class WebFetchMatcher(BaseMatcher):
    OPERATION = "net.web.fetch"
    DOMAIN = "net"
    DESCRIPTION = "Fetches a URL through the platform's own web tool"
    APPLIES_TO = (KIND_WEB,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    FACTS = ("host",)

    def match(self, call: ToolCall) -> MatchResult | None:
        if not call.urls:
            return None
        host = call.hosts[0] if call.hosts else None
        return self.hit(target=host or call.urls[0], evidence=call.urls[0], host=host)


@register_matcher
class WebSearchMatcher(BaseMatcher):
    OPERATION = "net.web.search"
    DOMAIN = "net"
    DESCRIPTION = "Runs a web search, which can carry local context off-box"
    APPLIES_TO = (KIND_WEB,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"

    def match(self, call: ToolCall) -> MatchResult | None:
        query = (call.raw_input or {}).get("query")
        if not query:
            return None
        # The query is the evidence; it is capped and masked before it reaches
        # an audit row.
        return self.hit(target="web_search", evidence=str(query))
