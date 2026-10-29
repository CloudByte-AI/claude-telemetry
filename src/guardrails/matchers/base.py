"""
Matcher contract.

A matcher DETECTS. It never decides:

    matcher (code, shipped)   "this call is a recursive delete"
    rule    (YAML, user)      "recursive deletes need approval"

so a new matcher can never start blocking anyone's work on upgrade.

`OPERATION` is a public interface: it appears in user profiles, taxonomy.json
and audit rows, so renaming one is a breaking change.
"""

from __future__ import annotations

import posixpath
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from src.guardrails.parser import POSIX, Command, parse_chain
from src.guardrails.toolcall import ToolCall

# Cap on stored evidence. A matched fragment can contain a secret, so evidence
# is masked before storage AND capped here, so a long command cannot smuggle a
# payload into the audit log.
MAX_EVIDENCE = 256

# Programs that run code they read on standard input (see reads_code_from_stdin).
STDIN_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
STDIN_INTERPRETERS = frozenset({"python", "python3", "py", "node", "ruby", "perl", "php"})

# Filename shapes that carry credentials or keys. Matched on the basename or on
# a path segment, never as a bare substring: `environment.ts` must not look like
# a `.env`.
_SENSITIVE_BASENAMES = frozenset({
    ".env", ".npmrc", ".pypirc", ".netrc", ".git-credentials", ".htpasswd",
    "credentials", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    # authorized_keys holds no secret, but writing it grants access (a backdoor).
    "authorized_keys", "secrets.yaml", "secrets.yml",
    "secrets.json", "terraform.tfvars", ".dockercfg", "kubeconfig",
})
_SENSITIVE_SUFFIXES = (
    ".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".asc", ".ppk",
    # Terraform variable files hold provider credentials (`prod.tfvars`).
    ".tfvars", ".tfvars.json",
)
# `.env.production`, `.env.local`... A service-account KEY is a JSON file
# (`service-account-prod.json`); `ServiceAccountController.ts` and a Helm
# `serviceaccount.yaml` template hold no secret.
_ENV_PREFIX = ".env."
_SERVICE_ACCOUNT_PREFIXES = ("service-account", "serviceaccount", "service_account")
# Committed templates that document a secret file's shape, never its values.
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist", ".defaults", ".default", ".tmpl", ".tpl")
# Directory segments whose contents are sensitive regardless of filename.
_SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg", ".kube", ".docker", ".azure"})
# The public half of a key pair carries no secret (`~/.ssh/id_ed25519.pub`).
_PUBLIC_SUFFIXES = (".pub",)
# Files under ~/.ssh that hold no secret: client settings and the public keys of
# hosts already trusted.
_SSH_PUBLIC_FILES = frozenset({"config", "known_hosts", "known_hosts.old"})
# Files under ~/.aws that hold profiles, regions and command aliases, never keys
# (those live in `credentials` and the SSO / CLI token caches).
_AWS_SETTINGS_FILES = frozenset({"config", "alias"})
# A CA, root or chain certificate is public (`root-ca.pem`,
# `ca-bundle.crt`, `fullchain.pem`). A name that also says key or private is not.
# Name-based, a heuristic of this plugin: the content is not read here.
_PUBLIC_CERTIFICATE = re.compile(
    r"^(?!.*(?:key|priv))(?:.*[-_.])?(?:ca|cacert|ca-bundle|root|rootca|chain|fullchain|intermediate)"
    r"(?:[-_.]?\d*)?(?:[-_.].*)?\.(?:pem|crt|cer)$"
)

