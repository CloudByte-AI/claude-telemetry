"""
AWS Detector - covers all IAM identifier prefixes and the Secret Access Key.

AWS IAM ID lengths by prefix type:
  20 chars (prefix + 16): AKIA (access keys), ASIA (STS temporary keys)
  21 chars (prefix + 17): all other identifier types (ABIA, ACCA, AGPA, AIDA,
                           AIPA, ANPA, ANVA, APKA, AROA, ASCA)

The Secret Access Key has no prefix and is detected via variable name context.
"""

import re

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

# AWS documents these as canonical example keys - always safe to skip.
_AWS_EXAMPLE_KEYS: frozenset = frozenset({
    "AKIAIOSFODNN7EXAMPLE",    # 20-char AKIA
    "ASIAY7XTRA4NBEXAMPLE",    # 20-char ASIA
    "ABIAJOSHUA67EXAMPLEBX",   # 21-char ABIA
})

_IAM_CHARSET      = "[A-Z0-9]"
_IAM_AFTER_SHORT  = 16  # AKIA, ASIA  → total 20 chars
_IAM_AFTER_LONG   = 17  # all others  → total 21 chars


@register_detector
class AWSDetector(BaseDetector):
    CATEGORY           = "AWS"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "AWS IAM access key IDs (all identifier prefixes) and Secret Access Keys"
    DOMAIN             = "Cloud & Infrastructure"

    _DEFINITIONS: list[TokenDefinition] = [
        # ── 20-char IAM keys: AKIA, ASIA (prefix + 16) ───────────────────────
        TokenDefinition(
            type="IAM User Access Key",
            label="AWS_IAM_ACCESS_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("AKIA", _IAM_CHARSET, _IAM_AFTER_SHORT),
            known_safe=_AWS_EXAMPLE_KEYS,
            description="AWS IAM user access key - grants programmatic API access to your AWS account",
            example="AKIAIOSFODNN7EXAMPLE",
        ),
        TokenDefinition(
            type="STS Temporary Access Key",
            label="AWS_STS_ACCESS_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ASIA", _IAM_CHARSET, _IAM_AFTER_SHORT),
            known_safe=_AWS_EXAMPLE_KEYS,
            description="AWS STS temporary access key - short-lived credential issued by AssumeRole",
            example="ASIAY7XTRA4NBEXAMPLE",
        ),
        # ── 21-char IAM identifiers: all others (prefix + 17) ────────────────
        TokenDefinition(
            type="STS Service Bearer Token",
            label="AWS_STS_BEARER_TOKEN",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ABIA", _IAM_CHARSET, _IAM_AFTER_LONG),
            known_safe=_AWS_EXAMPLE_KEYS,
            description="AWS STS service bearer token - used internally by AWS services",
            example="ABIAJOSHUA67EXAMPLEBX",
        ),
        TokenDefinition(
            type="Context-Specific Credential",
            label="AWS_CONTEXT_CREDENTIAL",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ACCA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS context-specific credential - used within service-to-service calls",
            example="ACCAIOSFODNN7EXAMPLEX",
        ),
        TokenDefinition(
            type="IAM User Group ID",
            label="AWS_IAM_GROUP_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("AGPA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS IAM user group identifier",
            example="AGPAJOSHUA67EXAMPLE01",
        ),
        TokenDefinition(
            type="IAM User ID",
            label="AWS_IAM_USER_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("AIDA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS IAM user unique identifier",
            example="AIDAIDONTKNOWEXAMPLEX",
        ),
        TokenDefinition(
            type="EC2 Instance Profile ID",
            label="AWS_EC2_INSTANCE_PROFILE_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("AIPA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS EC2 instance profile - role attached to an EC2 instance",
            example="AIPARUNNINGONEXAMP001",
        ),
        TokenDefinition(
            type="Managed Policy ID",
            label="AWS_MANAGED_POLICY_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ANPA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS IAM managed policy identifier",
            example="ANPAMYPOLICYEXAMPLE01",
        ),
        TokenDefinition(
            type="Managed Policy Version ID",
            label="AWS_POLICY_VERSION_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ANVA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS IAM managed policy version identifier",
            example="ANVAVERSION12345EXP01",
        ),
        TokenDefinition(
            type="Public Key ID",
            label="AWS_PUBLIC_KEY_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("APKA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS IAM public key identifier",
            example="APKAPUBLICKEYEXAMP001",
        ),
        TokenDefinition(
            type="Role ID",
            label="AWS_ROLE_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("AROA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS IAM role unique identifier",
            example="AROASSUMEROLEEXAMP001",
        ),
        TokenDefinition(
            type="Certificate ID",
            label="AWS_CERTIFICATE_ID",
            severity="MEDIUM",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("ASCA", _IAM_CHARSET, _IAM_AFTER_LONG),
            description="AWS IAM certificate identifier",
            example="ASCACERTIFICATEEXP001",
        ),

        # ── Secret Access Key (context-anchored, 40-char base64-style) ────────
        TokenDefinition(
            type="Secret Access Key",
            label="AWS_SECRET_ACCESS_KEY",
            severity="HIGH",
            detection="context",
            capture_group=1,
            pattern=BaseDetector.context_pattern(
                variable_names=[
                    "aws_secret_access_key", "aws_secret_key", "secret_access_key",
                    "AWS_SECRET_ACCESS_KEY", "AWS_SECRET_KEY",
                ],
                value_charset=r"[A-Za-z0-9+/]",
                value_min=40,
                value_max=40,
            ),
            description="AWS IAM secret access key - paired with an access key ID, used to sign API requests",
            example="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        # Covers all IAM prefix patterns + common secret key variable names
        return [
            "AKIA", "ASIA", "ABIA", "ACCA", "AGPA", "AIDA",
            "AIPA", "ANPA", "ANVA", "APKA", "AROA", "ASCA",
            "aws_secret", "AWS_SECRET",
        ]
