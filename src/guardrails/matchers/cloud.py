"""
Cloud account matchers: deleting resources, changing who can access them,
opening network access, creating billable resources and changing DNS, across
the AWS, Azure and Google Cloud CLIs (and a few smaller providers).

Each CLI is read the way it is written:
    aws   [global options] <service> <action> ...      `aws --profile p ec2 terminate-instances`
    az    <group words...> <verb> [options]            `az sql server firewall-rule delete --name x`
    gcloud <group words...> <verb> [NAME] [options]    `gcloud compute instances delete vm-1`
so a word inside a value (`--query "Delete..."`, a resource named delete) is
never the verb.
"""

from __future__ import annotations

import re

from src.guardrails.matchers.base import BaseMatcher, MatchResult, asks_for_help, targets_production
from src.guardrails.registry import register_matcher
from src.guardrails.toolcall import KIND_SHELL, ToolCall

# aws options that take a value and may come before the service.
_AWS_VALUE_OPTIONS = frozenset({
    "--profile", "--region", "--output", "--endpoint-url", "--query", "--color", "--ca-bundle",
    "--cli-read-timeout", "--cli-connect-timeout", "--cli-binary-format",
})
_SMALL_PROVIDERS = frozenset({"doctl", "hcloud", "linode-cli", "vultr-cli", "scw", "civo"})


def _aws(command) -> tuple[str, str, tuple[str, ...]] | None:
    """(service, action, rest) for an aws command."""
    sub = BaseMatcher.operands_from_subcommand(command, _AWS_VALUE_OPTIONS)
    if len(sub) < 2:
        return None
    return sub[0].lower(), sub[1].lower(), tuple(sub[2:])


def _words(command) -> list[str]:
    """The command words before the first option: az and gcloud groups and verb (and gcloud's NAME)."""
    words: list[str] = []
    for token in command.argv:
        if token.startswith("-"):
            break
        words.append(token.lower())
    return words


def _has_run(words, run) -> bool:
    return BaseMatcher.has_token_run(words, run)


def _option_value(argv, names: tuple[str, ...]) -> str | None:
    for i, token in enumerate(argv):
        option, eq, value = token.partition("=")
        if option.lower() in names:
            return value if eq else (argv[i + 1] if i + 1 < len(argv) else None)
    return None


def _production(command, call) -> bool:
    return targets_production(command, call)


def _prod_suffix(result: MatchResult) -> str:
    return " (production)" if result.facts.get("production") else ""


_PROVIDER_NAMES = {"aws": "AWS", "az": "Azure", "gcloud": "Google Cloud", "doctl": "DigitalOcean",
                   "hcloud": "Hetzner", "kubernetes": "Kubernetes", "cloudflare": "Cloudflare"}


def _provider(result: MatchResult) -> str:
    provider = str(result.facts.get("provider") or "")
    return _PROVIDER_NAMES.get(provider, provider)


# -- deleting cloud resources -------------------------------------------------

# `admin-delete-user` (Cognito) deletes as an administrator.
_AWS_DELETE_ACTION = re.compile(r"^(?:admin-)?(?:delete|terminate|deregister|purge|batch-delete|release)-")
# Actions that remove a tag, a permission or an attachment, not a resource;
# IAM and security-group rules have their own operations.
_AWS_NOT_A_RESOURCE = re.compile(r"tag|permission|-from-|policy-version")
_AZ_DELETE_VERBS = frozenset({"delete", "purge", "delete-batch"})
# az groups whose delete is an access or network-rule change, not a resource.
_AZ_OTHER_OPERATION = (("firewall-rule",), ("network", "nsg", "rule"), ("role", "assignment"), ("role", "definition"),
                       ("ad",), ("network-rule",), ("access-restriction",), ("dns",), ("private-dns",))


