# Security Scanning Strategy
## Claude Code Plugin — Prompt & Response Protection
### detect-secrets + Custom Logic + Presidio  vs  LLM Guard

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Use Case & Requirements](#2-use-case--requirements)
3. [Approach A — detect-secrets + Custom Regex + Presidio](#3-approach-a--detect-secrets--custom-regex--presidio)
4. [Approach B — LLM Guard](#4-approach-b--llm-guard)
5. [Side-by-Side Master Comparison](#5-side-by-side-master-comparison)
6. [Decision Framework & Recommendation](#6-decision-framework--recommendation)
7. [Database Design & Operational Flow](#7-database-design--operational-flow)
8. [Four-Point Scanning Coverage](#8-four-point-scanning-coverage)
9. [Final Coverage Summary](#9-final-coverage-summary)

---

## 1. Executive Summary

This document compares two approaches for securing a Python-based Claude Code telemetry plugin against sensitive data leaks. The core problem: users paste prompts containing API keys, credentials, and PII — these must be detected, masked, and never reach Claude or the telemetry server in raw form.

> **Key Finding:** detect-secrets + custom regex + Presidio is the right choice for the current phase. LLM Guard adds genuine value only for NLP-based PII detection and output scanning — capabilities that can be added later via Microsoft Presidio directly, without the full LLM Guard framework overhead.

---

## 2. Use Case & Requirements

### Threats We Are Protecting Against

| Threat Type | Example | Direction | Priority |
|---|---|---|---|
| API Keys & Tokens | AKIAIOSFODNN7EXAMPLE, ghp_abc123, sk_live_... | Input (prompt) | Critical |
| PII — Structured | SSN: 123-45-6789, Card: 4111-1111-1111-1111 | Input (prompt) | Critical |
| PII — Unstructured | John Smith at 42 Main Street, Boston | Input (prompt) | High |
| DB Credentials | mongodb://user:pass@cluster.mongodb.net | Input (prompt) | Critical |
| Secrets in Response | Claude echoes back a key from the prompt | Output (response) | High |
| Tool File Reads | Claude reads .env or secrets file | Tool use | High |
| Prompt Injection | Ignore previous instructions... | Input (prompt) | Medium |
| Invisible Characters | Hidden unicode injection tricks | Input (prompt) | Low |

---

## 3. Approach A — detect-secrets + Custom Regex + Presidio

### How It Works

detect-secrets is a Yelp-built, industry-standard Python library designed specifically for real-time string and file scanning. It is NOT a git history scanner. It exposes a native Python API `scan_line()` that scans individual lines of text directly — no subprocesses, no temp files, no external calls.

### Three-Layer Architecture

| Layer | Technology | What It Catches | Speed |
|---|---|---|---|
| Layer 1 | detect-secrets | API keys, tokens, credentials, high-entropy strings | Fast (~ms per line) |
| Layer 2 | Presidio NLP | Names, addresses, passport numbers, context-aware PII | Medium (~50ms) |
| Layer 3 | Custom regex | SSN, credit cards, DB connections, inline passwords | Very fast (single pass) |

---

### All detect-secrets Built-in Detectors

| Category | Detector | What It Catches |
|---|---|---|
| Cloud | AWSKeyDetector | AWS Access Key ID + Secret Key pairs |
| Cloud | AzureStorageKeyDetector | Azure Storage account keys (base64 format) |
| Cloud | CloudantDetector | IBM Cloudant database credentials |
| Cloud | IbmCloudIamDetector | IBM Cloud IAM API keys |
| Cloud | IbmCosHmacDetector | IBM Cloud Object Storage HMAC credentials |
| Dev Tools | GitHubTokenDetector | GitHub personal access tokens (ghp_..., github_pat_...) |
| Dev Tools | GitLabTokenDetector | GitLab personal access tokens (glpat-...) |
| Dev Tools | NpmDetector | npm authentication tokens (npm_...) |
| Payment | StripeDetector | Stripe secret and restricted keys (sk_live_..., rk_live_...) |
| Payment | SquareOAuthDetector | Square OAuth access tokens |
| Comms | SlackDetector | Slack bot/user tokens and webhook URLs (xox...) |
| Comms | SendGridDetector | SendGrid API keys (SG....) |
| Comms | MailchimpDetector | Mailchimp API keys (...-us1 format) |
| Comms | TwilioKeyDetector | Twilio API keys (SK...) |
| Auth | JwtTokenDetector | JSON Web Tokens (eyJ... format) |
| Auth | PrivateKeyDetector | PEM private key blocks (-----BEGIN PRIVATE KEY-----) |
| Auth | BasicAuthDetector | Basic auth credentials in URLs (http://user:pass@host) |
| Entropy | HexHighEntropyString | High-entropy hex strings — catches unknown/custom API keys |
| Entropy | Base64HighEntropyString | High-entropy base64 strings — catches internal service tokens |
| Keyword | KeywordDetector | Secret-looking assignments: api_key='x', secret='x', password='x' |

---

### Custom Regex Layer — Gaps detect-secrets Misses

| Pattern | Example | Why Needed |
|---|---|---|
| Credit Card Numbers | 4111-1111-1111-1111 | detect-secrets has no CC detector |
| Social Security Numbers | 123-45-6789 format | No SSN detector in detect-secrets |
| Database Connection Strings | mongodb://user:pass@host/db | detect-secrets misses many DB formats |
| Inline Passwords | password = abc123, pwd: 'secret' | Supplements KeywordDetector |
| Bearer Tokens | Authorization: Bearer token | Generic bearer pattern not in detect-secrets |
| GCP API Keys | AIzaSy... format | Google Cloud keys not in detect-secrets |
| OpenAI API Keys | sk-... (48 chars) | OpenAI keys not in detect-secrets |
| Anthropic API Keys | sk-ant-... format | Anthropic keys not in detect-secrets |
| Firebase URLs | *.firebaseio.com | Firebase database URLs not covered |

---

### Entropy Detection — The Critical Advantage

The `HexHighEntropyString` and `Base64HighEntropyString` detectors are detect-secrets' most powerful capability. They catch secrets that no regex pattern can ever match.

> **Example:** A custom internal service key like `xK9mP2nQ7vL4wR1yZ3` or `a8f3c2e1b4d6f9a2c5e8` has no known format — regex will always miss it. Entropy detection measures string randomness mathematically and flags it regardless of format.

| String | Entropy Score | Result |
|---|---|---|
| "hello world" | 3.1 — low entropy | Ignored — normal text |
| "password123" | 3.8 — low entropy | Ignored — predictable |
| "a8f3c2e1b4d6f9a2c5e8" | 5.9 — high entropy | Flagged — likely a secret |
| "AKIAIOSFODNN7EXAMPLE" | 4.2 — known pattern | Flagged — AWS key pattern |
| "xK9mP2nQ7vL4wR1yZ3" | 5.7 — high entropy | Flagged — unknown internal key |

---

### Presidio NLP — What It Adds Over Regex

Microsoft Presidio adds genuine NLP-based PII detection that regex fundamentally cannot replicate.

| Capability | Regex | Presidio NLP |
|---|---|---|
| SSN with dashes (123-45-6789) | ✅ Catches it | ✅ Catches it |
| SSN without dashes (123456789) | ❌ Misses it | ✅ Catches it in context |
| Credit card with spaces | ✅ Catches it | ✅ Catches it |
| Person names | ❌ Cannot detect | ✅ Catches contextually |
| Street addresses | ❌ Cannot detect | ✅ Catches contextually |
| Passport numbers | ❌ Needs country-specific regex | ✅ Multi-country coverage |
| Driver's licence IDs | ❌ Needs format-specific regex | ✅ Multi-format coverage |
| Medical record numbers | ❌ No known format | ✅ Context-aware |

---

### Performance Strategy for Large Prompts

`UserPromptSubmit` has a hard 30-second timeout. Users frequently paste code snippets with hundreds or thousands of lines.

| Prompt Size | Strategy | How It Works | Expected Time |
|---|---|---|---|
| ≤ 500 lines | Full scan | Every line scanned sequentially | 50 — 200ms |
| 501 — 5,000 lines | Chunked parallel | Split into 100-line chunks, 4 threads | 500ms — 3s |
| 5,000+ lines | Pre-filtered | Quick regex filter first, only high-risk lines deep-scanned | 1 — 5s |
| Any size | Custom regex | Single-pass regex over full text, always runs | 5 — 20ms extra |

> **Pre-filter insight:** Lines containing only simple assignments, comments, or natural language cannot mathematically contain known secret formats. A quick regex check skips these lines entirely, reducing detect-secrets workload by 80-90% on typical code.

---

### Approach A — Full Pros and Cons

#### Pros

- **Lightweight** — ~2MB install, zero model downloads, works offline always
- **Fast** — milliseconds per prompt regardless of size with strategy selector
- **Native Python API** — direct function calls, no subprocess, no temp files, no binary
- **Purpose-built** — designed exactly for real-time string scanning, not git history
- **Entropy detection** — catches unknown/custom API keys that no regex pattern can match
- **Battle-tested** — production use at Yelp, Netflix, and many enterprises
- **Zero management** — no models to download, update, or manage
- **Works anywhere** — no GPU, no internet after install, no RAM requirements
- **Easy to extend** — add custom patterns in one place with simple regex
- **Both directions** — same code works for input (prompt) and output (response) scanning
- **Presidio addition** — adds NLP PII without full LLM Guard framework overhead

#### Cons

- **No injection detection** — does not detect prompt injection or jailbreak attempts
- **No invisible text** — does not catch hidden unicode character injection tricks (without adding LLM Guard InvisibleText)
- **No risk scoring** — binary detect/not-detect — no confidence score per finding (Presidio partially addresses this)
- **Pattern maintenance** — new services need manual pattern additions (mitigated by entropy detection)
- **Presidio cold start** — Presidio spaCy models add ~100MB download and ~1-3s first-load per session

---

## 4. Approach B — LLM Guard

### How LLM Guard Works Internally

LLM Guard is NOT one thing. It is a collection of scanners. Each scanner uses a different underlying technology.

| Scanner Type | Technology Inside | Examples | Weight |
|---|---|---|---|
| Rule-based | Pure logic / detect-secrets | Secrets, InvisibleText, TokenLimit, BanSubstrings | Minimal (~2MB) |
| NLP-based | Microsoft Presidio + spaCy | Anonymize, PII | Medium (~100MB models) |
| ML classifier | HuggingFace Transformers | PromptInjection, Toxicity, Bias | Heavy (~500MB models) |

---

### All LLM Guard Input Scanners Evaluated

| Scanner | Technology | Relevant? | Notes |
|---|---|---|---|
| Secrets | detect-secrets (wrapper) | ⚠️ Redundant | Literally wraps detect-secrets — adds no value if you already use it directly |
| Anonymize | Presidio NLP | ✅ Valuable | NLP-based PII — catches "my social is 123456789" without dashes |
| PromptInjection | HuggingFace ML | ❌ Too slow | 2-5s per prompt on CPU — too slow for 30s timeout on every prompt |
| Toxicity | HuggingFace ML | ❌ Not relevant | Hate speech / harassment detection — not your threat model |
| InvisibleText | Unicode check | ✅ Useful | Catches hidden unicode characters used in injection attacks |
| TokenLimit | Token counting | ✅ Useful | Block prompts exceeding cost threshold — useful for telemetry billing |
| BanTopics | ML classifier | ❌ Not relevant | Topic restriction — not your use case |
| BanSubstrings | String matching | ⚠️ Optional | Custom blocklist — can do this with regex |
| Gibberish | Classifier | ❌ Not relevant | Nonsense input detection — not your threat model |
| Language | Classifier | ❌ Not relevant | Language detection — not your threat model |

---

### All LLM Guard Output Scanners Evaluated

| Scanner | Technology | Relevant? | Notes |
|---|---|---|---|
| Secrets | detect-secrets (wrapper) | ✅ Useful | Catches secrets Claude might echo back in its response |
| PII | Presidio NLP | ✅ Valuable | Catches PII in Claude's response before user sees it |
| MaliciousURLs | URL classifier | ✅ Useful | Catches dangerous URLs Claude might include |
| Toxicity | HuggingFace ML | ❌ Not relevant | Not your threat model |
| Relevance | Embedding similarity | ❌ Not relevant | Off-topic response detection — not needed |
| Bias | ML classifier | ❌ Not relevant | Bias detection — not your threat model |
| Regex | Custom patterns | ✅ Useful | Apply custom patterns to output — can replace with your own code |
| NoRefusal | Classifier | ❌ Not relevant | Refusal detection — not your threat model |

---

### Model Download Reality

| Component | Download Size | Stored In | Required For |
|---|---|---|---|
| detect-secrets (base) | ~2MB | pip package | Secrets scanner |
| Presidio + spaCy en_core_web_lg | ~100MB | ~/.cache/ | Anonymize, PII scanners |
| PromptInjection transformer | ~250-500MB | ~/.cache/huggingface/ | PromptInjection scanner |
| Toxicity transformer | ~250-500MB | ~/.cache/huggingface/ | Toxicity scanner |
| Total (all scanners) | ~1GB+ | User's machine | Full LLM Guard stack |
| Total (recommended only) | ~100-150MB | User's machine | Secrets + Anonymize + PII only |

> **Important:** These models download silently on first use. For a plugin shipping to users' machines, this means a silent 100MB-1GB download on first run. Subsequent runs load from cache and are fast — but the first-run experience must be managed.

---

### The Genuine Value LLM Guard Adds

Only two capabilities in LLM Guard genuinely add value beyond detect-secrets + custom regex:

| Capability | Why It Matters | Alternative |
|---|---|---|
| Anonymize (Presidio NLP) | Catches "my social is 123456789" — no dashes, no known pattern. Understands context, not just format. Covers names, addresses, passport numbers, driver's licence IDs. | Use Presidio directly (pip install presidio-analyzer) — same capability without full LLM Guard framework |
| Output PII scanner | Claude might include PII in its response even when the prompt was clean. Presidio NLP catches this in Claude's output before the user sees it. | Implement detect-secrets + regex on Stop hook — catches structured secrets, misses unstructured PII |

---

### Approach B — Full Pros and Cons

#### Pros

- **NLP PII detection** — catches unstructured PII: names, addresses, passport numbers, context-aware patterns
- **Output scanning built-in** — structured pipeline for scanning Claude's response, not just the prompt
- **Risk scores** — each scanner returns a 0.0 to 1.0 confidence score, not just binary detect/not-detect
- **Injection detection available** — PromptInjection scanner available (though too slow on CPU for real-time use)
- **Invisible text detection** — InvisibleText scanner catches hidden unicode injection tricks
- **Maintained pipeline** — new scanners added by maintainers — framework stays current
- **Standardised API** — consistent scan_prompt() / scan_output() interface regardless of scanner mix

#### Cons

- **Massive dependency** — 500MB-1GB model downloads on first run — significant for a developer plugin
- **Cold start latency** — first session load: 3-15 seconds for model initialisation — must pre-warm at SessionStart
- **RAM requirements** — transformer models require 1-2GB RAM with full stack loaded
- **Secrets scanner is redundant** — LLM Guard's Secrets scanner literally wraps detect-secrets — no added value
- **PromptInjection too slow** — 2-5 seconds per prompt on CPU — impossible within 30-second UserPromptSubmit timeout
- **Wrong threat model for ML scanners** — Toxicity, Bias, Relevance solve different problems than secret/PII detection
- **Framework complexity** — additional abstraction layer over detect-secrets adds complexity without benefit for core use case
- **Internet required first run** — users need internet connection for initial model downloads
- **Better PII path exists** — Presidio directly gives the same NLP PII capability without the full framework overhead

---

## 5. Side-by-Side Master Comparison

### Capability Comparison

| Capability | detect-secrets + Regex + Presidio | LLM Guard |
|---|---|---|
| AWS, Azure, IBM Cloud keys | ✅ Full coverage via detect-secrets | ✅ Same — wraps detect-secrets |
| GitHub, GitLab, npm tokens | ✅ Full coverage via detect-secrets | ✅ Same — wraps detect-secrets |
| Stripe, Square payment keys | ✅ Full coverage via detect-secrets | ✅ Same — wraps detect-secrets |
| Slack, SendGrid, Twilio keys | ✅ Full coverage via detect-secrets | ✅ Same — wraps detect-secrets |
| JWT, Private Keys, Basic Auth | ✅ Full coverage via detect-secrets | ✅ Same — wraps detect-secrets |
| Unknown / custom API keys | ✅ Entropy detection catches them | ✅ Same entropy detection underneath |
| Secret-looking assignments | ✅ KeywordDetector | ✅ Same KeywordDetector |
| SSN, Credit Card (structured) | ✅ Custom regex + Presidio NLP | ✅ Presidio NLP (same capability) |
| Email, Phone (structured) | ✅ Custom regex + Presidio NLP | ✅ Presidio NLP (same capability) |
| Names, Addresses (unstructured) | ✅ Presidio NLP | ✅ Presidio NLP (same capability) |
| Passport, Driving Licence IDs | ✅ Presidio NLP (multi-country) | ✅ Presidio NLP (same capability) |
| DB connection strings | ✅ Custom regex | ⚠️ Partial — some formats only |
| Output / response scanning | ✅ Via Stop hook (same scanners) | ✅ Built-in output pipeline |
| Secrets in Claude's response | ✅ detect-secrets on Stop hook | ✅ Output Secrets scanner |
| Unstructured PII in response | ✅ Presidio on Stop hook | ✅ Presidio PII output scanner |
| Prompt injection detection | ❌ Not supported | ⚠️ Available but 2-5s — too slow on CPU |
| Invisible unicode characters | ❌ Not detected | ✅ InvisibleText scanner |
| Risk / confidence scoring | ⚠️ Presidio gives partial scores | ✅ 0.0 to 1.0 per scanner |
| Malicious URL detection | ❌ Not supported | ✅ MaliciousURLs output scanner |
| Token limit enforcement | ❌ Not supported | ✅ TokenLimit input scanner |

---

### Operational Comparison

| Aspect | detect-secrets + Regex + Presidio | LLM Guard |
|---|---|---|
| Install size | ~100MB (Presidio models) | ~50MB package + 100MB-1GB models |
| First-run experience | ~100MB Presidio model download | Silent 100MB-1GB model download |
| Cold start time | ~1-3 seconds (Presidio load) | 3-15 seconds (all models) |
| Per-prompt latency | 5-200ms + ~50ms Presidio | 50-500ms (rule-based only) |
| RAM requirements | ~500MB (Presidio + spaCy) | 1-2GB with transformers loaded |
| GPU requirement | None | None (but CPU slow for ML scanners) |
| Works offline | ✅ After first download | ✅ After first download |
| Internet on first run | Required for Presidio models | Required for model downloads |
| Pre-warming needed | Yes — Presidio at SessionStart | Yes — all models at SessionStart |
| Thread safety | ✅ transient_settings is thread-local | ✅ Stateless scanner calls |
| Maintenance burden | Low — add regex patterns as needed | Low — framework maintained externally |

---

### Hook Integration Comparison

| Hook | detect-secrets + Regex + Presidio | LLM Guard |
|---|---|---|
| UserPromptSubmit | ✅ scan_line() + Presidio + regex. Well within 30s timeout if pre-warmed. | ⚠️ Rule-based scanners fine. ML scanners (PromptInjection) too slow. |
| PreToolUse | ✅ Scan file path — block .env / secrets file reads | ✅ Same capability |
| PostToolUse | ✅ Scan tool_response contents for leaked secrets | ✅ Same capability |
| Stop (output scan) | ✅ Same scanners on last_assistant_message. Claude regenerates on block. | ✅ scan_output() pipeline. Same underlying capability. |
| SessionStart (pre-warm) | Required — load Presidio models here | Required — load all models here |

---

## 6. Decision Framework & Recommendation

### When To Choose Each Approach

| Scenario | Recommended | Reason |
|---|---|---|
| Core secret / credential detection | detect-secrets + regex | Purpose-built, faster, zero overhead |
| Users are developers pasting code | detect-secrets + regex | API keys have known formats — covers 95% of real cases |
| Need NLP PII (names, addresses) | Presidio directly | Same capability as LLM Guard Anonymize without full framework |
| Need output scanning | detect-secrets + regex on Stop hook | Same scanners work — no LLM Guard needed |
| Need injection detection | LLM Guard (if latency acceptable) | PromptInjection scanner — only viable with GPU or async |
| Need risk confidence scores | LLM Guard | Only LLM Guard provides 0.0-1.0 scores per finding |
| Enterprise users with rich PII | Presidio (either approach) | NLP entity recognition adds genuine value over regex |
| Need invisible text detection | LLM Guard | InvisibleText scanner not available elsewhere |
| Plugin size is a concern | detect-secrets + regex | Much lighter than full LLM Guard stack |

---

### Recommended Path

**Phase 1 — Implement Now**

detect-secrets + custom regex on both `UserPromptSubmit` and `Stop` hooks. Covers all structured secrets, all known API key formats, entropy-based unknown keys, and structured PII. Ships fast, zero model overhead.

**Phase 2 — Add Next**

Add Microsoft Presidio directly (`pip install presidio-analyzer`) for NLP PII detection. Pre-warm at `SessionStart`. This gives NLP-based names, addresses, and context-aware PII detection — same capability as LLM Guard's Anonymize and PII scanners without the full framework.

**Skip Permanently**

LLM Guard's ML-based scanners — PromptInjection, Toxicity, Bias. These solve different threat models (content moderation, jailbreak detection) and are too slow for a real-time prompt hook on CPU. Not relevant to secret and credential protection.

---

### Final Verdict

| Dimension | Winner | Notes |
|---|---|---|
| Secret detection accuracy | Tie | Both use detect-secrets underneath — identical for structured secrets |
| Structured PII (SSN, CC) | Tie | Both handle well — detect-secrets + regex equally effective |
| Unstructured PII (names, addresses) | Tie (with Presidio added) | Both use Presidio NLP — same capability |
| Performance on prompt hook | detect-secrets + Presidio | LLM Guard ML scanners too slow for 30s timeout |
| Plugin size and weight | detect-secrets + Presidio | Lighter — only Presidio models, not full transformer stack |
| Output response scanning | Tie | Same capability achievable on Stop hook with either approach |
| Setup complexity | detect-secrets + Presidio | Simpler — no LLM Guard framework layer |
| Future extensibility | LLM Guard | Richer scanner ecosystem if requirements grow |
| Right choice for current phase | detect-secrets + Presidio | Covers the realistic threat model with appropriate overhead |

---

## 7. Database Design & Operational Flow

### Table Design — Key Principle

```
prompt table       →  clean prompts only — what Claude actually processed
blocked_prompts    →  intercepted prompts — never reached Claude
response table     →  Claude's responses with scan metadata
security_events    →  every individual finding across all scan points
```

Prompt table and blocked_prompts are intentionally separate because they represent fundamentally different things — one is a real interaction, the other is a security interception that never became an interaction.

---

### Prompt Table — Add Columns, Keep Clean

Only receives an entry AFTER scan passes. Every row = Claude saw this = real interaction.

```
Existing columns stay unchanged.

Add:
  is_scanned          boolean    scan was run on this prompt
  scan_duration_ms    integer    performance tracking
  finding_count       integer    always 0 here (only clean prompts land here)
  masked_prompt       text       if minor item masked but allowed through
```

---

### New: blocked_prompts Table

Created only when `UserPromptSubmit` is blocked. These never became real prompts.

```
id                  uuid / serial
session_id          varchar
raw_prompt_hash     varchar       SHA256 only — NEVER store raw prompt
masked_prompt       text          what the masked version looked like
finding_count       integer       how many secrets found
finding_labels      jsonb         ["AWS Access Key", "SSN"]
finding_sources     jsonb         ["detect-secrets", "presidio", "custom"]
severity_highest    varchar       HIGH / MEDIUM / LOW
scan_duration_ms    integer
scan_strategy       varchar       full / chunked / filtered
user_warned         boolean
created_at          timestamp
```

No foreign key to prompt table — these never became prompts.

---

### Response Table — Add Columns

```
Existing columns stay unchanged.

Add:
  is_scanned          boolean
  scan_duration_ms    integer
  has_findings        boolean
  masked_response     text          store masked version if findings exist
  finding_count       integer
  finding_labels      jsonb
```

---

### New: security_events Table

One row per individual finding. Links to whichever table originated the event.

```
id                  uuid / serial
session_id          varchar
direction           varchar       input_blocked / input_clean / tool_blocked / tool_output / output
source_table        varchar       blocked_prompts / prompts / responses
source_id           integer       foreign key to whichever source_table
finding_label       varchar       "AWS Access Key"
finding_source      varchar       detect-secrets / presidio / custom
severity            varchar       HIGH / MEDIUM / LOW
created_at          timestamp
```

---

### Raw vs Masked Rule

```
Raw prompt      →  NEVER stored anywhere in DB
Masked prompt   →  stored in blocked_prompts.masked_prompt
                   stored in prompt.masked_prompt (if minor masking applied)
Raw response    →  stored as usual IF no findings
Masked response →  stored in response.masked_response IF findings exist
SHA256 hash     →  stored in blocked_prompts for dedup / audit only
```

---

### Operational Flow — Input Side

```
User types prompt and hits enter
        ↓
UserPromptSubmit hook fires (before Claude sees anything)
        ↓
security_scan.py receives prompt via stdin JSON
        ↓
  Layer 1: detect-secrets scans line by line
  Layer 2: Presidio NLP scans for unstructured PII
  Layer 3: Custom regex scans for SSN, CC, DB strings
        ↓
        ├── CLEAN (no findings)
        │     Exit 0 — prompt goes through to Claude
        │     INSERT into prompt table
        │     (is_scanned=true, finding_count=0)
        │
        └── BLOCKED (findings found)
              Mask prompt → replace secrets with [REDACTED:LABEL]
              Block original (decision: block, suppressOriginalPrompt: true)
              Show warning to user with masked version to copy-paste
              INSERT into blocked_prompts table
              INSERT into security_events table (one row per finding)
              Claude receives NOTHING — process ends here
              User copies masked version → resubmits → clean → goes to Claude
```

---

### Operational Flow — Output Side

```
Claude finishes generating response
        ↓
Stop hook fires
        ↓
output_scan.py receives last_assistant_message via stdin
        ↓
  Same three layers scan Claude's response text
        ↓
        ├── CLEAN (no findings)
        │     Exit 0 — response shown to user normally
        │     UPDATE response table (is_scanned=true, has_findings=false)
        │
        └── FINDINGS in response
              Mask response
              Block Claude from stopping (decision: block)
              reason = "Regenerate using this masked version: [masked]"
              Claude sees reason as next instruction → regenerates
              User sees clean response automatically — no action needed
              UPDATE response table (has_findings=true, masked_response=...)
              INSERT into security_events table
```

---

### Query Examples With This Design

```sql
-- How many prompts were blocked this week?
SELECT COUNT(*) FROM blocked_prompts
WHERE created_at >= NOW() - INTERVAL '7 days';

-- What types of secrets are most common?
SELECT finding_label, COUNT(*) as count
FROM security_events
GROUP BY finding_label
ORDER BY count DESC;

-- Which sessions had output leaks?
SELECT DISTINCT session_id FROM security_events
WHERE direction = 'output';

-- Scan performance over time
SELECT DATE(created_at), AVG(scan_duration_ms)
FROM prompt table
GROUP BY DATE(created_at)
ORDER BY 1;

-- Blocked vs clean ratio per day
SELECT
  DATE(created_at) as day,
  COUNT(*) as blocked
FROM blocked_prompts
GROUP BY DATE(created_at);
```

---

## 8. Four-Point Scanning Coverage

From the Claude Code hooks documentation, four hook events give complete coverage across the full interaction lifecycle.

### Point 1 — UserPromptSubmit (Input)

```
What:    User's raw prompt before Claude sees it
Hook:    UserPromptSubmit
Action:  Block if secret found — Claude receives nothing
Output:  decision: "block", suppressOriginalPrompt: true
         Show masked version to user for resubmission
DB:      INSERT into blocked_prompts + security_events
```

---

### Point 2 — PreToolUse (File Read Prevention)

From the docs: *"Runs after Claude creates tool parameters and before processing the tool call. Can block it."*

Supported tools: `Read`, `Bash`, `Grep`, `Glob`

```
What:    Files Claude is about to read
Hook:    PreToolUse with matcher "Read|Bash|Grep"
Action:  Check tool_input.file_path
         If .env / .secret / config / credentials file → deny
Output:  hookSpecificOutput.permissionDecision: "deny"
DB:      INSERT into security_events (direction: tool_blocked)
```

This prevents Claude from reading `.env` files, AWS credential files, SSH keys, and other sensitive files on disk entirely — before the read happens.

---

### Point 3 — PostToolUse (File Content Scan)

From the docs: *"Runs immediately after a tool completes successfully. tool_response contains the result it returned."*

```
What:    Contents Claude just read from files
Hook:    PostToolUse with matcher "Read|Bash|Grep"
Action:  Scan tool_response for secrets
         If found → decision: "block", reason tells Claude not to use values
Output:  decision: "block"
         reason: "Response contained sensitive data. Do not use these values."
DB:      INSERT into security_events (direction: tool_output)
```

This catches cases where Claude reads a file that was not blocked by PreToolUse but contains secrets — ensuring Claude does not use or echo those values.

---

### Point 4 — Stop (Response Scan)

From the docs: *"Runs when the main Claude Code agent has finished responding. last_assistant_message contains Claude's final response. decision: block prevents Claude from stopping, reason becomes Claude's next instruction."*

```
What:    Claude's complete final response
Hook:    Stop
Action:  Scan last_assistant_message for secrets
         If found → block, inject masked version as Claude's next instruction
Output:  decision: "block"
         reason: "Regenerate using this masked version: [masked_response]"
DB:      UPDATE response table + INSERT into security_events (direction: output)
```

Unlike `UserPromptSubmit` where block kills everything, `Stop` block makes Claude regenerate — user gets a clean response automatically with no action required.

---

### Coverage Map

| Scan Point | Hook | Blocks What | User Action Required |
|---|---|---|---|
| Input prompt | UserPromptSubmit | Raw prompt with secrets | Yes — resubmit masked version |
| File read prevention | PreToolUse | .env / secrets file reads | No — Claude told it was denied |
| File content | PostToolUse | Secrets in files Claude read | No — Claude told not to use values |
| Response output | Stop | Secrets in Claude's response | No — Claude regenerates automatically |

---

## 9. Final Coverage Summary

### What Gets Covered With Phase 1 (detect-secrets + Regex)

| Threat | Covered | Method |
|---|---|---|
| AWS, Azure, IBM Cloud keys | ✅ Yes | detect-secrets AWSKeyDetector, AzureStorageKeyDetector |
| GitHub, GitLab, npm tokens | ✅ Yes | detect-secrets GitHubTokenDetector, GitLabTokenDetector, NpmDetector |
| Stripe, Square payment keys | ✅ Yes | detect-secrets StripeDetector, SquareOAuthDetector |
| Slack, SendGrid, Twilio, Mailchimp | ✅ Yes | detect-secrets SlackDetector, SendGridDetector, TwilioKeyDetector |
| JWT, Private Keys, Basic Auth | ✅ Yes | detect-secrets JwtTokenDetector, PrivateKeyDetector, BasicAuthDetector |
| Unknown / internal API keys | ✅ Yes | HexHighEntropyString and Base64HighEntropyString entropy detectors |
| Secret-looking variable assignments | ✅ Yes | KeywordDetector — api_key='x', secret='x', password='x' |
| SSN, Credit Card, Phone, Email | ✅ Yes | Custom regex patterns |
| Database connection strings | ✅ Yes | Custom regex — mongodb, postgresql, mysql, redis |
| OpenAI, Anthropic, GCP keys | ✅ Yes | Custom regex patterns |
| Generic password assignments | ✅ Yes | KeywordDetector + custom regex |
| Secrets in Claude's response | ✅ Yes | Same scanners on Stop hook — last_assistant_message field |
| Secrets in files Claude reads | ✅ Yes | PreToolUse + PostToolUse hooks |
| Large prompts (1000+ lines) | ✅ Yes | Chunked parallel scanning strategy |
| Performance within 30s timeout | ✅ Yes | Pre-filter + strategy selector keeps well under 20s |

### What Gets Added With Phase 2 (+ Presidio)

| Threat | Covered | Method |
|---|---|---|
| Names, addresses (unstructured) | ✅ Yes | Presidio NLP entity recognition |
| Passport numbers | ✅ Yes | Presidio multi-country coverage |
| Driver's licence IDs | ✅ Yes | Presidio multi-format coverage |
| SSN without dashes (context-aware) | ✅ Yes | Presidio understands surrounding context |
| Medical / legal identifiers | ✅ Yes | Presidio entity types |
| PII in Claude's response (unstructured) | ✅ Yes | Presidio on Stop hook output scan |

### What Remains Out of Scope

| Threat | Status | Notes |
|---|---|---|
| Prompt injection / jailbreaks | ❌ Not in scope | Different threat model — consider LLM Guard PromptInjection if needed with GPU |
| Hidden unicode injection | ❌ Not in scope | LLM Guard InvisibleText scanner — low priority |
| Malicious URLs in response | ❌ Not in scope | LLM Guard MaliciousURLs — add if needed |
| Toxicity / harmful content | ❌ Not in scope | Different product concern entirely |

---

*Document prepared for architectural decision-making. Recommendations based on the specific constraints of a Python-based Claude Code telemetry plugin with a 30-second UserPromptSubmit hook timeout and plugin distribution requirements.*
