"""
Infrastructure matchers.

`infra.terraform.destroy` ships at `ask` with a `critical` alert, unlike most
matchers: it is the most expensive operation to get wrong, and an `ask` costs
seconds.
"""

from __future__ import annotations

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall


@register_matcher
class TerraformDestroyMatcher(BaseMatcher):
    OPERATION = "infra.terraform.destroy"
    DOMAIN = "infra"
    DESCRIPTION = "Tears down provisioned infrastructure"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("tool", "auto_approved")

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
class DockerPrivilegedMatcher(BaseMatcher):
    OPERATION = "infra.docker.privileged"
    DOMAIN = "infra"
    DESCRIPTION = "Runs a container with privileged access to the host"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    FACTS = ("technique", "flags")

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
            lowered = {f.lower() for f in command.flags}
            if "--privileged" in lowered:
                return self.hit(target="privileged", command=command, technique="privileged",
                                flags=["--privileged"])
            shared = self._shared_namespaces(command)
            if shared or "--cap-add" in lowered or "--device" in lowered:
                found = sorted(set(shared) | (lowered & {"--cap-add", "--device"}))
                return self.hit(target=found[0], command=command, technique="host_namespace", flags=found)
            mount = self._host_mount(command)
            if mount:
                return self.hit(target=mount, command=command, technique="host_mount", flags=["--volume"])
        return None

    def _shared_namespaces(self, command) -> list[str]:
        found = []
        argv = command.argv
        for i, token in enumerate(argv):
            name, eq, value = token.partition("=")
            if name.lower() in self._HOST_NAMESPACES:
                value = value if eq else (argv[i + 1] if i + 1 < len(argv) else "")
                if value.lower() == "host":
                    found.append(name.lower())
        return found

    def _host_mount(self, command) -> str | None:
        """The host side of a -v/--volume/--mount that exposes the host itself."""
        argv = command.argv
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