def _cloud_delete(command) -> tuple[str, str, str | None] | None:
    """(provider, service, resource) when this command deletes a cloud resource."""
    name = command.command_name.lower()
    argv = command.argv
    lowered = {f.lower() for f in command.flags}
    if name == "aws":
        found = _aws(command)
        if not found:
            return None
        service, action, rest = found
        if service == "s3":
            if action in ("rm", "rb", "mv"):
                return "aws", "s3", rest[0] if rest else None
            if action == "sync" and "--delete" in lowered:
                return "aws", "s3", rest[-1] if rest else None
            return None
        if service in ("iam", "route53") or action in ("delete-security-group-rule",):
            return None
        if _AWS_DELETE_ACTION.match(action) and not _AWS_NOT_A_RESOURCE.search(action):
            return "aws", service, rest[0] if rest else None
        return None
    if name == "az":
        words = _words(command)
        if words[:1] == ["rest"]:
            method = (_option_value(argv, ("--method", "-m")) or "").lower()
            return ("az", "rest", _option_value(argv, ("--url", "--uri", "-u"))) if method == "delete" else None
        if len(words) >= 2 and words[-1] in _AZ_DELETE_VERBS:
            if any(_has_run(words, run) for run in _AZ_OTHER_OPERATION):
                return None
            return "az", " ".join(words[:-1]), _option_value(argv, ("--name", "-n"))
        return None
    if name == "gcloud":
        words = _words(command)
        if "delete" in words[1:] and not ({"firewall-rules", "iam", "dns"} & set(words)) \
                and not any(w.endswith("-iam-policy-binding") for w in words):
            at = words.index("delete")
            return "gcloud", " ".join(words[:at]), words[at + 1] if len(words) > at + 1 else None
        if _has_run(words, ("storage", "rm")):
            return "gcloud", "storage", words[2] if len(words) > 2 else None
        return None
    if name == "gsutil":
        words = _words(command)
        if words and words[0] in ("rm", "rb"):
            return "gcloud", "storage", next((a for a in argv if a.startswith("gs://")), None)
        return None
    if name in _SMALL_PROVIDERS:
        words = _words(command)
        verb = next((w for w in words if w in ("delete", "rm", "destroy", "remove")), None)
        return (name, " ".join(words[:words.index(verb)]), None) if verb else None
    if name == "rclone":
        words = _words(command)
        if words and words[0] in ("delete", "purge", "deletefile", "rmdir", "rmdirs"):
            return "rclone", words[0], words[1] if len(words) > 1 else None
        return None
    if name in ("fly", "flyctl"):
        words = _words(command)
        if words[:1] == ["destroy"] or words[:2] == ["apps", "destroy"]:
            return "fly", "apps", words[-1] if len(words) > 1 else None
        return None
    if name == "heroku":
        words = _words(command)
        if words[:1] == ["apps:destroy"]:
            return "heroku", "apps", _option_value(argv, ("-a", "--app"))
        return None
    if name == "vercel":
        words = _words(command)
        if words[:1] in (["remove"], ["rm"]):
            return "vercel", "deployment", words[1] if len(words) > 1 else None
        return None
    if name == "supabase":
        words = _words(command)
        if words[:2] in (["projects", "delete"], ["functions", "delete"], ["branches", "delete"]):
            return "supabase", words[0], words[2] if len(words) > 2 else None
        return None
    if name == "netlify":
        words = _words(command)
        if words[:1] == ["sites:delete"]:
            return "netlify", "sites", words[1] if len(words) > 1 else None
        return None
    return None