# Hosts that are this machine. Loopback only: a LAN address is another machine.
_LOOPBACK = re.compile(r"^(?:localhost|127(?:\.\d{1,3}){3}|0\.0\.0\.0|::1|\[::1\]|host\.docker\.internal)$", re.I)
_PRIVATE = re.compile(
    r"^(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|169\.254(?:\.\d{1,3}){2}|[\w-]+\.local|[\w-]+\.internal|[\w-]+\.lan)$",
    re.I,
)
# `host[:port][/path]` as curl accepts it without a scheme.
_BARE_URL = re.compile(r"^(localhost|\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,})(?::\d+)?(?:/|$)")
_SCHEME_URL = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://(?:[^@/\s]*@)?(\[[^\]]+\]|[^/:?#\s]+)")
_VAR_REF = re.compile(r"^\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
# A word that is one variable reference and nothing else: `$NS`, `${CTX}`, `"$OPTS"`.
_ONLY_VARIABLE = re.compile(r"^[\"']?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[\"']?$")


def url_host(operand: str, chain_text: str = "") -> str | None:
    """
    The host an operand names, if it is a URL: `https://x.io/a`, `localhost:3000/a`,
    or `$BASE/auth` where the same command line assigned `BASE=http://...`.
    """
    text = operand.strip("'\"")
    variable = _VAR_REF.match(text)
    if variable and chain_text:
        assigned = re.search(
            rf"(?:^|[\s;&|(]){re.escape(variable.group(1))}=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)",
            chain_text,
        )
        if assigned:
            text = assigned.group(1).strip("'\"") + text[variable.end():]
    found = _SCHEME_URL.match(text) or _BARE_URL.match(text)
    if not found:
        return None
    return found.group(1).strip("[]").lower()


def network_of(host: str | None) -> str:
    """`loopback` (this machine), `private` (a LAN address) or `public`."""
    if not host:
        return "public"
    if _LOOPBACK.match(host) or _LOOPBACK.match(f"[{host}]"):
        return "loopback"
    if _PRIVATE.match(host):
        return "private"
    return "public"


# -- production targets -------------------------------------------------------
#
# Whether a command names a production target, read from the words that name
# one: a context, profile, namespace, resource group, project, host or a
# workflow input. Words with spaces are scripts or messages, not names, so they
# are skipped. `nonprod`, `preprod` and `product` are not production.
_PRODUCTION_WORDS = frozenset({"prod", "production", "prd"})
_NOT_PRODUCTION_PREFIXES = ("non", "pre", "not")
# English words that end in "production" but name no environment.
_PRODUCTION_LOOKALIKES = frozenset({"reproduction", "coproduction", "postproduction", "overproduction",
                                    "underproduction"})
_NAME_SEGMENTS = re.compile(r"[a-z0-9]+")
# Environment variables that select where later commands in the chain go:
# `export AWS_PROFILE=prod-admin; aws ...`, `NODE_ENV=production node x`.
_TARGET_VARIABLES = re.compile(
    r"(?:^|[\s;&|(])(?:export\s+)?(?:AWS_PROFILE|AWS_DEFAULT_PROFILE|KUBECONFIG|KUBE_CONTEXT|"
    r"CLOUDSDK_CORE_PROJECT|AZURE_SUBSCRIPTION_ID|ARM_SUBSCRIPTION_ID|NODE_ENV|RAILS_ENV|APP_ENV|"
    r"ENVIRONMENT|ENV|STAGE|ASPNETCORE_ENVIRONMENT|DEPLOY_ENV)=[\"']?([^\s;&|\"']+)"
)


# Options that pick the account, subscription, project or cluster a command acts
# in, by tool; their value decides the environment for everything else named.
_SCOPE_OPTIONS = {
    "aws": ("--profile",),
    "az": ("--subscription", "-g", "--resource-group"),
    "gcloud": ("--project",),
    "kubectl": ("--context",), "oc": ("--context",), "helm": ("--kube-context",),
}
_NON_PRODUCTION_WORDS = frozenset({"dev", "develop", "development", "uat", "stage", "staging", "stg", "test",
                                   "testing", "qa", "sandbox", "sbx", "demo", "local", "nonprod", "preprod"})


def _names_non_production_scope(command: Command) -> bool:
    """Whether the command's scope options name a non-production environment and none names production."""
    names = _SCOPE_OPTIONS.get(command.command_name.lower(), ())
    values = [value for value in (option_of(command, name) for name in names) if value]
    if not values or any(names_production(value) for value in values):
        return False
    return any(segment in _NON_PRODUCTION_WORDS
               for value in values for segment in _NAME_SEGMENTS.findall(value.lower()))


def names_production(word: str) -> bool:
    """`shop-prod`, `prod`, `env=production`, `shopprod`, `shopproduction` yes; `nonprod`, `products` no."""
    for segment in _NAME_SEGMENTS.findall(word.lower()):
        if segment in _PRODUCTION_WORDS:
            return True
        if (segment.endswith(("prod", "production")) and not segment.startswith(_NOT_PRODUCTION_PREFIXES)
                and segment not in _PRODUCTION_LOOKALIKES):
            return True
    return False


def targets_production(command: Command, call: ToolCall | None = None) -> bool:
    """
    Whether this command, as written, names a production target: any of its
    own words (`--context prod`, `-g app-rg-prod`, `user@prod-db`,
    `-f env=prod`), or a target variable assigned earlier in the same call.
    A name heuristic: it reads names, it cannot know what an alias points at.

    An explicit non-production scope wins: under `--profile shop-dev`, a
    resource called `copy-of-prod-db` lives in the dev account. (This plugin's
    own rule, not a standard.)
    """
    if _names_non_production_scope(command):
        return False
    for token in command.argv:
        if len(token) > 200 or any(ch.isspace() for ch in token):
            continue
        if names_production(token):
            return True
        # `CTX=shop-prod; kubectl --context "$CTX" ...`: the value assigned in this call.
        variable = _VAR_REF.match(token.strip("'\""))
        if variable and call is not None and call.command:
            assigned = re.search(rf"(?:^|[\s;&|(]){re.escape(variable.group(1))}=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)",
                                 call.command)
            if assigned and names_production(assigned.group(1).strip("'\"")):
                return True
    if call is not None and call.command:
        for value in _TARGET_VARIABLES.findall(call.command):
            if names_production(value):
                return True
    return False


SUMMARY_VALUE_MAX = 40


def is_sensitive_path(path: str | None) -> bool:
    """
    True when `path` names a credential or key file (`.env`, `id_rsa`, `*.pem`,
    `~/.aws/...`). Templates (`.env.example`) and public keys are not. Shared by
    every matcher that judges a path, so they agree on what a credential file is.
    """
    if not path:
        return False
    normalised = str(path).replace("\\", "/").rstrip("/")
    # A glob is judged by what it can match: `.env*` covers `.env`, `*.pem` a key.
    basename = posixpath.basename(normalised).lower().replace("*", "").replace("?", "")
    segments = {segment.lower() for segment in normalised.split("/")}

    if basename.endswith(_TEMPLATE_SUFFIXES) or basename.endswith(_PUBLIC_SUFFIXES):
        return False
    if _PUBLIC_CERTIFICATE.search(basename):
        return False
    if basename in _SENSITIVE_BASENAMES:
        return True
    if basename.endswith(_SENSITIVE_SUFFIXES):
        return True
    if basename.startswith(_ENV_PREFIX):
        return True
    if basename.startswith(_SERVICE_ACCOUNT_PREFIXES) and basename.endswith(".json"):
        return True
    if ".aws" in segments and basename in _AWS_SETTINGS_FILES:
        return False
    if ".ssh" in segments and basename in _SSH_PUBLIC_FILES:
        return False
    return bool(segments & _SENSITIVE_DIRS)


# A redirect operator as the parser leaves it in argv (`>`, `2>>`, `&>`, `<`).
# One ending in `&` duplicates a descriptor (`2>&1`): its target is not a file.
_REDIRECT_TOKEN = re.compile(r"^(?:\d*|&)(?:>>?\|?|<<?<?)&?$")


def _redirect_pairs(command: Command) -> list[tuple[str, str]]:
    """(operator, target) for each file redirect, confirmed against command.redirects."""
    known = set(command.redirects)
    argv = command.argv
    return [(op, target) for op, target in zip(argv, argv[1:])
            if _REDIRECT_TOKEN.match(op) and not op.endswith("&") and target in known]


def written_redirects(command: Command) -> list[str]:
    """Files this command writes through a redirect (`> f`, `>> f`, `2> f`); never `< f`."""
    return [target for op, target in _redirect_pairs(command) if ">" in op]


def without_redirects(tokens: Sequence[str]) -> list[str]:
    """`tokens` with every redirect operator and its target removed (`x 2> /dev/null` -> `x`)."""
    kept: list[str] = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if _REDIRECT_TOKEN.match(token):
            skip = True     # its target: a file, or a descriptor after `>&`
            continue
        kept.append(token)
    return kept


def summary_value(value: str | None) -> str | None:
    """A value fit for a one-line summary: unquoted and cut to a readable length, or None."""
    if not value:
        return None
    value = str(value).strip("'\"")
    if not value:
        return None
    return value if len(value) <= SUMMARY_VALUE_MAX else value[:SUMMARY_VALUE_MAX - 3] + "..."


SUMMARY_PATH_MAX = 48


def summary_path(value: str | None) -> str | None:
    """A path fit for a one-line summary: whole when short, else `.../` and its last segments."""
    if not value:
        return None
    value = str(value).strip("'\"")
    if len(value) <= SUMMARY_PATH_MAX:
        return value or None
    tail = value.replace("\\", "/")[-(SUMMARY_PATH_MAX - 4):]
    if "/" in tail:
        tail = tail[tail.index("/") + 1:] or tail
    return f".../{tail}"


def option_of(command: Command | None, *names: str) -> str | None:
    """
    The value given to the first of `names` (lowercase) on `command`, as
    written (`--name x` or `--name=x`; option names match in any case, as
    PowerShell's do), unquoted. None when no such option has a value.
    """
    if command is None:
        return None
    argv = command.argv
    for name in names:
        for index, token in enumerate(argv):
            option, eq, value = token.partition("=")
            if option.lower() != name:
                continue
            if not eq:
                value = argv[index + 1] if index + 1 < len(argv) else ""
            value = value.strip("'\"")
            if value:
                return value
    return None


def asks_for_help(command: Command) -> bool:
    """
    `--help` or `-h` as written, before any `--`. Case matters (`-H` is curl's
    header and docker's host), and what follows `--` belongs to the command run
    inside (`kubectl run x -- mysql -h db`).
    """
    for token in command.argv:
        if token == "--":
            return False
        if token in ("--help", "-h"):
            return True
    return False


# -- commands run somewhere else ----------------------------------------------
#
# `docker exec db psql -c "..."`, `kubectl exec pod -- psql ...` and
# `ssh host "psql ..."` run their command in a container or on another machine.
# The shell parser deliberately does not report those inner commands as this
# machine's commands; matchers that need them (the database matchers, remote
# execution) ask for them here.
_CONTAINER_TOOLS = frozenset({"docker", "podman", "nerdctl"})
_CONTAINER_EXEC_VALUE_OPTIONS = frozenset({
    "-e", "--env", "--env-file", "-u", "--user", "-w", "--workdir", "--detach-keys",
})
_KUBE_TOOLS = frozenset({"kubectl", "oc"})
SSH_VALUE_OPTIONS = frozenset({
    "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o", "-p",
    "-Q", "-R", "-S", "-W", "-w", "-B",
})


@dataclass(frozen=True)
class RemoteRun:
    """A command another place runs: where (`channel`), the wrapper, and what it runs."""

    channel: str                 # docker_exec | kubectl_exec | ssh
    wrapper: Command
    commands: tuple[Command, ...]


def _parse_words(words: Sequence[str], stdin: str = "") -> tuple[Command, ...]:
    """Re-parse argv words as a command line; a here-document fed to the wrapper feeds the first command."""
    if not words:
        return ()
    line = " ".join(_quote(word) for word in words)
    commands = parse_chain(line, POSIX)
    if stdin and commands and not commands[0].stdin:
        from dataclasses import replace
        commands = (replace(commands[0], stdin=stdin),) + commands[1:]
    return commands


def _quote(word: str) -> str:
    if word and all(ch.isalnum() or ch in "-_./:=@,%+" for ch in word):
        return word
    return "'" + word.replace("'", "'\"'\"'") + "'"


# The last call's answer: several matchers ask about the same call in a row.
_LAST_REMOTE: list = [None, []]


def remote_runs(call: ToolCall) -> list[RemoteRun]:
    """Every command this call runs inside a container or on another machine, by wrapper."""
    if _LAST_REMOTE[0] is call:
        return _LAST_REMOTE[1]
    runs = _remote_runs(call)
    _LAST_REMOTE[0], _LAST_REMOTE[1] = call, runs
    return runs


def _remote_runs(call: ToolCall) -> list[RemoteRun]:
    runs: list[RemoteRun] = []
    for command in call.commands:
        name = command.command_name
        argv = command.argv
        if name in _CONTAINER_TOOLS:
            start = _exec_start(argv)
            if start is None:
                continue
            i = start
            while i < len(argv) and argv[i].startswith("-"):
                option = argv[i].partition("=")[0]
                i += 2 if option in _CONTAINER_EXEC_VALUE_OPTIONS and "=" not in argv[i] else 1
            inner = argv[i + 1:]          # argv[i] is the container
            if inner:
                runs.append(RemoteRun("docker_exec", command, _parse_words(inner, command.stdin)))
        elif name in _KUBE_TOOLS and "exec" in command.subcommand_path[:4] and "--" in argv:
            inner = argv[argv.index("--") + 1:]
            if inner:
                runs.append(RemoteRun("kubectl_exec", command, _parse_words(inner, command.stdin)))
        elif name == "ssh":
            operands = BaseMatcher.operands_from_subcommand(command, SSH_VALUE_OPTIONS)
            if len(operands) > 1:
                text = " ".join(operands[1:])
                runs.append(RemoteRun("ssh", command, parse_chain(text, POSIX)))
            elif operands and command.stdin:
                runs.append(RemoteRun("ssh", command, parse_chain(command.stdin, POSIX)))
    return runs


def _exec_start(argv: Sequence[str]) -> int | None:
    """Index after `exec` in `docker exec`, `docker container exec`, `docker compose exec`."""
    for i, token in enumerate(argv[:3]):
        if token == "exec":
            return i + 1
        if not (token in ("container", "compose") or token.startswith("-")):
            return None
    return None


@dataclass(frozen=True)
class MatchResult:
    """
    What a matcher found.

    `target` is what rules scope on (`target_path: {contains: "/prod"}`).
    `evidence` is what makes an audit row explainable.
    """

    operation: str
    target: str | None = None
    evidence: str = ""
    command_name: str | None = None
    facts: dict = field(default_factory=dict)
    # The exact command that hit, so a rule reads THAT command's flags: in
    # `rm -f a.txt; rm -rf build` a name lookup would pair a hit with the wrong rm.
    command: Command | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.evidence and len(self.evidence) > MAX_EVIDENCE:
            object.__setattr__(self, "evidence", self.evidence[:MAX_EVIDENCE] + "...")


class BaseMatcher(ABC):
    """
    Abstract base for every matcher.

    Subclasses MUST implement `match()` and declare:
        OPERATION       taxonomy name - PUBLIC INTERFACE, renaming is breaking
        DOMAIN          fs, exec, vcs, mcp, net, db, infra, sec, sys, devflow
        DESCRIPTION     one line, shown in taxonomy.json and used as a reason fallback
        APPLIES_TO      operation KINDS this can fire for (never platform tool names)
        DEFAULT_ACTION  guidance published in taxonomy.json, NOT a fallback:
                        the profile's tables decide every call
        DEFAULT_ALERT   critical | warn | info
        FACTS           names this matcher puts in MatchResult.facts, for rules
                        to scope on. Declared so a profile typo fails at load
    """

    OPERATION: str = ""
    DOMAIN: str = ""
    DESCRIPTION: str = ""
    APPLIES_TO: tuple[str, ...] = ()
    DEFAULT_ACTION: str = "allow"
    DEFAULT_ALERT: str = "info"
    FACTS: tuple[str, ...] = ()

    @abstractmethod
    def match(self, call: ToolCall) -> MatchResult | None:
        """Return a MatchResult if this operation is present, else None."""
        ...

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        """
        What the call does, in a few plain words built from the hit's target and
        facts (`push to main on origin`), for notifications. None means the
        notification shows the command as written.
        """
        return None

    # ── helpers available to every matcher ────────────────────────────────

    def hit(
        self,
        *,
        target: str | None = None,
        evidence: str = "",
        command: Command | None = None,
        **facts,
    ) -> MatchResult:
        """Build a MatchResult for this matcher's operation."""
        return MatchResult(
            operation=self.OPERATION,
            target=target,
            evidence=evidence or (command.raw if command else ""),
            command_name=command.command_name if command else None,
            facts=facts,
            command=command,
        )

    @staticmethod
    def commands_named(call: ToolCall, names: Iterable[str]) -> list[Command]:
        """Every parsed command whose resolved name is one of `names`."""
        wanted = frozenset(names)
        return [c for c in call.commands if c.command_name in wanted]

    @staticmethod
    def has_token_run(path: Sequence[str], run: Sequence[str]) -> bool:
        """
        Whether `run` appears as a CONTIGUOUS token run in `path`.

        The same semantics the `contains` operator gives a token-list field, so
        a matcher and a rule agree on what "contains" means. Robust to flag
        position: `aws --profile p s3 rm x` still contains the run (s3, rm).
        """
        run = list(run)
        if not run or len(run) > len(path):
            return False
        return any(list(path[i:i + len(run)]) == run for i in range(len(path) - len(run) + 1))

    @classmethod
    def subcommand_is(cls, command: Command, *run: str) -> bool:
        """`subcommand_is(c, "push")` / `subcommand_is(c, "s3", "rm")`."""
        return cls.has_token_run(command.subcommand_path, run)

    @staticmethod
    def operands_from_subcommand(
        command: Command, value_flags: frozenset[str] | set[str] = frozenset(),
    ) -> tuple[str, ...]:
        """
        The operands starting AT the subcommand, so [0] is `push` in
        `git -C /repo push` and `delete` in `kubectl -n prod delete pod x`.

        The parser cannot know which options take a value, so a global
        option's value lands in `subcommand_path` ahead of the subcommand;
        `value_flags` names the global options that do, and their values are
        dropped. Anchoring on [0] is what keeps `git log --grep rebase`,
        `npm run publish` and `kubectl auth can-i delete pods` from reading as
        a rebase, a publish and a delete.
        """
        skipped = 0
        argv = command.argv
        i = 0
        while i < len(argv):
            token = argv[i]
            if token == "--" or not token.startswith("-") or len(token) < 2:
                break
            name, eq, value = token.partition("=")
            if eq:
                if value:
                    skipped += 1   # the parser kept the inline value as an operand
            elif name in value_flags and i + 1 < len(argv):
                skipped += 1
                i += 2
                continue
            i += 1
        operands = tuple(command.subcommand_path[skipped:])
        # `kubectl $NS exec pod`, `git $R log`: a variable holding global
        # options sits where the subcommand goes. A leading word that is only
        # a variable is skipped when more words follow it.
        while len(operands) > 1 and _ONLY_VARIABLE.match(operands[0]):
            operands = operands[1:]
        return operands

    @staticmethod
    def starts_with_run(path: Sequence[str], run: Sequence[str]) -> bool:
        """Whether `path` begins with the token run `run`."""
        run = list(run)
        return bool(run) and list(path[:len(run)]) == run

    @staticmethod
    def reads_code_from_stdin(command: Command) -> bool:
        """
        Whether this command runs whatever arrives on its standard input as
        code: `sh`, `bash -s`, a bare `python`/`node`, `powershell -`, and
        PowerShell's `| iex`. `python -m json.tool`, `python -c ...` and
        `bash script.sh` read their input as data (or not at all).
        """
        name = command.command_name
        operands = command.subcommand_path
        flags = command.flags
        if name in STDIN_SHELLS:
            return "-c" not in flags and (not operands or operands[0] == "-" or "-s" in flags)
        if name in STDIN_INTERPRETERS:
            code_flags = {"-c", "-m", "-e", "-E", "--eval", "-p", "--print", "-r"}
            return not (flags & code_flags) and (not operands or operands[0] == "-")
        if name in ("powershell", "pwsh"):
            return "-file" not in flags and (not operands or operands[0] == "-")
        if name == "eval" and command.dialect == "powershell":
            return not operands   # `iwr url | iex`
        return False

    @staticmethod
    def compile_any(patterns: Iterable[str], flags: int = re.IGNORECASE) -> re.Pattern:
        """
        One alternation instead of N patterns. Call it at class-definition
        time, never inside match(): matching runs before every governed call.
        """
        return re.compile("|".join(f"(?:{p})" for p in patterns), flags)
