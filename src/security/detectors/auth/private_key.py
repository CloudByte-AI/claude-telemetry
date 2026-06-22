"""
Private Key / Certificate Detector.

Detects PEM-encoded private keys and certificates embedded in text.
Patterns anchor on the BEGIN/END markers which are always present.
"""

import re
from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector


def _pem_pattern(key_type: str) -> re.Pattern:
    """Match a PEM block from -----BEGIN <type>----- to -----END <type>-----."""
    header = rf"-----BEGIN {re.escape(key_type)}-----"
    footer = rf"-----END {re.escape(key_type)}-----"
    return re.compile(
        rf"{header}[\s\S]+?{footer}",
        re.MULTILINE,
    )


@register_detector
class PrivateKeyDetector(BaseDetector):
    CATEGORY           = "Private Key"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "PEM-encoded private keys, certificates, and PKCS documents"
    DOMAIN             = "Auth"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="RSA Private Key",
            label="RSA_PRIVATE_KEY",
            severity="CRITICAL",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("RSA PRIVATE KEY"),
            description="RSA private key (PEM) — used for TLS certificates, SSH authentication, and code signing",
            example="-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4PAtEo1\n-----END RSA PRIVATE KEY-----",
        ),
        TokenDefinition(
            type="EC Private Key",
            label="EC_PRIVATE_KEY",
            severity="CRITICAL",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("EC PRIVATE KEY"),
            description="Elliptic Curve private key (PEM) — used for TLS, JWT signing, and cryptocurrency wallets",
            example="-----BEGIN EC PRIVATE KEY-----\nMHQCAQEEIOWDzJFVzYoEMkZQUBfqPH4VaQrHNKLjBfEM\n-----END EC PRIVATE KEY-----",
        ),
        TokenDefinition(
            type="Generic Private Key (PKCS#8)",
            label="PRIVATE_KEY",
            severity="CRITICAL",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("PRIVATE KEY"),
            description="PKCS#8 private key (PEM) — vendor-neutral private key format for TLS and code signing",
            example="-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEA\n-----END PRIVATE KEY-----",
        ),
        TokenDefinition(
            type="Encrypted Private Key",
            label="ENCRYPTED_PRIVATE_KEY",
            severity="CRITICAL",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("ENCRYPTED PRIVATE KEY"),
            description="Password-protected PKCS#8 private key (PEM)",
            example="-----BEGIN ENCRYPTED PRIVATE KEY-----\nMIIFHDBOBgkqhkiG9w0BBQ0wQTApBgkqhkiG9w0BBQww\n-----END ENCRYPTED PRIVATE KEY-----",
        ),
        TokenDefinition(
            type="OpenSSH Private Key",
            label="OPENSSH_PRIVATE_KEY",
            severity="CRITICAL",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("OPENSSH PRIVATE KEY"),
            description="OpenSSH private key — used for SSH public key authentication",
            example="-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAA\n-----END OPENSSH PRIVATE KEY-----",
        ),
        TokenDefinition(
            type="DSA Private Key",
            label="DSA_PRIVATE_KEY",
            severity="CRITICAL",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("DSA PRIVATE KEY"),
            description="DSA private key (PEM) — legacy digital signature algorithm key",
            example="-----BEGIN DSA PRIVATE KEY-----\nMIIBugIBAAKBgQDMPqGE7t3WoQ9fMaW5vLpM3hJpL5mY\n-----END DSA PRIVATE KEY-----",
        ),
        TokenDefinition(
            type="PGP Private Key",
            label="PGP_PRIVATE_KEY",
            severity="CRITICAL",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("PGP PRIVATE KEY BLOCK"),
            description="PGP/GPG private key — used for email encryption and package signing",
            example="-----BEGIN PGP PRIVATE KEY BLOCK-----\nxcaGBGRmXJEBEACsMJ5bfqpKhMbPMEGrYzE0VpEqR2kL\n-----END PGP PRIVATE KEY BLOCK-----",
        ),
        TokenDefinition(
            type="PKCS#12 Certificate",
            label="PKCS12_CERT",
            severity="HIGH",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("PKCS12"),
            description="PKCS#12 certificate bundle (PEM) — contains private key + certificate chain",
            example="-----BEGIN PKCS12-----\nMIIN0wIBAzCCDY0GCSqGSIb3DQEHAaCCDX4Egg16MIIMdA\n-----END PKCS12-----",
        ),
        TokenDefinition(
            type="X.509 Certificate",
            label="X509_CERTIFICATE",
            severity="MEDIUM",
            detection="pattern",
            capture_group=0,
            pattern=_pem_pattern("CERTIFICATE"),
            description="X.509 certificate (PEM) — public certificate; low sensitivity but flag in sensitive contexts",
            example="-----BEGIN CERTIFICATE-----\nMIIDXTCCAkWgAwIBAgIJAKoK9EXAMPLE0MAoGCCqGSM49\n-----END CERTIFICATE-----",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["-----BEGIN"]
