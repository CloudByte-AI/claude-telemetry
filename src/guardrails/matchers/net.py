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

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, KIND_WEB, ToolCall

_FETCHERS = frozenset({"curl", "wget", "aria2c", "httpie", "http", "iwr"})
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "powershell", "pwsh", "python", "python3", "node", "ruby", "perl"})

# Flags that mean "send this payload somewhere", as opposed to fetching.
_UPLOAD_FLAGS = frozenset({
    "-d", "--data", "--data-binary", "--data-raw", "--data-urlencode",
    "-f", "--form", "-t", "--upload-file", "--upload",
})


@register_matcher
class DownloadAndExecuteMatcher(BaseMatcher):
    OPERATION = "net.download.exec"
    DOMAIN = "net"
    DESCRIPTION = "Pipes downloaded content straight into an interpreter"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("fetcher", "host")

    def match(self, call: ToolCall) -> MatchResult | None:
        names = set(call.command_names)
        fetchers = names & _FETCHERS
        if not fetchers or not (names & _SHELLS):
            return None
        # A fetch and a shell in the same chain. Not conclusive on its own
        # (`curl -o x.sh && bash x.sh` is the same risk, `curl x && ls` is not),
        # but the combination is rare enough in agent sessions to be worth asking.
        command = next(c for c in call.commands if c.command_name in fetchers)
        host = call.hosts[0] if call.hosts else None
        return self.hit(
            target=host,
            command=command,
            fetcher=command.command_name,
            host=host,
        )


@register_matcher
class UploadMatcher(BaseMatcher):
    OPERATION = "net.upload"
    DOMAIN = "net"
    DESCRIPTION = "Sends local data to a remote host"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    FACTS = ("host", "method")

    _COPIERS = frozenset({"scp", "rsync", "sftp", "ftp"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            lowered = {f.lower() for f in command.flags}
            if command.command_name in _FETCHERS and (lowered & _UPLOAD_FLAGS):
                host = call.hosts[0] if call.hosts else None
                return self.hit(target=host, command=command, host=host, method="http")
            if command.command_name in self._COPIERS:
                # A remote target contains `host:` - a purely local rsync does not.
                remote = next(
                    (o for o in command.subcommand_path if ":" in o and not o.startswith("-")),
                    None,
                )
                if remote:
                    return self.hit(
                        target=remote, command=command,
                        host=remote.split(":")[0], method=command.command_name,
                    )
        return None


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
