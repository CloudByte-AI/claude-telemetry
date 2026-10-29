"""
Infrastructure matchers.

`infra.terraform.destroy` ships at `ask` with a `critical` alert, unlike most
matchers: it is the most expensive operation to get wrong, and an `ask` costs
seconds.
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import (BaseMatcher, MatchResult, asks_for_help, option_of, summary_path,
                                          summary_value, targets_production)
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall


# kubectl options that take a value, wherever they sit in the command.
_KUBE_VALUE_OPTIONS = frozenset({
    "-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "-s", "--server", "--token",
    "-o", "--output", "-l", "--selector", "--field-selector", "-f", "--filename", "-k", "--kustomize",
    "--clusterrole", "--role", "--serviceaccount", "--group", "--image", "--replicas", "--grace-period",
    "--timeout", "-c", "--container", "--as", "--as-group", "--request-timeout", "--type", "--port",
    "-p", "--patch", "--patch-file", "--overrides", "--env", "--labels", "--restart",
})
# What an object name looks like; shell leftovers (`2>&1`, `$VAR/x.json`) are not names.
_OBJECT_NAME = re.compile(r"^[A-Za-z0-9][\w.:/@-]*$")
_REDIRECT = re.compile(r"^\d*(?:[<>]{1,2}&?|&>)\S*$")


def _cluster_label(context: str | None) -> str | None:
    """A kube context for a summary: an EKS ARN (`arn:aws:eks:...:cluster/shop`) shows as its cluster name."""
    if not context:
        return None
    if context.startswith("arn:") and "/" in context:
        context = context.rsplit("/", 1)[-1]
    return summary_value(context)


def _object_names(result: MatchResult) -> str:
    """` api` or ` api-0, api-1 and 2 more`: the objects named after the verb and kind, as a suffix."""
    command = result.command
    kind = result.facts.get("resource")
    if command is None or not kind:
        return ""
    argv = list(command.argv)
    if kind not in argv:
        return ""
    names: list[str] = []
    skip = False
    for token in argv[argv.index(kind) + 1:]:
        if token == "--":
            break
        if skip:
            skip = False
            continue
        if _REDIRECT.match(token):
            # `2>&` `1`, `>` `out.log`: a redirect and its target are not objects.
            skip = token.endswith((">", ">&", "<"))
            continue
        if token.startswith("-"):
            skip = "=" not in token and token.lower() in _KUBE_VALUE_OPTIONS
            continue
        if "=" in token or not _OBJECT_NAME.match(token):
            continue
        value = summary_value(token)
        if value:
            names.append(value)
    if not names:
        return ""
    more = f" and {len(names) - 2} more" if len(names) > 2 else ""
    return f" {', '.join(names[:2])}{more}"


@register_matcher
class TerraformDestroyMatcher(BaseMatcher):
    OPERATION = "infra.terraform.destroy"
    DOMAIN = "infra"
    DESCRIPTION = "Tears down provisioned infrastructure"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("tool", "auto_approved")

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        auto = " without confirmation" if result.facts.get("auto_approved") else ""
        return f"destroy infrastructure ({result.facts.get('tool')}){auto}"

    _TOOLS = frozenset({"terraform", "tofu", "terragrunt", "pulumi", "cdk", "cdktf"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._TOOLS):
            path = command.subcommand_path
            lowered = {f.lower() for f in command.flags}
            destroys = (
                self.has_token_run(path, ("destroy",))
                # `terraform apply -destroy` is the same operation spelled as a
                # flag, so it is NOT in subcommand_path - that list is
                # flag-stripped.
                or (self.has_token_run(path, ("apply",)) and "-destroy" in lowered)
            )
            if not destroys:
                continue
            # -auto-approve removes the tool's own confirmation prompt, which is
            # precisely when ours matters most.
            auto = {"-auto-approve", "--auto-approve", "-y", "--yes"} & lowered
            return self.hit(
                target=command.command_name,
                command=command,
                tool=command.command_name,
                auto_approved=bool(auto),
            )
        return None


@register_matcher
class KubernetesDeleteMatcher(BaseMatcher):
    OPERATION = "infra.k8s.delete"
    DOMAIN = "infra"
    DESCRIPTION = "Deletes Kubernetes resources"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("resource", "namespace", "all_resources")

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        where = f" in namespace {facts['namespace']}" if facts.get("namespace") else ""
        resource = facts.get("resource")
        if result.command is not None and result.command.command_name == "helm":
            return f"uninstall Helm release {resource}{where}" if resource else f"uninstall a Helm release{where}"
        what = f"all {resource}" if facts.get("all_resources") else (resource or "resources")
        names = "" if facts.get("all_resources") else _object_names(result)
        return f"delete {what}{names}{where} (Kubernetes)"

    _TOOLS = frozenset({"kubectl", "oc", "helm", "k9s", "kubectl.exe"})
    # Global options that take a value, ahead of the subcommand: `kubectl -n prod delete`.
    _VALUE_OPTIONS = frozenset({
        "-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user", "-s", "--server",
        "--token", "--as", "--as-group", "--request-timeout", "--kube-context", "--registry-config",
    })
    _HELM_DELETES = frozenset({"uninstall", "delete", "del", "un"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._TOOLS):
            sub = self.operands_from_subcommand(command, self._VALUE_OPTIONS)
            if not sub:
                continue
            # The subcommand itself, so `kubectl auth can-i delete pods` and a
            # resource NAMED delete are not deletes.
            is_delete = sub[0] == "delete" or (command.command_name == "helm" and sub[0] in self._HELM_DELETES)
            if not is_delete:
                continue
            lowered = {f.lower() for f in command.flags}
            # A dry run (client or server) deletes nothing; `--dry-run=none` is the real thing.
            if "--dry-run" in lowered and not any(t.lower() == "--dry-run=none" for t in command.argv):
                continue
            resource = sub[1] if len(sub) > 1 else None
            namespace = None
            for index, operand in enumerate(command.argv):
                if operand.lower() in ("-n", "--namespace") and index + 1 < len(command.argv):
                    namespace = command.argv[index + 1]
                    break
            return self.hit(
                target=resource,
                command=command,
                resource=resource,
                namespace=namespace,
                all_resources="--all" in lowered,
            )
        return None


@register_matcher
class KubernetesChangeMatcher(BaseMatcher):
    OPERATION = "infra.k8s.change"
    DOMAIN = "infra"
    DESCRIPTION = "Changes live Kubernetes resources, or which cluster later commands use"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # verb: apply, create, replace, patch, edit, scale, autoscale, set, rollout,
    # annotate, label, cordon, uncordon, drain, taint, expose, run, install,
    # upgrade, rollback, sync, use-context, set-context. resource: what it
    # changes. context / namespace: as given. production: names a production
    # target (`use-context prod` makes every later command a production one).
    # use-context and set-context count only when production.
    FACTS = ("verb", "resource", "namespace", "context", "production")

    _KUBE = frozenset({"kubectl", "oc"})
    _VALUE_OPTIONS = KubernetesDeleteMatcher._VALUE_OPTIONS
    _VERBS = frozenset({
        "apply", "create", "replace", "patch", "edit", "scale", "autoscale", "annotate", "label",
        "cordon", "uncordon", "drain", "taint", "expose", "run",
    })
    _SET = frozenset({"image", "env", "resources", "selector", "serviceaccount", "subject"})
    _ROLLOUT = frozenset({"restart", "undo", "pause", "resume"})
    _CONFIG = frozenset({"use-context", "use", "set-context"})
    _HELM = frozenset({"install", "upgrade", "rollback"})
    _ARGOCD = frozenset({"sync", "rollback", "set", "patch"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._KUBE | {"helm", "argocd"}):
            found = self._verb(command)
            if not found:
                continue
            verb, resource = found
            argv = command.argv
            lowered = {t.lower() for t in argv}
            # A dry run, a diff or help changes nothing; `--dry-run=none` does.
            if any(t.startswith("--dry-run") and t != "--dry-run=none" for t in lowered) or asks_for_help(command):
                continue
            if verb == "set" and "--list" in lowered:
                continue
            context = self._option(argv, ("--context", "--kube-context"))
            production = targets_production(command, call)
            if verb in ("use-context", "set-context"):
                # Switching to a non-production cluster changes nothing live.
                if not production:
                    continue
                context = resource
            return self.hit(target=resource or verb, command=command, verb=verb, resource=resource,
                            namespace=self._option(argv, ("-n", "--namespace")), context=context,
                            production=production)
        return None

    def _verb(self, command) -> tuple[str, str | None] | None:
        name = command.command_name
        sub = self.operands_from_subcommand(command, self._VALUE_OPTIONS)
        if not sub:
            return None
        first, second = sub[0], (sub[1] if len(sub) > 1 else None)
        if name in self._KUBE:
            if first in self._VERBS:
                # `apply -f deploy.yaml` / `replace -f -`: the manifest is what changes.
                manifest = self._option(command.argv, ("-f", "--filename", "-k", "--kustomize"))
                if manifest is not None and (second is None or second == manifest or second == "-"):
                    return first, "a manifest from input" if manifest == "-" else manifest
                return first, second
            if first == "set" and second in self._SET:
                return "set", sub[2] if len(sub) > 2 else second
            if first == "rollout" and second in self._ROLLOUT:
                return "rollout", sub[2] if len(sub) > 2 else None
            if first == "config" and second in self._CONFIG:
                return ("use-context" if second == "use" else second), (sub[2] if len(sub) > 2 else None)
            return None
        if name == "helm" and first in self._HELM:
            return first, second
        if name == "argocd" and first == "app" and second in self._ARGOCD:
            return second, sub[2] if len(sub) > 2 else None
        return None

    @staticmethod
    def _option(argv, names: tuple[str, ...]) -> str | None:
        for i, token in enumerate(argv):
            option, eq, value = token.partition("=")
            if option in names:
                return value if eq else (argv[i + 1] if i + 1 < len(argv) else None)
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        verb = facts.get("verb")
        prod = " (production)" if facts.get("production") else ""
        if verb in ("use-context", "set-context"):
            return f"make {_cluster_label(facts.get('context')) or 'another cluster'} the default cluster{prod}"
        where = f" on {_cluster_label(facts['context'])}" if facts.get("context") else (
            f" in namespace {facts['namespace']}" if facts.get("namespace") else "")
        resource = facts.get("resource")
        if result.command is not None and result.command.command_name == "helm":
            return f"{verb} Helm release {resource}{where}{prod}" if resource else f"{verb} a Helm release{where}{prod}"
        if verb in ("set", "rollout") and result.command is not None:
            # `set image deployment/api`, `rollout restart deployment/api`: name the sub-action.
            operands = cls.operands_from_subcommand(result.command, cls._VALUE_OPTIONS)
            action = operands[1] if len(operands) > 1 else ""
            joiner = " on" if verb == "set" else ""
            return f"{verb} {action}{joiner} {resource or 'resources'}{where}{prod}".replace("  ", " ")
        if verb == "run" and resource:
            return f"run pod {summary_value(resource)}{where}{prod}"
        what = f" {summary_path(resource)}" if resource else ""
        # `create rolebinding ci-admin`: the object's own name follows its kind.
        manifest = option_of(result.command, "-f", "--filename", "-k", "--kustomize")
        if verb in cls._VERBS and resource and "/" not in resource and manifest is None:
            what += _object_names(result)
        return f"{verb}{what}{where}{prod}"


@register_matcher
class DockerPrivilegedMatcher(BaseMatcher):
    OPERATION = "infra.docker.privileged"
    DOMAIN = "infra"
    DESCRIPTION = "Runs a container with privileged access to the host"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("technique", "flags")

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        technique = result.facts.get("technique")
        if technique == "host_mount":
            return f"run a container with {summary_value(result.target) or 'a host folder'} mounted"
        if technique == "host_namespace":
            target = str(result.target or "")
            if target == "--cap-add":
                return "run a container with extra kernel capabilities"
            if target == "--device":
                return "run a container with this machine's devices"
            shared = {"--network": "network", "--net": "network", "--pid": "processes", "--ipc": "IPC",
                      "--uts": "hostname", "--userns": "user namespace"}.get(target, "namespaces")
            return f"run a container sharing this machine's {shared}"
        return "run a privileged container (full access to this machine)"

    _TOOLS = frozenset({"docker", "podman", "nerdctl"})
    _VALUE_OPTIONS = frozenset({"-H", "--host", "--context", "--config", "-l", "--log-level"})
    # Options that share a host namespace when their value is `host`.
    _HOST_NAMESPACES = frozenset({"--pid", "--net", "--network", "--ipc", "--uts", "--userns", "--cgroupns"})
    # A bind mount of one of these hands the container the host itself. A
    # project folder (`-v $PWD:/app`, `-v /home/me/proj:/app`) does not.
    _HOST_PATHS = frozenset({
        "/", "/etc", "/root", "/proc", "/sys", "/dev", "/boot", "/var/run", "/run", "/var/lib/docker",
        "/var/run/docker.sock", "/run/docker.sock", "/usr", "/bin", "/sbin", "/lib", "/home",
        "c:", "c:/", "c:\\", "//./pipe/docker_engine",
    })

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._TOOLS):
            sub = self.operands_from_subcommand(command, self._VALUE_OPTIONS)
            if not (self.starts_with_run(sub, ("run",)) or self.starts_with_run(sub, ("create",))
                    or self.starts_with_run(sub, ("container", "run")) or self.starts_with_run(sub, ("container", "create"))):
                continue
            # Only docker's own options count: everything after the image is
            # the container's command line (`... gitlab/glab auth login --device`).
            options = self._run_options(command.argv)
            lowered = {t.partition("=")[0].lower() for t in options if t.startswith("-")}
            if "--privileged" in lowered:
                return self.hit(target="privileged", command=command, technique="privileged",
                                flags=["--privileged"])
            shared = self._shared_namespaces(options)
            if shared or "--cap-add" in lowered or "--device" in lowered:
                found = sorted(set(shared) | (lowered & {"--cap-add", "--device"}))
                return self.hit(target=found[0], command=command, technique="host_namespace", flags=found)
            mount = self._host_mount(options)
            if mount:
                return self.hit(target=mount, command=command, technique="host_mount", flags=["--volume"])
        return None

    # `docker run` / `create` options that take a value (docs.docker.com).
    _RUN_VALUE_OPTIONS = frozenset({
        "-a", "--attach", "--add-host", "--annotation", "--blkio-weight", "--blkio-weight-device",
        "--cap-add", "--cap-drop", "--cgroup-parent", "--cgroupns", "--cidfile", "--cpu-period",
        "--cpu-quota", "--cpu-rt-period", "--cpu-rt-runtime", "-c", "--cpu-shares", "--cpus",
        "--cpuset-cpus", "--cpuset-mems", "--device", "--device-cgroup-rule", "--device-read-bps",
        "--device-read-iops", "--device-write-bps", "--device-write-iops", "--dns", "--dns-option",
        "--dns-search", "--domainname", "--entrypoint", "-e", "--env", "--env-file", "--expose",
        "--gpus", "--group-add", "--health-cmd", "--health-interval", "--health-retries",
        "--health-start-interval", "--health-start-period", "--health-timeout", "-h", "--hostname",
        "--ip", "--ip6", "--ipc", "--isolation", "--kernel-memory", "-l", "--label", "--label-file",
        "--link", "--link-local-ip", "--log-driver", "--log-opt", "--mac-address", "-m", "--memory",
        "--memory-reservation", "--memory-swap", "--memory-swappiness", "--mount", "--name",
        "--network", "--net", "--network-alias", "--oom-score-adj", "--pid", "--pids-limit",
        "--platform", "-p", "--publish", "--pull", "--restart", "--runtime", "--security-opt",
        "--shm-size", "--stop-signal", "--stop-timeout", "--storage-opt", "--sysctl", "--tmpfs",
        "--ulimit", "-u", "--user", "--userns", "--uts", "-v", "--volume", "--volume-driver",
        "--volumes-from", "-w", "--workdir",
    })

    def _run_options(self, argv: tuple[str, ...]) -> list[str]:
        """The tokens between `run`/`create` and the image: docker's own options and their values."""
        start = next((i + 1 for i, t in enumerate(argv) if t in ("run", "create")), len(argv))
        options: list[str] = []
        i = start
        while i < len(argv):
            token = argv[i]
            if token == "--" or not token.startswith("-"):
                break   # the image; what follows is the container's command
            options.append(token)
            if token in self._RUN_VALUE_OPTIONS and i + 1 < len(argv):
                options.append(argv[i + 1])
                i += 1
            i += 1
        return options

    def _shared_namespaces(self, argv) -> list[str]:
        found = []
        for i, token in enumerate(argv):
            name, eq, value = token.partition("=")
            if name.lower() in self._HOST_NAMESPACES:
                value = value if eq else (argv[i + 1] if i + 1 < len(argv) else "")
                if value.lower() == "host":
                    found.append(name.lower())
        return found

    def _host_mount(self, argv) -> str | None:
        """The host side of a -v/--volume/--mount that exposes the host itself."""
        for i, token in enumerate(argv):
            name, eq, value = token.partition("=")
            if name in ("-v", "--volume"):
                spec = value if eq else (argv[i + 1] if i + 1 < len(argv) else "")
                source = self._volume_source(spec)
            elif name == "--mount":
                spec = value if eq else (argv[i + 1] if i + 1 < len(argv) else "")
                fields = dict(part.split("=", 1) for part in spec.split(",") if "=" in part)
                source = fields.get("source") or fields.get("src") or ""
            else:
                continue
            normalised = source.replace("\\", "/").rstrip("/").lower() or ("/" if source.startswith("/") else "")
            if normalised in self._HOST_PATHS or normalised.startswith(("/etc/", "/proc/", "/sys/", "/root/", "/boot/")):
                return source
        return None

    @staticmethod
    def _volume_source(spec: str) -> str:
        """The host side of `SRC:DST[:mode]`, keeping a Windows drive (`C:/x:/app`)."""
        if len(spec) > 2 and spec[1] == ":" and spec[0].isalpha():
            rest = spec[2:]
            return spec[:2] + rest.split(":", 1)[0]
        return spec.split(":", 1)[0] if ":" in spec else ""
