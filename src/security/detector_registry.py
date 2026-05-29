"""
Detector Registry — metadata for every detection capability in the security scanner.

Each entry defines:
  key         : the YAML config key (detectors: section or pii: section)
  name        : human-readable display name shown in the UI
  description : plain-English explanation of what it catches and why it matters
  example     : anonymised example of a real match
  category    : display grouping in the UI
  default     : True if enabled by default in the standard preset

No mention of "detect-secrets" or "built-in custom" — those are implementation
details the user should never need to know about.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectorMeta:
    key: str
    name: str
    description: str
    example: str
    category: str
    default: bool = True


# ── Categories (in display order) ────────────────────────────────────────────
CAT_CLOUD       = "Cloud & Infrastructure"
CAT_DEVELOPER   = "Developer Tools"
CAT_AI          = "AI & ML Platforms"
CAT_PAYMENT     = "Payment Gateways"
CAT_COMMS       = "Communication & Marketing"
CAT_DATABASE    = "Databases & Connections"
CAT_AUTH        = "Authentication & Secrets"
CAT_PII         = "Privacy (PII)"
CAT_ENTROPY     = "Entropy Detection"


DETECTORS: list[DetectorMeta] = [

    # ── Cloud & Infrastructure ────────────────────────────────────────────────
    DetectorMeta(
        key="AWSKeyDetector",
        name="AWS Access Key",
        description="Detects Amazon Web Services access key IDs and their paired secret keys. "
                    "These credentials grant access to your AWS account and can be used to launch instances, "
                    "access S3 buckets, or modify IAM policies.",
        example="AKIAIOSFODNN7EXAMPLE",
        category=CAT_CLOUD,
    ),
    DetectorMeta(
        key="AzureStorageKeyDetector",
        name="Azure Storage Key",
        description="Detects Azure Storage account access keys. These 88-character base64 keys "
                    "provide full read/write access to all blobs, files, queues, and tables "
                    "in a storage account.",
        example="dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q=",
        category=CAT_CLOUD,
    ),
    DetectorMeta(
        key="GCP_API_KEY",
        name="Google Cloud API Key",
        description="Detects Google Cloud Platform API keys. These keys start with 'AIza' and "
                    "provide access to GCP services such as Maps, Vision, Translation, and more "
                    "depending on the key's permissions.",
        example="AIzaSyD-9tSrke72X0C5example",
        category=CAT_CLOUD,
    ),
    DetectorMeta(
        key="DIGITAL_OCEAN_TOKEN",
        name="DigitalOcean Personal Access Token",
        description="Detects DigitalOcean API tokens. Starting with 'dop_v1_', these 64-character "
                    "tokens control your DigitalOcean account — droplets, DNS, networking, and more.",
        example="dop_v1_a1b2c3d4e5f6a1b2c3d4e5f6...",
        category=CAT_CLOUD,
    ),
    DetectorMeta(
        key="CLOUDFLARE_API_TOKEN",
        name="Cloudflare API Token",
        description="Detects Cloudflare API tokens used to manage DNS zones, CDN rules, "
                    "Workers, and security settings. Leaking this gives an attacker control "
                    "over your domain infrastructure.",
        example="cloudflare_api_token=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        category=CAT_CLOUD,
    ),
    DetectorMeta(
        key="IbmCloudIamDetector",
        name="IBM Cloud IAM Key",
        description="Detects IBM Cloud Identity and Access Management API keys. "
                    "These authenticate service-to-service calls across IBM Cloud resources "
                    "including Watson, Kubernetes, and Cloud Object Storage.",
        example="ibm_api_key=abcdefghIJKLMNOP1234567890example",
        category=CAT_CLOUD,
        default=False,
    ),
    DetectorMeta(
        key="IbmCosHmacDetector",
        name="IBM Cloud Object Storage HMAC Key",
        description="Detects IBM Cloud Object Storage HMAC credentials (access key + secret key pair). "
                    "These provide programmatic access to IBM COS buckets for read/write operations.",
        example="ibm_cos_hmac_access_key_id=ABCDEFGH12345678",
        category=CAT_CLOUD,
        default=False,
    ),
    DetectorMeta(
        key="CloudantDetector",
        name="IBM Cloudant Credential",
        description="Detects IBM Cloudant NoSQL database credentials. These appear as "
                    "basic auth in database connection URLs and provide full database access "
                    "including reading, writing, and deleting documents.",
        example="https://user:password@account.cloudant.com",
        category=CAT_CLOUD,
    ),
    DetectorMeta(
        key="SoftlayerDetector",
        name="IBM SoftLayer Credential",
        description="Detects IBM SoftLayer (Classic Infrastructure) API credentials. "
                    "SoftLayer is IBM's legacy IaaS platform. These credentials can control "
                    "virtual servers, networking, and storage on Classic Infrastructure.",
        example="softlayer_api_key=abcdef1234567890abcdef1234567890",
        category=CAT_CLOUD,
        default=False,
    ),
    DetectorMeta(
        key="FIREBASE_URL",
        name="Firebase Database URL",
        description="Detects Firebase Realtime Database endpoint URLs. These URLs, "
                    "when combined with your Firebase project credentials, expose your "
                    "entire real-time database to potential unauthorised access.",
        example="https://my-app-default-rtdb.firebaseio.com",
        category=CAT_CLOUD,
    ),
    DetectorMeta(
        key="MAPBOX_TOKEN",
        name="Mapbox Access Token",
        description="Detects Mapbox public access tokens starting with 'pk.eyJ1'. "
                    "Although labelled 'public', these tokens are tied to your account "
                    "billing — unauthorised use results in unexpected charges.",
        example="pk.eyJ1IjoieW91cnVzZXJuYW1lIiwiYSI6...",
        category=CAT_CLOUD,
    ),

    # ── Developer Tools ───────────────────────────────────────────────────────
    DetectorMeta(
        key="GitHubTokenDetector",
        name="GitHub Access Token",
        description="Detects GitHub personal access tokens and fine-grained tokens. "
                    "These start with 'ghp_' or 'github_pat_' and can read/write repositories, "
                    "manage issues, access organisations, and more depending on scopes granted.",
        example="ghp_abcdefghijklmnopqrstuvwxyz123456",
        category=CAT_DEVELOPER,
    ),
    DetectorMeta(
        key="GitLabTokenDetector",
        name="GitLab Access Token",
        description="Detects GitLab personal and project access tokens starting with 'glpat-'. "
                    "These provide authenticated access to repositories, CI/CD pipelines, "
                    "and GitLab API endpoints.",
        example="glpat-abcdefghijklmnopqrst",
        category=CAT_DEVELOPER,
    ),
    DetectorMeta(
        key="NpmDetector",
        name="NPM Access Token",
        description="Detects NPM registry authentication tokens starting with 'npm_'. "
                    "These tokens allow publishing packages to the NPM registry and "
                    "accessing private packages — leaking one can enable malicious package releases.",
        example="npm_abcdefghijklmnopqrstuvwxyz123456789",
        category=CAT_DEVELOPER,
    ),
    DetectorMeta(
        key="PypiTokenDetector",
        name="PyPI Upload Token",
        description="Detects Python Package Index (PyPI) authentication tokens. "
                    "These start with 'pypi-' and are used to publish Python packages. "
                    "A leaked token could allow an attacker to publish malicious versions of your packages.",
        example="pypi-AgEIcHlwaS5vcmcCJA...",
        category=CAT_DEVELOPER,
    ),
    DetectorMeta(
        key="PYPI_TOKEN",
        name="PyPI Token (Extended)",
        description="Extended detection for PyPI tokens across different formats and "
                    "variable naming patterns commonly seen in CI/CD configurations.",
        example="PYPI_TOKEN=pypi-AgEIcHlwaS5vcmcCJA...",
        category=CAT_DEVELOPER,
    ),
    DetectorMeta(
        key="ArtifactoryDetector",
        name="JFrog Artifactory Token",
        description="Detects authentication tokens for JFrog Artifactory, a universal "
                    "artifact repository used in enterprise DevOps. Leaked tokens expose "
                    "private build artifacts, Docker images, and Maven/npm packages.",
        example="AKCp8abcdef1234567890abcdef1234567890",
        category=CAT_DEVELOPER,
    ),
    DetectorMeta(
        key="SENTRY_DSN",
        name="Sentry DSN",
        description="Detects Sentry Data Source Names — connection strings that include "
                    "an authentication key embedded in the URL. Leaking a DSN allows "
                    "anyone to submit fake error reports to your Sentry project.",
        example="https://abc123def456@o123456.ingest.sentry.io/789",
        category=CAT_DEVELOPER,
    ),

    # ── AI & ML Platforms ─────────────────────────────────────────────────────
    DetectorMeta(
        key="OpenAIDetector",
        name="OpenAI API Key",
        description="Detects OpenAI API keys used to access GPT, DALL-E, Whisper, and "
                    "Embeddings. Leaked keys are immediately exploited for expensive API "
                    "calls billed to your account — OpenAI charges can reach thousands of "
                    "dollars before you notice.",
        example="sk-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQ",
        category=CAT_AI,
    ),
    DetectorMeta(
        key="ANTHROPIC_KEY",
        name="Anthropic API Key",
        description="Detects Anthropic API keys for Claude models. These start with 'sk-ant-' "
                    "and provide access to Claude's API. Unauthorised use results in charges "
                    "billed to your Anthropic account.",
        example="sk-ant-api03-abcdefghijklmnopqrstuvwxyz...",
        category=CAT_AI,
    ),
    DetectorMeta(
        key="GROQ_API_KEY",
        name="Groq API Key",
        description="Detects Groq API keys starting with 'gsk_'. Groq provides ultra-fast "
                    "LLM inference. Leaked keys result in unauthorised usage billed to your account.",
        example="gsk_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0",
        category=CAT_AI,
    ),
    DetectorMeta(
        key="HUGGING_FACE_TOKEN",
        name="Hugging Face Access Token",
        description="Detects Hugging Face Hub access tokens starting with 'hf_'. "
                    "These provide access to private models, datasets, and spaces, "
                    "and can be used to push changes to your organisation's repositories.",
        example="hf_XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        category=CAT_AI,
    ),
    DetectorMeta(
        key="REPLICATE_TOKEN",
        name="Replicate API Token",
        description="Detects Replicate API tokens starting with 'r8_'. "
                    "Replicate runs ML models in the cloud. Leaked tokens allow "
                    "expensive GPU model runs billed to your account.",
        example="r8_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN",
        category=CAT_AI,
    ),
    DetectorMeta(
        key="COHERE_API_KEY",
        name="Cohere API Key",
        description="Detects Cohere AI platform API keys for natural language processing, "
                    "embeddings, and reranking. Typically assigned to variables named "
                    "'cohere_key', 'co_api_key', or similar.",
        example="cohere_api_key=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        category=CAT_AI,
    ),
    DetectorMeta(
        key="MISTRAL_API_KEY",
        name="Mistral API Key",
        description="Detects Mistral AI API keys. Mistral provides open-weight LLMs via API. "
                    "Leaked keys allow model access billed to your account.",
        example="mistral_api_key=abcdefghijklmnopqrstuvwxyz123456",
        category=CAT_AI,
    ),

    # ── Payment Gateways ─────────────────────────────────────────────────────
    DetectorMeta(
        key="StripeDetector",
        name="Stripe Secret Key",
        description="Detects Stripe live and restricted secret keys starting with 'sk_live_' or 'rk_live_'. "
                    "These keys have full API access to your Stripe account — leaked keys "
                    "can be used to create charges, access customer data, and issue refunds.",
        example="sk_live_<24-alphanumeric-chars>",
        category=CAT_PAYMENT,
    ),
    DetectorMeta(
        key="SquareOAuthDetector",
        name="Square OAuth Token",
        description="Detects Square payment platform OAuth access tokens. "
                    "These tokens provide access to Square's payment, inventory, "
                    "and customer management APIs on behalf of a merchant.",
        example="EAAAEabcdefghijklmnopqrstuvwxyz...",
        category=CAT_PAYMENT,
    ),
    DetectorMeta(
        key="RAZORPAY_KEY",
        name="Razorpay API Key",
        description="Detects Razorpay live and test API keys starting with 'rzp_live_' or 'rzp_test_'. "
                    "Razorpay is India's leading payment gateway. Leaked live keys allow "
                    "creating orders, capturing payments, and accessing transaction data.",
        example="rzp_live_abcdefghijklmnopqrst",
        category=CAT_PAYMENT,
    ),
    DetectorMeta(
        key="PAYU_KEY",
        name="PayU Merchant Key",
        description="Detects PayU merchant keys. PayU is a major payment gateway in "
                    "India and Eastern Europe. Merchant keys are required for payment "
                    "initiation and verification — leaking them enables fraudulent transactions.",
        example="payu_merchant_key=AbCdEf",
        category=CAT_PAYMENT,
    ),

    # ── Communication & Marketing ─────────────────────────────────────────────
    DetectorMeta(
        key="SlackDetector",
        name="Slack Token or Webhook",
        description="Detects Slack bot tokens (xoxb-), user tokens (xoxp-), and "
                    "incoming webhook URLs. These provide access to send messages, "
                    "read channel history, and manage workspaces.",
        example="xoxb-<11digits>-<11digits>-<16chars>",
        category=CAT_COMMS,
    ),
    DetectorMeta(
        key="TwilioKeyDetector",
        name="Twilio API Key",
        description="Detects Twilio API key SIDs starting with 'SK'. Twilio handles "
                    "SMS, voice calls, and email. Leaked keys can send messages billed "
                    "to your account or access customer communication data.",
        example="SKXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        category=CAT_COMMS,
    ),
    DetectorMeta(
        key="SendGridDetector",
        name="SendGrid API Key",
        description="Detects SendGrid transactional email API keys starting with 'SG.'. "
                    "Leaked keys allow sending email as your domain, accessing recipient lists, "
                    "and viewing email analytics.",
        example="SG.XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
        category=CAT_COMMS,
    ),
    DetectorMeta(
        key="TelegramBotTokenDetector",
        name="Telegram Bot Token",
        description="Detects Telegram Bot API authentication tokens. "
                    "These numeric:alphanumeric tokens allow sending/receiving messages, "
                    "managing groups, and controlling bot behaviour through Telegram's API.",
        example="1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ-example",
        category=CAT_COMMS,
    ),
    DetectorMeta(
        key="DiscordBotTokenDetector",
        name="Discord Bot Token",
        description="Detects Discord bot application tokens. "
                    "These base64-encoded tokens give a bot full control over "
                    "its Discord permissions — reading messages, joining servers, "
                    "and sending notifications.",
        example="MTIzNDU2Nzg5MDEyMzQ1Ng.XXXXXX.YYYYYYYYYYYYYYYYYYYY",
        category=CAT_COMMS,
    ),
    DetectorMeta(
        key="MailchimpDetector",
        name="Mailchimp API Key",
        description="Detects Mailchimp email marketing API keys. These keys end with "
                    "a datacenter identifier (e.g., '-us1') and provide access to "
                    "subscriber lists, campaign data, and automation workflows.",
        example="<32-hex-chars>-us1",
        category=CAT_COMMS,
    ),

    # ── Databases & Connections ───────────────────────────────────────────────
    DetectorMeta(
        key="DB_CONNECTION",
        name="Database Connection String",
        description="Detects database URLs with embedded credentials for MongoDB, "
                    "PostgreSQL, MySQL, Redis, RabbitMQ, Elasticsearch, and 10+ other "
                    "protocols. A connection string in plain text is a complete set of "
                    "credentials for your database.",
        example="postgresql://<user>:<password>@host:5432/dbname",
        category=CAT_DATABASE,
    ),
    DetectorMeta(
        key="JDBC_CONNECTION",
        name="JDBC Connection String",
        description="Detects Java JDBC database connection strings with inline username "
                    "and password parameters. Common in Java, Spring Boot, and Android "
                    "projects where database config is hardcoded.",
        example="jdbc:mysql://host:3306/dbname;user=<user>;password=<pass>",
        category=CAT_DATABASE,
    ),
    DetectorMeta(
        key="BasicAuthDetector",
        name="HTTP Basic Auth in URL",
        description="Detects usernames and passwords embedded directly in HTTP/HTTPS URLs "
                    "(format: http://user:password@host). These appear in API calls, "
                    "git remote URLs, and curl commands.",
        example="https://<user>:<password>@api.example.com/endpoint",
        category=CAT_DATABASE,
    ),

    # ── Authentication & Secrets ─────────────────────────────────────────────
    DetectorMeta(
        key="JwtTokenDetector",
        name="JWT Token",
        description="Detects JSON Web Tokens — the three-part base64-encoded strings "
                    "starting with 'eyJ'. JWTs often carry user identity and session "
                    "state. Active tokens allow impersonation of the user they represent.",
        example="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxw...",
        category=CAT_AUTH,
    ),
    DetectorMeta(
        key="PrivateKeyDetector",
        name="Private Key (PEM Format)",
        description="Detects PEM-encoded private keys including RSA, EC, DSA, and Ed25519. "
                    "These begin with '-----BEGIN PRIVATE KEY-----' and are used for TLS "
                    "certificates, SSH access, and code signing — among the most sensitive "
                    "secrets to leak.",
        example="-----BEGIN RSA PRIVATE KEY----- ... -----END RSA PRIVATE KEY-----",
        category=CAT_AUTH,
    ),
    DetectorMeta(
        key="KeywordDetector",
        name="Secret Variable Assignment",
        description="Detects secret values directly assigned to variables named 'api_key', "
                    "'secret', 'password', 'auth_token', and similar. Catches hardcoded "
                    "credentials that look like: api_key = 'actualvalue'. "
                    "Note: may produce some false positives in config examples and test files.",
        example="api_key = '<secret-value>'",
        category=CAT_AUTH,
        default=False,
    ),
    DetectorMeta(
        key="BEARER_TOKEN",
        name="HTTP Bearer Token",
        description="Detects tokens following 'Bearer ' in Authorization headers. "
                    "These are commonly used for OAuth2 and JWT-based authentication "
                    "in API requests. Note: may flag test and mock auth tokens in "
                    "code and documentation.",
        example="Authorization: Bearer eyJhbGciOiJIUzI1NiJ9...",
        category=CAT_AUTH,
        default=False,
    ),
    DetectorMeta(
        key="INLINE_PASSWORD",
        name="Hardcoded Password",
        description="Detects password values directly assigned in code — patterns like "
                    "password='value', passwd='value', or pwd='value'. "
                    "Note: may flag legitimate environment-variable reads like "
                    "password=os.getenv('DB_PASS') — review findings before blocking.",
        example="password = 'myProductionPassword123'",
        category=CAT_AUTH,
        default=False,
    ),
    DetectorMeta(
        key="IpPublicDetector",
        name="Public IP Address",
        description="Detects publicly routable IP addresses. Useful for identifying "
                    "hardcoded server addresses in code that should use environment "
                    "variables instead. "
                    "Warning: very high false-positive rate in code — only enable if specifically needed.",
        example="server_ip = '203.0.113.42'",
        category=CAT_AUTH,
        default=False,
    ),

    # ── PII ───────────────────────────────────────────────────────────────────
    DetectorMeta(
        key="email",
        name="Email Address",
        description="Detects email addresses in prompts. Useful when working with "
                    "user data, customer lists, or support tickets where personal "
                    "email addresses should not be shared with AI.",
        example="user@company.com",
        category=CAT_PII,
        default=False,
    ),
    DetectorMeta(
        key="phone",
        name="Phone Number",
        description="Detects US and international phone numbers. Enable when handling "
                    "customer data, HR records, or any context where phone numbers "
                    "are personally identifiable.",
        example="+1 (555) 123-4567",
        category=CAT_PII,
        default=False,
    ),

    # ── Entropy Detection ─────────────────────────────────────────────────────
    DetectorMeta(
        key="hex_entropy",
        name="High-Entropy Hex String",
        description="Detects long strings of hexadecimal characters with unusually high "
                    "randomness — a statistical signature of unknown or custom API keys. "
                    "Catches secrets no specific pattern could detect. "
                    "Sensitivity is controlled by the threshold below: lower = more catches, "
                    "higher = fewer false positives.",
        example="a8f3c2e1b4d6f9a2c5e8b1d4f7a0c3e6",
        category=CAT_ENTROPY,
    ),
    DetectorMeta(
        key="base64_entropy",
        name="High-Entropy Base64 String",
        description="Detects long base64-encoded strings with high randomness. "
                    "Many cloud and SaaS services issue base64-encoded tokens with no "
                    "recognisable prefix — this catches them by their statistical properties. "
                    "Most effective for catching cloud service keys, session tokens, and "
                    "symmetric encryption keys.",
        example="xK9mP2nQ7vL4wR1yZ3aB5cD8eF6gH0iJ",
        category=CAT_ENTROPY,
    ),
]


# ── Lookup helpers ────────────────────────────────────────────────────────────

def get_by_key(key: str) -> DetectorMeta | None:
    return next((d for d in DETECTORS if d.key == key), None)


def get_by_category(category: str) -> list[DetectorMeta]:
    return [d for d in DETECTORS if d.category == category]


def all_categories() -> list[str]:
    seen: list[str] = []
    for d in DETECTORS:
        if d.category not in seen:
            seen.append(d.category)
    return seen


def to_dict_list() -> list[dict]:
    return [
        {
            "key":         d.key,
            "name":        d.name,
            "description": d.description,
            "example":     d.example,
            "category":    d.category,
            "default":     d.default,
        }
        for d in DETECTORS
    ]
