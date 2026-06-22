"""
Master import file for all security detectors.

Importing this module triggers every @register_detector decorator,
populating the global DetectorRegistry.  Import order is intentional:
more-specific prefix detectors before generic/entropy catch-alls.
"""

# Cloud & Infrastructure
from src.security.detectors.cloud_infra import aws        # noqa: F401
from src.security.detectors.cloud_infra import gcp        # noqa: F401
from src.security.detectors.cloud_infra import digital_ocean  # noqa: F401
from src.security.detectors.cloud_infra import cloudflare  # noqa: F401

# AI / ML platforms
from src.security.detectors.ai_ml import openai       # noqa: F401
from src.security.detectors.ai_ml import anthropic    # noqa: F401
from src.security.detectors.ai_ml import groq         # noqa: F401
from src.security.detectors.ai_ml import hugging_face # noqa: F401
from src.security.detectors.ai_ml import replicate    # noqa: F401
from src.security.detectors.ai_ml import cohere       # noqa: F401
from src.security.detectors.ai_ml import mistral      # noqa: F401

# Developer Tools
from src.security.detectors.dev_tools import github   # noqa: F401
from src.security.detectors.dev_tools import gitlab   # noqa: F401
from src.security.detectors.dev_tools import npm      # noqa: F401
from src.security.detectors.dev_tools import pypi     # noqa: F401

# Payment Gateways
from src.security.detectors.payments import stripe    # noqa: F401
from src.security.detectors.payments import razorpay  # noqa: F401

# Communication
from src.security.detectors.communication import twilio  # noqa: F401

# Database credentials
from src.security.detectors.database import db_connection  # noqa: F401
from src.security.detectors.database import jdbc           # noqa: F401

# Auth / credentials
from src.security.detectors.auth import jwt         # noqa: F401
from src.security.detectors.auth import private_key # noqa: F401
from src.security.detectors.auth import bearer_token  # noqa: F401  (off by default)
from src.security.detectors.auth import basic_auth    # noqa: F401  (off by default)

# PII  (all off by default)
from src.security.detectors.pii import email  # noqa: F401
from src.security.detectors.pii import phone  # noqa: F401

# Generic / entropy  (all off by default)
from src.security.detectors.generic import entropy  # noqa: F401
from src.security.detectors.generic import keyword  # noqa: F401
