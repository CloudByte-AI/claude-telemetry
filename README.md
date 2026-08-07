# claude-telemetry

> Full observability for Claude Code and Cursor - prompt history, token usage, tool telemetry, secret scanning and a local dashboard, across every session.

[![Plugin](https://img.shields.io/badge/Claude_Code-Plugin-blue)](https://github.com/CloudByte-AI/claude-telemetry)
[![Cursor](https://img.shields.io/badge/Cursor-Plugin-purple)](https://github.com/CloudByte-AI/claude-telemetry)
[![License](https://img.shields.io/badge/license-Apache_2.0-green)](LICENSE)
[![Storage](https://img.shields.io/badge/storage-local_SQLite-orange)](https://sqlite.org)

---

## Overview

`claude-telemetry` hooks into Claude Code (and Cursor) as a passive plugin and captures everything that happens in your sessions - prompts, responses, tool calls, thinking, file events and token usage - without changing your workflow.

Everything is stored locally in a SQLite database at `~/.cloudbyte/data/cloudbyte.db`, browsable through a dashboard on `http://localhost:4723`. Nothing leaves your machine.

**What's in the box**

| | |
|---|---|
| **Passive capture** | Prompts, responses, thinking, tool calls, tokens - via editor hooks |
| **Local dashboard** | FastAPI UI on `localhost:4723` - sessions, conversations, tokens, tools, projects |
| **Secret & PII scanning** | 28 detectors across 9 categories, three profiles, can block a prompt before it is sent |
| **Observations** | An MCP `record_observation` tool that lets the agent write a structured work log |
| **Cursor support** | The same database, the same dashboard - sessions tagged by originating client |
| **One-line installers** | Windows / macOS / Linux bootstrap that installs every missing dependency itself |
| **Matching uninstallers** | Remove the plugin, optionally the marketplace entry, optionally the data |

---

## Installation

Three ways in. All of them end at the same place: the plugin registered with your editor and its environment prepared.

### 1. One-line install (recommended)

The bootstrap installs everything that is missing - `uv`, a managed Python 3.12, the editor CLI itself if you do not have one - registers the marketplace, installs the plugin and prepares its virtualenv. The only question it asks is which editors to install into, and even that is skipped when there is no terminal to ask on.

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.ps1 | iex
```

**macOS / Linux (bash)**

```bash
curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.sh | bash
```

Then activate the plugin in your editor - `/reload-plugins`, or restart the session.

**What it does, in order**

```
Step 1  Detect editors, ask which to use, install any missing CLI
Step 2  Ensure uv
Step 3  Run prerequisites validation (validate.sh / validate.ps1)
Step 4  Add the marketplace to each editor
Step 5  Install the plugin  (Cursor: guided IDE step, then verified)
Step 6  Prepare each plugin environment (uv sync)
Step 7  Print activation instructions and the summary
```

**Exit codes:** `0` success · `1` unexpected failure · `2` no usable editor CLI and the automatic CLI install failed · `4` prerequisites validation failed · `5` marketplace add failed for every target · `6` plugin install failed for every target.

### 2. In-editor marketplace

**Claude Code**

```bash
/plugin marketplace add CloudByte-AI/claude-telemetry
/plugin install claude-telemetry@claude-telemetry
/reload-plugins
```

**Cursor** - the Cursor CLI has no `plugin install` verb, so the marketplace is added from the CLI and the plugin is enabled from the IDE:

```bash
cursor-agent plugin marketplace add https://github.com/CloudByte-AI/claude-telemetry
```

Then: **Settings → Plugins → cursor-telemetry → Install**, and restart the session.

### 3. Via the CloudByte skill (`npx cloudbyte-skills`)

Installs a Claude Code skill that drives the plugin installation from inside a session.

**Step 1** - install the Claude Code CLI, if you do not have it:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

**Step 2** - the skill installer needs Node.js:

```bash
brew install node          # macOS - or use your platform's package manager
```

**Step 3** - install the skill:

```bash
npx cloudbyte-skills claude-plugin-install
```

**Step 4** - open Claude Code in your terminal:

```bash
claude
```

**Step 5** - run the skill:

```
/cloudbyte-claude-plugin-install
```

---

## Prerequisites

The installers handle all of this for you - the table is here for troubleshooting.

| Requirement | Minimum | How to check | Installed automatically by |
|---|---|---|---|
| **uv** | Latest | `uv --version` | `scripts/validate.sh` / `validate.ps1` |
| **Python** | 3.12 | `uv python list` | `uv python install 3.12` (managed, not system) |
| **Claude Code CLI** | Latest | `claude --version` | Installer Step 1 |
| **Cursor CLI** (optional) | Latest | `cursor-agent --version` | Installer Step 1, when Cursor is present |

Python is provisioned **through uv**, into uv's own data directory. It does not touch your `PATH`, your system `python`, or the `py` launcher, and a pre-existing older interpreter is left exactly as it is.

`validate.*` also runs as the plugin's `Setup` hook, so a plugin installed from the marketplace still gets its prerequisites - it never prompts, and it logs to `~/.cloudbyte/logs/setup/setup-<date>.log`.

---

## Uninstall

```powershell
# Windows
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.ps1))) -Script uninstall.ps1
```

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/CloudByte-AI/claude-telemetry/main/scripts/bootstrap.sh | bash -s -- --script uninstall.sh
```

Nothing is removed without being asked for, and both destructive answers - removing the marketplace entry and deleting `~/.cloudbyte` - default to **No**.

| bash | PowerShell | Description |
|---|---|---|
| `--target auto\|ask\|claude\|cursor\|both` | `-Target …` | Which editors to remove from |
| `--yes` / `--non-interactive` | `-Yes` / `-NonInteractive` | Never prompt. Uninstalls everywhere but **keeps** the marketplace entry and the data |
| `--remove-marketplace` / `--keep-marketplace` | `-RemoveMarketplace` / `-KeepMarketplace` | Decide the marketplace entry without asking |
| `--delete-data` / `--keep-data` | `-DeleteData` / `-KeepData` | Decide `~/.cloudbyte` without asking. Deletion is permanent |
| `--cursor-dir DIR` | `-CursorDir DIR` | Cursor's data directory |

---

## How It Works

```
Session starts  →  claude-telemetry initializes
                 ├─ Prerequisites verified (Setup hook)
                 ├─ MCP server starts (background)
                 ├─ Dashboard worker starts on localhost:4723
                 ├─ Database schema verified / migrated
                 └─ Session record created

You work        →  Prompts, tools and tokens are captured passively
                 ├─ Every prompt   → USER_PROMPT   (scanned for secrets first)
                 ├─ Every response → RESPONSE
                 ├─ Every tool call→ TOOL
                 ├─ Thinking       → THINKING
                 ├─ Token usage    → IO_TOKENS / TOOL_TOKENS
                 └─ Agent write-ups→ HOOK_OBSERVATION (via MCP)

Session ends    →  Data persisted, worker torn down only when no other
                   session is still using it
```

No configuration required. No changes to your workflow.

### Hooks

**Claude Code** (`hooks/hooks.json`)

| Hook | Command | What it does |
|---|---|---|
| `Setup` | `scripts/validate.sh` | Installs `uv` and a managed Python 3.12 |
| `SessionStart` | `src.main session_start` | Creates project/session records, starts worker + dashboard |
| `UserPromptSubmit` | `src.main user_prompt` | Records the prompt, runs the security scan, can block |
| `Stop` | `src.main stop` | Ingests the transcript - responses, tools, thinking, tokens |
| `SessionEnd` | `src.main session_end` | Finalizes the session, guarded teardown of the worker |

**Cursor** (`.cursor/hooks.json`) adds `beforeSubmitPrompt`, `afterAgentResponse`, `postToolUse`, `afterMCPExecution` and `afterAgentThought`, handled by `src/cursor/main.py`. Both editors write to the same database; `SESSION.client` records which one a session came from.

---

## Dashboard

Starts automatically with your first session at **`http://localhost:4723`**.

| Page | What it shows |
|---|---|
| `/` | Overview - recent activity, totals, live updates over SSE |
| `/sessions` · `/sessions/{id}` | Session list and per-session detail |
| `/conversation/{prompt_id}` | Full prompt → response thread with tool calls |
| `/tokens` · `/tokens/session/{id}` · `/tokens/project/{id}` | Token analytics, cache effectiveness |
| `/tools` · `/tools/session/{id}` · `/tools/project/{id}` | Tool usage breakdown and token cost |
| `/observations` · `/observations/{id}` | The agent's structured work log |
| `/projects` | Per-project history and totals |
| `/security` · `/security/events` | Scanner configuration and what was flagged or blocked |
| `/config` | Settings, database cleanup and log cleanup |
| `/events` | Server-sent events stream that keeps the UI live |
| `/version/status` · `/version/apply` | Detects a newer cached plugin version and restarts the workers to pick it up |

---

## Security Scanning

Prompts are scanned **before they are sent**. A finding can be recorded, masked, or block the prompt outright with a desktop notification pointing at `/security`.

- **28 detectors** across 9 categories: cloud & infrastructure (AWS, GCP, DigitalOcean, Cloudflare), AI/ML platforms (OpenAI, Anthropic, Groq, HuggingFace, Replicate, Cohere, Mistral), developer tools (GitHub, GitLab, npm, PyPI), payments (Stripe, Razorpay), communication (Twilio), databases (connection strings, JDBC), auth (JWT, private keys, bearer tokens, basic auth), PII (email, phone) and generic (entropy, keyword).
- **Three profiles** - `minimal`, `standard` (default, high-confidence detections only) and `strict`. Every detector category can be toggled individually.
- **Scope** - `prompt_only` (default) or `both`, to scan responses as well.
- **Custom patterns**, a **keyword blocklist** and an **allowlist** of values that must never be flagged - all editable from the dashboard, including a regex generator.
- Config lives in `~/.cloudbyte/security/security_profile.yaml` and is re-read on every scan. Findings land in `SECURITY_SCAN_EVENT` with the text masked.

---

## MCP Tools

The plugin ships an MCP server (`cloudbyte`) exposing one tool:

| Tool | Description |
|---|---|
| `record_observation` | Records a structured observation about work just done |

Every call carries eight fields: `type` (`bugfix` / `feature` / `refactor` / `change` / `discovery` / `decision`), `title`, `subtitle`, `narrative`, `facts[]`, `concepts[]`, `files_read[]` and `files_modified[]`.

The server enforces that contract rather than trusting it: incomplete payloads are rejected with an explanation the agent can act on, malformed ones are salvaged where the fields can be recovered, and duplicates are fingerprinted away. Strict mode is on by default (`CLOUDBYTE_OBS_STRICT=1`). Observations are stored in `HOOK_OBSERVATION` and rendered at `/observations`.

---

## Data Schema

**Location:** `~/.cloudbyte/data/cloudbyte.db` - SQLite, stored locally

| Table | Description |
|---|---|
| `PROJECT` | Project ID, name, path, created_at |
| `SESSION` | Session ID, project reference, working directory, transcript path, originating client (`claude_code` / `cursor`), end state |
| `RAW_LOG` | Complete event JSON for each interaction |
| `USER_PROMPT` | User prompts with UUID, parent references, timestamps |
| `RESPONSE` | Claude's responses linked to prompts |
| `TOOL` | Tool calls with inputs, outputs, and model info |
| `THINKING` | Thinking process data for applicable models |
| `IO_TOKENS` | Input/output/cache tokens per message |
| `TOOL_TOKENS` | Token usage for tool calls |
| `HOOK_OBSERVATION` | Observations recorded by the agent via the MCP `record_observation` tool |
| `SECURITY_SCAN_EVENT` | Secret/PII scan findings, with the masked text and what was blocked |

### On-disk layout

```
~/.cloudbyte/
├── data/cloudbyte.db            # everything captured
├── logs/                        # plugin logs
│   ├── claude/ · cursor/        # per-client logs
│   └── setup/                   # prerequisites validation logs
├── security/security_profile.yaml
├── active_sessions/             # guards the shared worker from early teardown
└── worker.pid
```

---

## Token Metrics Reference

| Metric | Description |
|---|---|
| `input_tokens` | Tokens in your prompt and context |
| `output_tokens` | Tokens in Claude's response |
| `cache_creation_tokens` | Tokens written to cache |
| `cache_read_tokens` | Tokens served from prompt cache (savings) |
| `tool_tokens` | Tokens consumed by tool results |

---

## Privacy

- All data is stored locally on your machine

**Database path:** `~/.cloudbyte/data/cloudbyte.db`

---

## Contributing

Issues and pull requests are welcome. Please open an issue first to discuss significant changes.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`uv run pytest`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

Installer and uninstaller options are documented in the comment header of each script under [scripts/](scripts/).

---

## License

Apache 2.0 © [CloudByte-AI](https://github.com/CloudByte-AI)
