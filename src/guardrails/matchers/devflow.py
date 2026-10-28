"""
Development-workflow matchers.

`devflow.publish` is irreversible: a package published to a public registry
cannot be truly unpublished, and the version number is burned either way.
"""

from __future__ import annotations

from src.guardrails.matchers.base import (BaseMatcher, MatchResult, asks_for_help, option_of, summary_value,
                                          targets_production)
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall

_CONTAINER_BUILDERS = frozenset({"docker", "podman", "nerdctl", "buildah"})
# Global options that take a value, ahead of the subcommand: `docker -H tcp://host push`.
_CONTAINER_GLOBALS = frozenset({"-H", "--host", "--context", "-c", "--config", "-l", "--log-level", "--url",
                                "--connection", "--namespace", "-n"})
_IMAGE_COPIERS = frozenset({"skopeo", "crane"})
_DRY_RUN = frozenset({"--dry-run", "--dryrun"})


def _option(argv, names: tuple[str, ...]) -> str | None:
    """The value of the first of `names` (`-t img`, `--tag=img`)."""
    for i, token in enumerate(argv):
        option, eq, value = token.partition("=")
        if option in names:
            return value if eq else (argv[i + 1] if i + 1 < len(argv) else None)
    return None


def _image_push(command) -> tuple[str, str | None] | None:
    """(tool, image) when this command pushes a container image to a registry."""
    name = command.command_name.lower()
    path = BaseMatcher.operands_from_subcommand(command, _CONTAINER_GLOBALS)
    flags = command.flags
    if name in _CONTAINER_BUILDERS:
        if path[:1] == ("push",) or path[:2] in (("image", "push"), ("compose", "push")):
            return name, path[2] if path[:1] != ("push",) and len(path) > 2 else (path[1] if len(path) > 1 else None)
        builds = path[:1] == ("build",) or path[:2] in (("buildx", "build"), ("buildx", "bake"), ("image", "build"))
        if builds and ("--push" in flags or any(a.startswith("--output=type=registry") or a == "type=registry"
                                                 for a in command.argv)):
            return name, _option(command.argv, ("-t", "--tag"))
        return None
    if name in _IMAGE_COPIERS and path[:1] in (("copy",), ("cp",), ("push",), ("sync",)):
        return name, path[-1] if len(path) > 1 else None
    return None


@register_matcher
class PublishMatcher(BaseMatcher):
    OPERATION = "devflow.publish"
    DOMAIN = "devflow"
    DESCRIPTION = "Publishes an artifact to a package registry"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # artifact_kind: package (npm, PyPI, crates, Maven...), image (a container image
    # pushed to a registry, by any route: docker push, buildx --push, skopeo
    # copy) or release (gh release create).
    FACTS = ("publisher", "artifact", "artifact_kind")

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
        "mvnw": (("deploy",),),
        "mvnw.cmd": (("deploy",),),
        "gradle": (("publish",),),
        "gradlew": (("publish",),),
        "gradlew.bat": (("publish",),),
        "dotnet": (("nuget", "push"),),
        "nuget": (("push",),),
        "gh": (("release", "create"),),
    }

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            image = _image_push(command)
            if image and not ({f.lower() for f in command.flags} & _DRY_RUN or asks_for_help(command)):
                tool, artifact = image
                return self.hit(target=artifact or tool, command=command, publisher=tool, artifact=artifact,
                                artifact_kind="image")
        for command in call.commands:
            runs = self._PUBLISHERS.get(command.command_name)
            if not runs:
                continue
            # The subcommand itself, so a package.json script named publish
            # (`<npm> run publish`) and a package NAMED publish are not
            # publishes. Maven and Gradle take a list of goals/tasks, so any
            # position counts for them.
            path = command.subcommand_path
            # The project wrappers (./gradlew, ./mvnw) are the same tools.
            anywhere = command.command_name in ("mvn", "mvnw", "mvnw.cmd", "gradle", "gradlew", "gradlew.bat")
            for run in runs:
                if not (self.has_token_run(path, run) if anywhere else self.starts_with_run(path, run)):
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
                    artifact_kind="release" if command.command_name == "gh" else "package",
                )
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        what = facts.get("artifact") or facts.get("publisher")
        if facts.get("artifact_kind") == "image":
            return f"push image {what} to a registry"
        if facts.get("artifact_kind") == "release":
            return f"publish release {what}"
        if not facts.get("artifact"):
            return f"publish this package with {facts.get('publisher')}"
        return f"publish {what} ({facts.get('publisher')})"


