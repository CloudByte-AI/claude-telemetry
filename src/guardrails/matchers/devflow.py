"""
Development-workflow matchers.

`devflow.publish` is irreversible: a package published to a public registry
cannot be truly unpublished, and the version number is burned either way.
"""

from __future__ import annotations

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall


@register_matcher
class PublishMatcher(BaseMatcher):
    OPERATION = "devflow.publish"
    DOMAIN = "devflow"
    DESCRIPTION = "Publishes an artifact to a package registry"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    FACTS = ("publisher", "artifact")

    _PUBLISHERS: dict[str, tuple[tuple[str, ...], ...]] = {
        "npm": (("publish",),),
        "pnpm": (("publish",),),
        "yarn": (("publish",), ("npm", "publish")),
        "bun": (("publish",),),
        "twine": (("upload",),),
        "poetry": (("publish",),),
        "uv": (("publish",),),
        "gem": (("push",),),
        "cargo": (("publish",),),
        "docker": (("push",),),
        "podman": (("push",),),
        "helm": (("push",),),
        "mvn": (("deploy",),),
        "gradle": (("publish",),),
        "dotnet": (("nuget", "push"),),
        "nuget": (("push",),),
        "gh": (("release", "create"),),
    }

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            runs = self._PUBLISHERS.get(command.command_name)
            if not runs:
                continue
            for run in runs:
                if not self.has_token_run(command.subcommand_path, run):
                    continue
                lowered = {f.lower() for f in command.flags}
                # A dry run publishes nothing, so it is not this operation.
                if {"--dry-run", "--dryrun"} & lowered:
                    continue
                artifact = next(
                    (o for o in command.subcommand_path if o not in run),
                    None,
                )
                return self.hit(
                    target=artifact or command.command_name,
                    command=command,
                    publisher=command.command_name,
                    artifact=artifact,
                )
        return None
