# claude-telemetry — Observability Stack for Claude Code

> **Track every prompt, token, and tool call across Claude Code sessions.**

claude-telemetry gives you full visibility into your Claude Code workflow — prompt history, token usage, and tool telemetry — so nothing gets lost between sessions.

---

## What claude-telemetry Observes

### Prompt Telemetry
Full capture of your Claude Code conversations:
- Every prompt you send and response you receive
- Tool calls invoked and their results
- Files read, written, and modified
- Timestamps for every interaction

### Token Observability
Real-time and historical token metrics:
- Input / output tokens per prompt and per session
- Cache hit rates and cache token savings
- Tool usage token breakdown
- Project-level and session-level totals

### Session Observability
Continuous session tracking across your work:
- Session start/end times and working directories
- Activity timelines across sessions
- Per-project history and decision log
- What was built, fixed, or deployed — and when

---

## How It Works

claude-telemetry hooks into Claude Code as a plugin and passively captures telemetry while you work. No changes to your workflow required.

```
Session starts  →  claude-telemetry initializes
You work        →  Prompts, tools, tokens captured
Session ends    →  Data persisted to local SQLite
```

All data is stored locally at `~/.cloudbyte/data/cloudbyte.db`. Nothing leaves your machine.

---

## Use Cases

### Resume Context Across Sessions
```
"Continue the auth module from last Tuesday"
→ claude-telemetry surfaces: files modified, decisions made, what's left
```

### Debug Token Spend
```
"Which prompts are costing the most?"
→ Per-prompt token breakdown, cache effectiveness, session totals
```

### Audit Tool Usage
```
"What tools did Claude use on the payment service?"
→ Full tool call log with inputs, outputs, and token cost
```

### Reconstruct Decisions
```
"Why did we switch from Memcached to Redis?"
→ The session, the reasoning, what was compared, what shipped
```

---

## Installation

```bash
claude plugin install CloudByte-AI/claude-telemetry
```

Or manually:

```bash
git clone https://github.com/CloudByte-AI/claude-telemetry.git
cd claude-telemetry
python scripts/install.py
```

---

## Data Schema

**Location:** `~/.cloudbyte/data/cloudbyte.db` (SQLite, local only)

| Table | What's Captured |
|-------|----------------|
| `sessions` | Start/end time, project, working directory |
| `prompts` | User messages sent to Claude |
| `responses` | Claude's replies |
| `tool_calls` | Tool name, inputs, outputs |
| `token_usage` | Input / output / cache tokens per turn |
| `file_events` | Files read and modified |

---

## Token Metrics

| Metric | Description |
|--------|-------------|
| `input_tokens` | Tokens in your prompt + context |
| `output_tokens` | Tokens in Claude's response |
| `cache_read_tokens` | Tokens served from prompt cache |
| `cache_write_tokens` | Tokens written to cache |
| `tool_tokens` | Tokens consumed by tool results |

---

## Privacy

- All data stored locally on your machine
- No telemetry sent to external servers
- No cloud sync unless you configure it
- Full control — export or delete any time

---

## Requirements

- Python 3.10+
- Claude Code CLI
- SQLite3 (bundled with Python)

---

## Roadmap

- **Search** — Query across all sessions and prompts
- **Dashboard** — Visual token and activity analytics
- **Alerts** — Notify on token budget thresholds
- **Cloud Sync** — Optional encrypted backup
- **Claude Memory Integration** — Surface past context automatically

---

**Install:** `claude plugin install CloudByte-AI/claude-telemetry`
 