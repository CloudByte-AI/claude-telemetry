"""
System matchers.

`sys.package.install` ships at `allow`: installing a dependency is the most
common legitimate thing an agent does. It is still worth DETECTING, because a
global install is a common supply-chain foothold and the audit trail is the
point.
"""

from __future__ import annotations

from src.guardrails.matchers.base import BaseMatcher, MatchResult
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall


@register_matcher
class PackageInstallMatcher(BaseMatcher):
    OPERATION = "sys.package.install"
    DOMAIN = "sys"
    DESCRIPTION = "Installs a package or dependency"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "info"
    FACTS = ("manager", "is_global", "package_count")

    # manager -> token runs that install
    _MANAGERS: dict[str, tuple[tuple[str, ...], ...]] = {
        "npm": (("install",), ("i",), ("add",), ("ci",)),
        "pnpm": (("install",), ("add",), ("i",)),
        "yarn": (("add",), ("install",)),
        "bun": (("install",), ("add",), ("i",)),
        "pip": (("install",),),
        "pip3": (("install",),),
        "uv": (("pip", "install"), ("add",)),
        "poetry": (("add",), ("install",)),
        "gem": (("install",),),
        "cargo": (("install",), ("add",)),
        "go": (("install",), ("get",)),
        "apt": (("install",),), "apt-get": (("install",),),
        "yum": (("install",),), "dnf": (("install",),),
        "brew": (("install",),), "choco": (("install",),),
        "winget": (("install",),), "scoop": (("install",),),
        "composer": (("require",), ("install",)),
        "nuget": (("install",),), "dotnet": (("add",),),
    }

    _GLOBAL_FLAGS = frozenset({"-g", "--global", "--user", "--system"})

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            runs = self._MANAGERS.get(command.command_name)
            if not runs:
                continue
            for run in runs:
                if not self.has_token_run(command.subcommand_path, run):
                    continue
                lowered = {f.lower() for f in command.flags}
                packages = [
                    operand for operand in command.subcommand_path
                    if operand not in run
                ]
                return self.hit(
                    target=packages[0] if packages else command.command_name,
                    command=command,
                    manager=command.command_name,
                    is_global=bool(lowered & self._GLOBAL_FLAGS),
                    package_count=len(packages),
                )
        return None
