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

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._TOOLS):
            path = command.subcommand_path
            is_delete = (
                self.has_token_run(path, ("delete",))
                or (command.command_name == "helm" and self.has_token_run(path, ("uninstall",)))
            )
            if not is_delete:
                continue
            lowered = {f.lower() for f in command.flags}
            resource = next((o for o in path if o not in ("delete", "uninstall")), None)
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
    _DANGEROUS = frozenset({"--privileged", "--pid", "--net", "--network", "--cap-add", "--userns"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in self.commands_named(call, self._TOOLS):
            if not (self.has_token_run(command.subcommand_path, ("run",))
                    or self.has_token_run(command.subcommand_path, ("create",))):
                continue
            lowered = {f.lower() for f in command.flags}
            if "--privileged" not in lowered:
                # A host mount is the other way a container reaches the host.
                host_mount = any(
                    operand.startswith("/") and ":" in operand
                    for operand in command.subcommand_path
                )
                if not host_mount:
                    continue
                return self.hit(
                    target="host_mount", command=command, technique="host_mount",
                )
            return self.hit(
                target="privileged", command=command, technique="privileged",
                flags=sorted(lowered & self._DANGEROUS),
            )
        return None