# -- CI and deploy pipelines --------------------------------------------------

def _ci_trigger(command) -> tuple[str, str | None] | None:
    """(tool, workflow) when this command starts or re-runs a CI or deploy pipeline."""
    name = command.command_name.lower()
    path = BaseMatcher.operands_from_subcommand(command, frozenset({"-R", "--repo", "--hostname"}))
    argv = command.argv
    if name == "gh":
        if path[:2] in (("workflow", "run"), ("run", "rerun")):
            return "gh", path[2] if len(path) > 2 else None
        if path[:1] == ("api",) and any("/dispatches" in a for a in argv):
            method = (_option(argv, ("-X", "--method")) or "").upper()
            if method == "POST" or {"-f", "-F", "--field", "--raw-field", "--input"} & set(argv):
                return "gh", next((a for a in argv if "/dispatches" in a), None)
        return None
    if name == "glab" and path[:2] in (("ci", "run"), ("ci", "retry"), ("ci", "trigger"), ("pipeline", "run")):
        return "glab", None
    if name == "az" and path[:2] == ("pipelines", "run") or (name == "az" and path[:3] in (
            ("pipelines", "build", "queue"), ("pipelines", "release", "create"))):
        return "az", _option(argv, ("--name", "--definition-name"))
    if name == "aws":
        sub = BaseMatcher.operands_from_subcommand(command, frozenset({"--profile", "--region", "--output", "--query"}))
        if sub[:2] in (("codepipeline", "start-pipeline-execution"), ("codebuild", "start-build"),
                       ("codebuild", "start-build-batch")):
            return "aws", _option(argv, ("--name", "--project-name"))
        return None
    if name == "gcloud" and (path[:2] == ("builds", "submit") or path[:3] == ("builds", "triggers", "run")):
        return "gcloud", path[3] if path[:3] == ("builds", "triggers", "run") and len(path) > 3 else None
    if name in ("curl", "http", "https") and any("/dispatches" in a and "api.github.com" in a for a in argv):
        return name, next((a for a in argv if "/dispatches" in a), None)
    return None


