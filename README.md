# claude-telemetry

> Full observability for Claude Code — prompt history, token usage, and tool telemetry across every session.

[![Plugin](https://img.shields.io/badge/Claude_Code-Plugin-blue)](https://github.com/CloudByte-AI/claude-telemetry)
[![License](https://img.shields.io/badge/license-Apache_2.0-green)](LICENSE)
[![Storage](https://img.shields.io/badge/storage-local_SQLite-orange)](https://sqlite.org)

---

## Overview

`claude-telemetry` hooks into Claude Code as a passive plugin and captures everything that happens in your sessions — prompts, responses, tool calls, file events, and token usage — without changing your workflow.

All data is stored locally in a SQLite database. Nothing leaves your machine.

---

## Prerequisites

Before installing this plugin, ensure you have the following:

| Requirement | Minimum Version | How to Check | How to Install |
|-------------|-----------------|--------------|----------------|
| **Python** | 3.12+ | `python --version` | [python.org](https://www.python.org/downloads/) |
| **uv** | Latest | `uv --version` | `pip install uv` or [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| **Claude Code CLI** | Latest | `claude --version` | [claude.ai/code](https://claude.ai/code) |

**Note:** This plugin requires `uv` as the package manager. Make sure it's installed before proceeding.

---

## Installation

### Via Claude Marketplace (Recommended)

```bash
# Add the marketplace
/plugin marketplace add CloudByte-AI/claude-telemetry

# Install the plugin
/plugin install claude-telemetry@CloudByte-AI

# Reload plugins
/reload-plugins
```

### Manual Installation

```bash
# Clone the repository
git clone https://github.com/CloudByte-AI/claude-telemetry.git
cd claude-telemetry

# Install dependencies
uv sync

# Link as a Claude plugin
ln -s $(pwd) ~/.claude/plugins/claude-telemetry
```

---

## How It Works

```
Session starts  →  claude-telemetry initializes
                 ├─ MCP Server starts (background)
                 ├─ Database schema verified/created
                 └─ Session record created

You work        →  Prompts, tools, and tokens are captured passively
                 ├─ Every prompt → USER_PROMPT table
                 ├─ Every response → RESPONSE table
                 ├─ Every tool call → TOOL table
                 └─ Token usage → IO_TOKENS / TOOL_TOKENS tables

Session ends    →  All data persisted to local SQLite
```

**Database location:** `~/.cloudbyte/data/cloudbyte.db`

**MCP Server:** Provides tools for querying your telemetry data within Claude Code conversations.

No configuration required. No changes to your workflow.

---

## What Gets Captured

### Prompt Telemetry
- Every prompt sent and response received
- Tool calls with their inputs and outputs
- Files read, written, and modified
- Timestamps for every interaction

### Token Observability
- Input / output tokens per prompt and per session
- Cache hit rates and cache token savings
- Tool usage token breakdown
- Project-level and session-level totals

### Session Tracking
- Session start/end times and working directories
- Activity timelines across sessions
- Per-project history and decision log
- Full record of what was built, fixed, or deployed — and when

---

## MCP Tools

The plugin includes an MCP server (`cloudbyte-obs`) that provides the following tool:

| Tool | Description |
|------|-------------|
| `record_observation` | Record technical observations about work done (type, title, subtitle, narrative, facts, concepts, files) |

This tool is called automatically after responses where tools were used or meaningful changes were made. Observations are stored in the `HOOK_OBSERVATION` table for later analysis.

---

## Use Cases

**Resume context across sessions**
```
"Continue the auth module from last Tuesday"
→ Surfaces files modified, decisions made, and what remains
```

**Debug token spend**
```
"Which prompts are costing the most?"
→ Per-prompt token breakdown, cache effectiveness, session totals
```

**Audit tool usage**
```
"What tools did Claude use on the payment service?"
→ Full tool call log with inputs, outputs, and token cost
```

**Reconstruct decisions**
```
"Why did we switch from Memcached to Redis?"
→ The session, the reasoning, what was compared, what shipped
```

---

## Data Schema

**Location:** `~/.cloudbyte/data/cloudbyte.db` — SQLite, stored locally

| Table | Description |
|---|---|
| `PROJECT` | Project ID, name, path, created_at |
| `SESSION` | Session ID, project reference, working directory, JSONL file |
| `RAW_LOG` | Complete event JSON for each interaction |
| `USER_PROMPT` | User prompts with UUID, parent references, timestamps |
| `RESPONSE` | Claude's responses linked to prompts |
| `TOOL` | Tool calls with inputs, outputs, and model info |
| `THINKING` | Thinking process data for applicable models |
| `IO_TOKENS` | Input/output/cache tokens per message |
| `TOOL_TOKENS` | Token usage for tool calls |
| `OBSERVATION` | Technical observations and learnings |
| `SESSION_SUMMARY` | Summarized session data |

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

## Local Development

### Setup

```bash
# Clone the repo
git clone https://github.com/CloudByte-AI/claude-telemetry.git
cd claude-telemetry

# Create virtual environment and install dependencies
uv sync

# Run tests (if available)
uv run pytest
```

### Running the MCP Server Standalone

```bash
# Run the MCP server directly for testing
uv run python -m src.mcp.server
```

### Project Structure

```
claude-telemetry/
├── src/
│   ├── app/              # Flask dashboard
│   ├── common/           # Shared utilities
│   ├── core/             # Event processing
│   ├── db/               # Database schema, writers, managers
│   ├── handlers/         # Hook handlers (session_start, user_prompt, etc.)
│   ├── integrations/     # External integrations (Claude, LLM providers)
│   ├── mcp/              # MCP server implementation
│   └── workers/          # Background workers
├── hooks/                # Claude hook definitions
├── scripts/              # Setup and validation scripts
├── pyproject.toml        # Project dependencies
└── README.md
```

---

## Privacy

- All data is stored locally on your machine
- No telemetry is sent to external servers
- No cloud sync unless explicitly configured
- Export or delete your data at any time

**Database path:** `~/.cloudbyte/data/cloudbyte.db`

To delete all telemetry data:
```bash
rm ~/.cloudbyte/data/cloudbyte.db
```

---

## Troubleshooting

### Plugin not loading
```bash
# Check plugin status
/plugin list

# Reload plugins
/reload-plugins
```

### MCP server not responding
```bash
# Check if uv is installed
uv --version

# Check MCP server logs
tail -f ~/.claude/logs/claude.log
```

### Database errors
```bash
# Reinitialize the database
rm ~/.cloudbyte/data/cloudbyte.db
# Restart Claude Code to trigger re-initialization
```

---

## Contributing

Issues and pull requests are welcome. Please open an issue first to discuss significant changes.

### Development Workflow

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests (`uv run pytest`)
5. Commit your changes (`git commit -m 'Add amazing feature'`)
6. Push to the branch (`git push origin feature/amazing-feature`)
7. Open a Pull Request

---

## License

Apache 2.0 © [CloudByte-AI](https://github.com/CloudByte-AI)