@register_matcher
class CloudDeleteMatcher(BaseMatcher):
    OPERATION = "infra.cloud.delete"
    DOMAIN = "infra"
    DESCRIPTION = "Deletes a cloud resource"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # provider: aws, az, gcloud, doctl, hcloud, rclone, fly, heroku, vercel,
    # supabase, netlify. service: the service or az/gcloud group (s3, ec2,
    # "sql server", "compute instances"). resource: the name or path given,
    # when there is one. production: names a production target.
    FACTS = ("provider", "service", "resource", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if asks_for_help(command) or any(t.lower() in ("--dry-run", "--dryrun") for t in command.argv):
                continue
            found = _cloud_delete(command)
            if found:
                provider, service, resource = found
                return self.hit(target=resource or f"{provider} {service}", command=command, provider=provider,
                                service=service, resource=resource, production=_production(command, call))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        what = facts.get("resource") or f"{facts.get('service')} resources"
        return f"delete {what} ({_provider(result)}){_prod_suffix(result)}"


# -- identity and access ------------------------------------------------------

_IAM_GRANT = ("create", "attach", "put", "add", "associate", "set", "upload", "enable")
_IAM_REVOKE = ("delete", "detach", "remove", "disassociate", "deactivate", "disable")
_IAM_CHANGE = ("update", "change", "reset")
_AWS_IAM_SERVICES = frozenset({"iam", "sso-admin", "identitystore"})


def _iam_action(verb: str) -> str | None:
    head = verb.split("-", 1)[0]
    if head in _IAM_GRANT:
        return "grant"
    if head in _IAM_REVOKE:
        return "revoke"
    if head in _IAM_CHANGE:
        return "change"
    return None


def _iam_change(command) -> tuple[str, str] | None:
    """(provider, action) when this command changes who can access what."""
    name = command.command_name.lower()
    if name == "aws":
        found = _aws(command)
        if not found:
            return None
        service, action, _ = found
        if service in _AWS_IAM_SERVICES or (service == "organizations" and "policy" in action) \
                or (service == "eks" and action in ("create-access-entry", "associate-access-policy",
                                                    "delete-access-entry", "disassociate-access-policy")):
            if "tag" in action:
                return None
            kind = _iam_action(action)
            return ("aws", kind) if kind else None
        return None
    if name == "az":
        words = _words(command)
        if len(words) < 2:
            return None
        verb = words[-1]
        access_group = (_has_run(words, ("role", "assignment")) or _has_run(words, ("role", "definition"))
                        or words[0] == "ad" or _has_run(words, ("keyvault", "set-policy"))
                        or _has_run(words, ("keyvault", "delete-policy")))
        if not access_group:
            return None
        if verb in ("set-policy", "delete-policy"):
            return "az", "grant" if verb == "set-policy" else "revoke"
        if verb == "create-for-rbac":
            return "az", "grant"
        kind = _iam_action(verb)
        return ("az", kind) if kind else None
    if name == "gcloud":
        words = _words(command)
        binding = next((w for w in words if w.endswith("-iam-policy-binding") or w == "set-iam-policy"), None)
        if binding:
            return "gcloud", "revoke" if binding.startswith("remove") else "grant"
        if words[:2] == ["iam", "service-accounts"] or words[:2] == ["iam", "roles"]:
            verb = next((w for w in words[2:] if _iam_action(w)), None)
            return ("gcloud", _iam_action(verb)) if verb else None
        return None
    if name in ("kubectl", "oc"):
        sub = command.subcommand_path
        if len(sub) >= 2 and sub[0] == "create" and sub[1] in (
                "clusterrolebinding", "rolebinding", "clusterrole", "role", "serviceaccount"):
            if any(t.startswith("--dry-run") and t != "--dry-run=none" for t in command.argv):
                return None
            return "kubernetes", "grant"
        if name == "oc" and sub[:2] == ("adm", "policy") and len(sub) > 2:
            return "kubernetes", "revoke" if sub[2].startswith("remove") else "grant"
    return None


@register_matcher
class IamChangeMatcher(BaseMatcher):
    OPERATION = "infra.iam.change"
    DOMAIN = "infra"
    DESCRIPTION = "Changes identity or access policy"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # provider: aws, az, gcloud, kubernetes. action: grant (create, attach,
    # add, put), revoke (delete, detach, remove) or change (update, reset).
    FACTS = ("provider", "action", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if asks_for_help(command):
                continue
            found = _iam_change(command)
            if found:
                provider, action = found
                return self.hit(target=f"{provider} access", command=command, provider=provider, action=action,
                                production=_production(command, call))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        verbs = {"grant": "grant access", "revoke": "remove access", "change": "change access"}
        return f"{verbs.get(result.facts.get('action'), 'change access')} ({_provider(result)})" \
               f"{_prod_suffix(result)}"


# -- firewalls and network access ---------------------------------------------

# Every address: in any value (aws --ip-permissions JSON, gcloud --source-ranges).
_WORLD_CIDR = re.compile(r"(?:^|[^\d.])(?:0\.0\.0\.0/0|::/0)")
# Options naming where traffic may come from, and the words that mean "anywhere"
# there (only there: `--protocol '*'` is any protocol, not any address).
_SOURCE_OPTIONS = ("--source-address-prefixes", "--source-address-prefix", "--source-ranges", "--cidr",
                   "--cidr-ip", "--remote-address", "-remoteaddress")
_ANYWHERE = frozenset({"*", "internet", "any", "0.0.0.0/0", "::/0"})


def _open_to_world(argv) -> bool:
    if any(_WORLD_CIDR.search(a) for a in argv):
        return True
    source = _option_value(argv, _SOURCE_OPTIONS)
    if source and source.strip("'\"").lower() in _ANYWHERE:
        return True
    return (_option_value(argv, ("--start-ip-address",)) == "0.0.0.0"
            and _option_value(argv, ("--end-ip-address",)) == "255.255.255.255")
_AZ_FIREWALL_GROUPS = (
    ("network", "nsg", "rule"), ("firewall-rule",), ("network-rule",), ("access-restriction",),
    ("redis", "firewall-rules"),
)
_IPTABLES_CHANGES = frozenset({"-A", "-I", "-D", "-R", "-F", "-P", "-X", "-N", "--append", "--insert",
                               "--delete", "--replace", "--flush", "--policy"})


def _firewall_change(command) -> tuple[str, str] | None:
    """(provider, action) when this command changes a firewall or security-group rule."""
    name = command.command_name.lower()
    argv = command.argv
    if name == "aws":
        found = _aws(command)
        if not found:
            return None
        service, action, _ = found
        if service == "ec2" and re.match(r"^(authorize|revoke)-security-group-(ingress|egress)$", action):
            return "aws", "open" if action.startswith("authorize") else "close"
        if service == "ec2" and action in ("modify-security-group-rules", "create-network-acl-entry",
                                           "replace-network-acl-entry", "delete-network-acl-entry"):
            return "aws", "change"
        if service == "lightsail" and action in ("open-instance-public-ports", "put-instance-public-ports"):
            return "aws", "open"
        return None
    if name == "az":
        words = _words(command)
        if words and any(_has_run(words, run) for run in _AZ_FIREWALL_GROUPS):
            verb = words[-1]
            if verb in ("create", "add", "set"):
                return "az", "open"
            if verb in ("delete", "remove"):
                return "az", "close"
            if verb == "update":
                return "az", "change"
        return None
    if name == "gcloud":
        words = _words(command)
        if words[:2] == ["compute", "firewall-rules"] and len(words) > 2 and words[2] in ("create", "update", "delete"):
            return "gcloud", {"create": "open", "delete": "close"}.get(words[2], "change")
        if words[:3] == ["sql", "instances", "patch"] and any(a.startswith("--authorized-networks") for a in argv):
            return "gcloud", "change"
        return None
    if name == "netsh":
        lowered = [a.lower() for a in argv]
        if "advfirewall" in lowered and any(w in lowered for w in ("add", "delete", "set")):
            return "host", "close" if "delete" in lowered else "change"
        return None
    if name in ("new-netfirewallrule", "set-netfirewallrule", "remove-netfirewallrule", "enable-netfirewallrule",
                "disable-netfirewallrule", "set-netfirewallprofile"):
        return "host", "open" if name.startswith(("new", "enable")) else "change"
    if name == "ufw":
        words = _words(command)
        if words and words[0] in ("allow", "deny", "reject", "limit", "delete", "insert", "route", "enable", "disable"):
            return "host", "open" if words[0] in ("allow", "disable") else "change"
        return None
    if name in ("iptables", "ip6tables"):
        if set(argv) & _IPTABLES_CHANGES:
            return "host", "change"
        return None
    if name == "nft":
        words = _words(command)
        if words and words[0] in ("add", "delete", "flush", "insert", "replace"):
            return "host", "change"
        return None
    if name == "firewall-cmd":
        if any(a.startswith(("--add-", "--remove-", "--set-", "--new-", "--delete-")) for a in argv):
            return "host", "change"
    return None


@register_matcher
class FirewallChangeMatcher(BaseMatcher):
    OPERATION = "sec.firewall.change"
    DOMAIN = "sec"
    DESCRIPTION = "Changes firewall or security-group rules"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "critical"
    # provider: aws, az, gcloud or host (this machine's firewall). action: open
    # (adds or allows access), close (removes a rule) or change. open_to_world:
    # the rule names every address (0.0.0.0/0, ::/0, *, Internet, Any).
    FACTS = ("provider", "action", "open_to_world", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if asks_for_help(command):
                continue
            found = _firewall_change(command)
            if found:
                provider, action = found
                world = action != "close" and _open_to_world(command.argv)
                return self.hit(target=f"{provider} firewall", command=command, provider=provider, action=action,
                                open_to_world=bool(world), production=_production(command, call))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        facts = result.facts
        where = "this machine's firewall" if facts.get("provider") == "host" else f"a firewall rule ({_provider(result)})"
        if facts.get("open_to_world"):
            return f"open {where} to the whole internet{_prod_suffix(result)}"
        verb = {"open": "open", "close": "remove"}.get(facts.get("action"), "change")
        return f"{verb} {where}{_prod_suffix(result)}"


# -- billable resources -------------------------------------------------------

_AWS_CREATES: dict[str, frozenset[str]] = {
    "ec2": frozenset({"run-instances", "allocate-address", "create-nat-gateway", "request-spot-instances",
                      "create-vpn-connection", "create-transit-gateway"}),
    "eks": frozenset({"create-cluster", "create-nodegroup"}),
    "rds": frozenset({"create-db-instance", "create-db-cluster", "restore-db-instance-from-db-snapshot",
                      "restore-db-cluster-from-snapshot", "create-db-instance-read-replica"}),
    "elasticache": frozenset({"create-cache-cluster", "create-replication-group", "create-serverless-cache"}),
    "redshift": frozenset({"create-cluster"}),
    "opensearch": frozenset({"create-domain"}),
    "es": frozenset({"create-elasticsearch-domain"}),
    "sagemaker": frozenset({"create-endpoint", "create-notebook-instance", "create-training-job"}),
    "lightsail": frozenset({"create-instances", "create-relational-database"}),
    "emr": frozenset({"create-cluster", "run-job-flow"}),
    "kafka": frozenset({"create-cluster", "create-cluster-v2"}),
    "docdb": frozenset({"create-db-cluster", "create-db-instance"}),
    "neptune": frozenset({"create-db-cluster", "create-db-instance"}),
}
_AZ_BILLABLE = (
    ("vm",), ("vmss",), ("aks",), ("sql", "server"), ("sql", "db"), ("sql", "mi"), ("postgres", "server"),
    ("postgres", "flexible-server"), ("mysql", "server"), ("mysql", "flexible-server"), ("redis",),
    ("redisenterprise",), ("cosmosdb",), ("appservice", "plan"), ("containerapp",), ("containerapp", "env"),
    ("webapp",), ("functionapp",), ("storage", "account"), ("network", "public-ip"),
    ("network", "application-gateway"), ("network", "vnet-gateway"), ("network", "firewall"),
    ("network", "bastion"), ("cognitiveservices", "account"), ("search", "service"), ("synapse", "workspace"),
    ("databricks", "workspace"), ("ml", "workspace"), ("acr",), ("servicebus", "namespace"),
    ("eventhubs", "namespace"),
)
_GCLOUD_BILLABLE = (
    ("compute", "instances"), ("container", "clusters"), ("sql", "instances"), ("redis", "instances"),
    ("filestore", "instances"), ("dataproc", "clusters"), ("composer", "environments"), ("alloydb", "clusters"),
    ("container", "node-pools"),
)


def _cloud_create(command) -> tuple[str, str] | None:
    """(provider, resource kind) when this command creates a resource that costs money while it runs."""
    name = command.command_name.lower()
    if name == "aws":
        found = _aws(command)
        if found and found[1] in _AWS_CREATES.get(found[0], ()):
            return "aws", f"{found[0]} {found[1]}"
        return None
    if name == "az":
        words = _words(command)
        if len(words) >= 2 and words[-1] == "create" and tuple(words[:-1]) in _AZ_BILLABLE:
            return "az", " ".join(words[:-1])
        return None
    if name == "gcloud":
        words = _words(command)
        for group in _GCLOUD_BILLABLE:
            if tuple(words[:len(group)]) == group and len(words) > len(group) and words[len(group)] == "create":
                return "gcloud", " ".join(group)
        return None
    if name == "doctl":
        words = _words(command)
        if words[:3] in (["compute", "droplet", "create"], ["kubernetes", "cluster", "create"]) \
                or words[:2] == ["databases", "create"]:
            return "doctl", " ".join(words[:-1])
        return None
    if name == "hcloud":
        words = _words(command)
        if words[:2] == ["server", "create"]:
            return "hcloud", "server"
    return None


@register_matcher
class CloudCreateMatcher(BaseMatcher):
    OPERATION = "infra.cloud.create"
    DOMAIN = "infra"
    DESCRIPTION = "Creates a cloud resource that is billed while it runs"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "allow"
    DEFAULT_ALERT = "warn"
    # provider: aws, az, gcloud, doctl, hcloud. resource: the kind created
    # (`ec2 run-instances`, `appservice plan`, `compute instances`).
    FACTS = ("provider", "resource", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if asks_for_help(command) or any(
                    t.lower() in ("--dry-run", "--what-if", "--validate-only") for t in command.argv):
                continue
            found = _cloud_create(command)
            if found:
                provider, resource = found
                return self.hit(target=resource, command=command, provider=provider, resource=resource,
                                production=_production(command, call))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        return f"create a billable {result.facts.get('resource')} ({_provider(result)})" \
               f"{_prod_suffix(result)}"


# -- DNS ----------------------------------------------------------------------

_CLOUDFLARE_DNS = re.compile(r"api\.cloudflare\.com/client/v4/zones/[^\s'\"]*/dns_records", re.IGNORECASE)
_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _dns_change(command) -> tuple[str, str] | None:
    """(provider, action) when this command changes DNS records or zones."""
    name = command.command_name.lower()
    argv = command.argv
    if name == "aws":
        found = _aws(command)
        if found and found[0] == "route53" and found[1] in (
                "change-resource-record-sets", "create-hosted-zone", "delete-hosted-zone"):
            return "aws", found[1]
        return None
    if name == "az":
        words = _words(command)
        if len(words) >= 3 and words[0] == "network" and words[1] in ("dns", "private-dns"):
            verb = words[-1]
            if verb in ("create", "delete", "update", "add-record", "remove-record", "set-record"):
                return "az", verb
        return None
    if name == "gcloud":
        words = _words(command)
        if words[:2] in (["dns", "record-sets"], ["dns", "managed-zones"]) and len(words) > 2 \
                and words[2] in ("create", "delete", "update", "transaction", "import"):
            if words[2] == "transaction" and (len(words) < 4 or words[3] not in ("add", "remove", "execute")):
                return None
            return "gcloud", words[2]
        return None
    if name == "doctl":
        words = _words(command)
        if words[:3] == ["compute", "domain", "records"] and len(words) > 3 and words[3] in ("create", "update", "delete"):
            return "doctl", words[3]
        return None
    if name in ("curl", "http", "https", "iwr", "irm", "invoke-webrequest", "invoke-restmethod"):
        if any(_CLOUDFLARE_DNS.search(a) for a in argv):
            method = (_option_value(argv, ("-x", "--request", "-method")) or "").upper()
            if method in _WRITE_METHODS or (name in ("http", "https") and any(a.upper() in _WRITE_METHODS for a in argv)):
                return "cloudflare", method.lower() or "change"
    return None


@register_matcher
class DnsChangeMatcher(BaseMatcher):
    OPERATION = "infra.dns.change"
    DOMAIN = "infra"
    DESCRIPTION = "Changes DNS records"
    APPLIES_TO = (KIND_SHELL,)
    DEFAULT_ACTION = "ask"
    DEFAULT_ALERT = "warn"
    # provider: aws (Route 53), az, gcloud, doctl, cloudflare (its API).
    # action: the change as the tool names it.
    FACTS = ("provider", "action", "production")

    def match(self, call: ToolCall) -> MatchResult | None:
        for command in call.commands:
            if asks_for_help(command):
                continue
            found = _dns_change(command)
            if found:
                provider, action = found
                return self.hit(target=f"{provider} dns", command=command, provider=provider, action=action,
                                production=_production(command, call))
        return None

    @classmethod
    def summary(cls, result: MatchResult) -> str | None:
        return f"change DNS records ({_provider(result)}){_prod_suffix(result)}"
