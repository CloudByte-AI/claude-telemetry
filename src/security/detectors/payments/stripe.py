"""
Stripe Detector.

Covers all Stripe key formats:
  sk_live_ / sk_test_    Secret keys (variable length)
  rk_live_ / rk_test_   Restricted keys (variable length)
  sk_org_               Organization key (variable length)
  whsec_               Webhook endpoint secret (variable length base64)
"""

from src.security.detectors.base import BaseDetector, TokenDefinition
from src.security.registry import register_detector

_STRIPE_CHARSET = r"[a-zA-Z0-9]"
_STRIPE_MIN     = 24
_STRIPE_MAX     = 255


@register_detector
class StripeDetector(BaseDetector):
    CATEGORY           = "Stripe"
    ENABLED_BY_DEFAULT = True
    DESCRIPTION        = "Stripe secret keys, restricted keys, and webhook secrets"
    DOMAIN             = "Payment Gateways"

    _DEFINITIONS: list[TokenDefinition] = [
        TokenDefinition(
            type="Live Secret Key",
            label="STRIPE_LIVE_SECRET_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("sk_live_", _STRIPE_CHARSET, _STRIPE_MIN, _STRIPE_MAX),
            description="Stripe live secret key - full access to your live Stripe account (charges, refunds, customers)",
            example="sk_live_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Test Secret Key",
            label="STRIPE_TEST_SECRET_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("sk_test_", _STRIPE_CHARSET, _STRIPE_MIN, _STRIPE_MAX),
            description="Stripe test secret key - access to Stripe test mode (no real charges)",
            example="sk_test_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Live Restricted Key",
            label="STRIPE_LIVE_RESTRICTED_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("rk_live_", _STRIPE_CHARSET, _STRIPE_MIN, _STRIPE_MAX),
            description="Stripe live restricted key - scoped to specific Stripe API endpoints",
            example="rk_live_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Test Restricted Key",
            label="STRIPE_TEST_RESTRICTED_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("rk_test_", _STRIPE_CHARSET, _STRIPE_MIN, _STRIPE_MAX),
            description="Stripe test restricted key - scoped restricted key for test mode",
            example="rk_test_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Organization Key",
            label="STRIPE_ORG_KEY",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern("sk_org_", _STRIPE_CHARSET, _STRIPE_MIN, _STRIPE_MAX),
            description="Stripe organization key - cross-account access for Stripe organizations",
            example="sk_org_[EXAMPLE]",
        ),
        TokenDefinition(
            type="Webhook Endpoint Secret",
            label="STRIPE_WEBHOOK_SECRET",
            severity="HIGH",
            detection="prefix",
            pattern=BaseDetector.prefix_pattern(
                "whsec_", r"[a-zA-Z0-9+/=]", 32, 64, word_boundary=True
            ),
            description="Stripe webhook signing secret - verifies that webhook events are sent by Stripe",
            example="whsec_[EXAMPLE]",
        ),
    ]

    @property
    def definitions(self) -> list[TokenDefinition]:
        return self._DEFINITIONS

    @property
    def _quick_strings(self) -> list[str]:
        return ["sk_live_", "sk_test_", "rk_live_", "rk_test_", "sk_org_", "whsec_"]
