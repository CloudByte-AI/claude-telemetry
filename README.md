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

## Installation

```bash
/plugin marketplace add CloudByte-AI/claude-telemetry
/plugin install claude-telemetry@CloudByte-AI
/reload-plugins
```

---

## How It Works

```
Session starts  →  claude-telemetry initializes
You work        →  Prompts, tools, and tokens are captured passively
Session ends    →  All data persisted to local SQLite
```

**Database location:** `~/.cloudbyte/data/cloudbyte.db`

No configuration required. No changes to your workflow.

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
| `sessions` | Start/end time, project, working directory |
| `prompts` | User messages sent to Claude |
| `responses` | Claude's replies |
| `tool_calls` | Tool name, inputs, and outputs |
| `token_usage` | Input / output / cache tokens per turn |
| `file_events` | Files read and modified |

---

## Token Metrics Reference

| Metric | Description |
|---|---|
| `input_tokens` | Tokens in your prompt and context |
| `output_tokens` | Tokens in Claude's response |
| `cache_read_tokens` | Tokens served from prompt cache |
| `cache_write_tokens` | Tokens written to cache |
| `tool_tokens` | Tokens consumed by tool results |

---

## Privacy

- All data is stored locally on your machine
- No telemetry is sent to external servers
- No cloud sync unless explicitly configured
- Export or delete your data at any time

---

## Requirements

| Dependency | Version |
|---|---|
| Python | 3.10+ |
| Claude Code CLI | Latest |
| SQLite3 | Bundled with Python |

---

## Contributing

Issues and pull requests are welcome. Please open an issue first to discuss significant changes.

---

## License

Apache 2.0 © [CloudByte-AI](https://github.com/CloudByte-AI)