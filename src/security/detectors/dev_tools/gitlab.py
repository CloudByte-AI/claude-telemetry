"""
GitLab Detector.

GitLab uses a rich set of token types, each with a distinctive prefix.
Most share the same format: prefix + 20 alphanumeric chars.
Two special-length tokens: gloas- (64 chars) and glagent- (50 chars).
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_GL_CHARSET  = r"[a-zA-Z0-9]"
_GL_STANDARD = 20   # length after prefix for most token types


@register_detector
class GitLabDetector(BaseDetector):
    CATEGORY           = "GitLab"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "GitLab access tokens, deploy tokens, runner tokens, and agent tokens"
    DOMAIN             = "Developer Tools"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Personal / Project / Group Access Token",
            label="GITLAB_ACCESS_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glpat-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab personal, project, or group access token — grants Git and API access",
            example="glpat-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Deploy Token",
            label="GITLAB_DEPLOY_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("gldt-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab deploy token — read-only access to pull packages or container images",
            example="gldt-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Runner Authentication Token",
            label="GITLAB_RUNNER_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glrt-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab runner authentication token — used by CI/CD runners to authenticate with GitLab",
            example="glrt-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Runner Authentication Token (v2)",
            label="GITLAB_RUNNER_TOKEN_V2",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glrtr-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab runner authentication token (v2 format)",
            example="glrtr-[EXAMPLE]",
        ),
        TokenDefinition(
            type="CI/CD Job Token",
            label="GITLAB_CI_JOB_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glcbt-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab CI/CD job token — temporary token available during CI pipeline jobs",
            example="glcbt-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Pipeline Trigger Token",
            label="GITLAB_PIPELINE_TRIGGER_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glptt-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab pipeline trigger token — used to trigger CI pipelines via API",
            example="glptt-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Feed Token",
            label="GITLAB_FEED_TOKEN",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glft-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab feed token — read-only access to RSS/Atom feeds",
            example="glft-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Incoming Mail Token",
            label="GITLAB_INCOMING_MAIL_TOKEN",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glimt-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab incoming email token — creates issues/comments via email",
            example="glimt-[EXAMPLE]",
        ),
        TokenDefinition(
            type="SCIM Token",
            label="GITLAB_SCIM_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glsoat-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab SCIM token — used for SCIM-based user provisioning (SSO/SAML)",
            example="glsoat-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Workspace Token",
            label="GITLAB_WORKSPACE_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glwt-", _GL_CHARSET, _GL_STANDARD),
            description="GitLab workspace token — access to GitLab workspaces",
            example="glwt-[EXAMPLE]",
        ),
        TokenDefinition(
            type="OAuth Application Secret",
            label="GITLAB_OAUTH_APP_SECRET",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("gloas-", _GL_CHARSET, 64),
            description="GitLab OAuth application secret — used in OAuth 2.0 flows for third-party apps",
            example="gloas-[EXAMPLE]",
        ),
        TokenDefinition(
            type="Agent for Kubernetes Token",
            label="GITLAB_KUBERNETES_AGENT_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("glagent-", _GL_CHARSET, 50),
            description="GitLab Kubernetes Agent token — authenticates the GitLab agent running inside a Kubernetes cluster",
            example="glagent-[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return [
            "glpat-", "gldt-", "glrt-", "glrtr-", "glcbt-", "glptt-",
            "glft-", "glimt-", "glsoat-", "glwt-", "gloas-", "glagent-",
        ]