@register_matcher
class CiTriggerMatcher(BaseMatcher):
    OPERATION = "devflow.ci.trigger"
    DOMAIN = "devflow"
    DESCRIPTION = "Triggers a CI or deployment pipeline"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # ci_tool: gh, glab, az, aws, gcloud, curl. workflow: the workflow, pipeline
    # or run named. production: the command names a production target (a
    # workflow input such as `-f env=prod`, or the workflow's file name).
    FACTS = ("ci_tool", "workflow", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if asks_for_help(command):
                continue
            found = _ci_trigger(command)
            if found:
                tool, workflow = found
                return self.hit(target=workflow or tool, command=command, ci_tool=tool, workflow=workflow,
                                production=targets_production(command, call))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        workflow = result.facts.get("workflow")
        prod = " (production)" if result.facts.get("production") else ""
        return f"start pipeline {workflow}{prod}" if workflow else f"start a CI pipeline{prod}"


# tool -> runs that deploy or reconfigure a running service. Matched at the
# start of the command words (after global options).
_DEPLOYS: dict[str, tuple[tuple[str, ...], ...]] = {
    "terraform": (("apply",),), "tofu": (("apply",),), "terragrunt": (("apply",), ("run-all", "apply")),
    "pulumi": (("up",), ("update",)),
    "cdk": (("deploy",),), "cdktf": (("deploy",),),
    "sam": (("deploy",), ("sync",)),
    "serverless": (("deploy",),), "sls": (("deploy",),),
    "netlify": (("deploy",),), "firebase": (("deploy",),),
    "fly": (("deploy",),), "flyctl": (("deploy",),),
    "wrangler": (("deploy",), ("publish",), ("versions", "deploy")),
    "supabase": (("functions", "deploy"), ("secrets", "set"), ("secrets", "unset")),
    "railway": (("up",), ("redeploy",)),
    "eb": (("deploy",),),
    "heroku": (("releases:rollback",), ("container:release",), ("ps:restart",), ("restart",), ("ps:scale",),
               ("config:set",), ("config:unset",)),
    "ansible-playbook": ((),),
    "gcloud": (("run", "deploy"), ("app", "deploy"), ("functions", "deploy"), ("run", "services", "update"),
               ("run", "services", "update-traffic"), ("app", "versions", "migrate")),
    "aws": (("lambda", "update-function-code"), ("lambda", "update-function-configuration"),
            ("lambda", "publish-version"), ("lambda", "update-alias"), ("ecs", "update-service"),
            ("cloudformation", "deploy"), ("cloudformation", "create-stack"), ("cloudformation", "update-stack"),
            ("cloudformation", "execute-change-set"), ("apprunner", "start-deployment"),
            ("apprunner", "update-service"), ("amplify", "start-job"), ("amplify", "start-deployment"),
            ("elasticbeanstalk", "update-environment"), ("codedeploy", "create-deployment"),
            ("lightsail", "create-container-service-deployment")),
    "az": (("webapp", "deploy"), ("webapp", "up"), ("webapp", "restart"), ("webapp", "stop"), ("webapp", "start"),
           ("webapp", "deployment", "source"), ("webapp", "config", "appsettings", "set"),
           ("webapp", "config", "appsettings", "delete"), ("webapp", "config", "set"),
           ("functionapp", "deploy"), ("functionapp", "restart"), ("functionapp", "stop"), ("functionapp", "start"),
           ("functionapp", "deployment", "source"), ("functionapp", "config", "appsettings", "set"),
           ("functionapp", "config", "appsettings", "delete"),
           ("containerapp", "update"), ("containerapp", "up"), ("containerapp", "revision", "restart"),
           ("containerapp", "revision", "activate"), ("containerapp", "revision", "deactivate"),
           ("containerapp", "ingress", "enable"), ("containerapp", "ingress", "disable"),
           ("containerapp", "ingress", "update"), ("containerapp", "secret", "set"),
           ("deployment", "group", "create"), ("deployment", "sub", "create"), ("deployment", "mg", "create"),
           ("deployment", "tenant", "create"), ("spring", "app", "deploy"), ("aks", "upgrade"), ("aks", "scale"),
           ("aks", "nodepool", "scale"), ("aks", "nodepool", "upgrade"), ("vm", "restart"), ("vm", "stop"),
           ("vm", "deallocate"), ("vm", "resize"), ("vm", "redeploy")),
}
_VALUE_GLOBALS = frozenset({"--profile", "--region", "--output", "--query", "--subscription", "--project",
                            "--account", "--config", "--chdir", "-chdir", "--cwd", "--stack", "-s", "--app", "-a"})
_PREVIEWS = frozenset({"--dry-run", "--dryrun", "--preview", "--check", "--syntax-check",
                       "--list-hosts", "--list-tasks", "--list-tags", "--what-if", "--validate-only", "-destroy"})
# Cloud CLIs whose deploy summary names the service acted on, and the options that name it.
_CLOUD_DEPLOY_LABELS = {"aws": "AWS", "az": "Azure", "gcloud": "Google Cloud", "heroku": "Heroku",
                        "supabase": "Supabase"}
_DEPLOY_NAME_OPTIONS = ("--name", "-n", "--function-name", "--service", "--stack-name", "--environment-name",
                        "--application-name", "--app", "-a", "--project-ref")
_AUTO_APPROVE = frozenset({"-auto-approve", "--auto-approve", "--yes", "-y", "--skip-preview", "--no-confirm-changeset",
                           "--non-interactive", "--force"})


def _deploy(command) -> tuple[str, bool] | None:
    """(tool, auto_approved) when this command deploys or reconfigures a running service."""
    name = command.command_name.lower()
    runs = _DEPLOYS.get(name)
    if name == "vercel":
        words = [t for t in command.argv if not t.startswith("-")]
        if not words or words[0] == "deploy":
            return "vercel", "--yes" in command.argv or "-y" in command.argv
        return None
    if runs is None:
        return None
    lowered = {t.lower() for t in command.argv}
    if lowered & _PREVIEWS or any(t.startswith("--dry-run") for t in lowered) or asks_for_help(command):
        return None
    path = BaseMatcher.operands_from_subcommand(command, _VALUE_GLOBALS)
    for run in runs:
        if not BaseMatcher.starts_with_run(path, run) and run:
            continue
        auto = bool(lowered & _AUTO_APPROVE) or any(t.startswith("--require-approval=never") for t in lowered) \
            or ("--require-approval" in lowered and "never" in lowered)
        # `terraform apply PLANFILE` applies a saved plan without asking.
        if name in ("terraform", "tofu", "terragrunt") and _plan_file(command.argv):
            auto = True
        return name, auto
    return None


_TERRAFORM_VALUE_OPTIONS = frozenset({"-var", "-var-file", "-target", "-replace", "-lock-timeout", "-parallelism",
                                      "-state", "-state-out", "-backup", "-chdir"})


def _plan_file(argv) -> bool:
    """Whether `terraform apply` is given a saved plan (an operand after `apply`)."""
    if "apply" not in argv:
        return False
    i = argv.index("apply") + 1
    while i < len(argv):
        token = argv[i]
        if token.startswith("-"):
            i += 2 if token in _TERRAFORM_VALUE_OPTIONS else 1
            continue
        return True
    return False


@register_matcher
class DeployMatcher(BaseMatcher):
    OPERATION = "devflow.deploy"
    DOMAIN = "devflow"
    DESCRIPTION = "Deploys, reconfigures or restarts a live service or its infrastructure"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # deploy_tool: terraform, pulumi, cdk, sam, serverless, aws, az, gcloud, vercel,
    # netlify, firebase, fly, wrangler, supabase, railway, eb, heroku,
    # ansible-playbook. auto_approved: the tool's own confirmation is skipped
    # (-auto-approve, --yes, a saved plan). production: names a production
    # target. Cluster changes are infra.k8s.change; previews and dry runs are
    # not deploys.
    FACTS = ("deploy_tool", "auto_approved", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            found = _deploy(command)
            if found:
                tool, auto = found
                return self.hit(target=tool, command=command, deploy_tool=tool, auto_approved=auto,
                                production=targets_production(command, call) or (
                                    tool in ("vercel", "netlify") and "--prod" in command.argv))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        auto = " without confirmation" if facts.get("auto_approved") else ""
        tool = str(facts.get("deploy_tool") or "")
        if tool in _CLOUD_DEPLOY_LABELS and result.command is not None:
            # `webapp restart shop-web (Azure)`: the cloud CLI's own words, and what they act on.
            path = cls.operands_from_subcommand(result.command, _VALUE_GLOBALS)
            run = next((r for r in _DEPLOYS[tool] if r and cls.starts_with_run(path, r)), None)
            if run:
                name = summary_value(option_of(result.command, *_DEPLOY_NAME_OPTIONS)) or (
                    summary_value(path[len(run)]) if len(path) > len(run) else None)
                where = ", ".join(p for p in (_CLOUD_DEPLOY_LABELS[tool], "production" if facts.get("production") else "") if p)
                return f"{' '.join(run)}{f' {name}' if name else ''} ({where}){auto}"
        prod = " to production" if facts.get("production") else ""
        return f"deploy with {tool}{prod}{auto}"
