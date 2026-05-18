# langmail

A multi-agent Gmail assistant built with LangChain and LangGraph. Reads your inbox, drafts context-aware replies, extracts action items, and flags security risks — all running locally with full user control.

> **Status:** Core system complete and working. See [What's Left](#whats-left) for remaining work.

---

## Features

- **Smart inbox triage** — classifies emails by urgency and action required
- **Security scanning** — detects phishing, suspicious links, and social engineering
- **Task extraction** — pulls deadlines and action items from email threads
- **Context-aware drafts** — writes replies that match your tone with that specific person
- **Local-first** — all data stays on your machine, encrypted at rest
- **Never auto-sends** — always creates Gmail drafts for your review

---

## Architecture

```
                        User (CLI)
                            |
                   [Supervisor Agent]
                  /     |     |     \
                 /      |     |      \
    [Email Reader] [Security] [Tasks] [Draft Writer]
                  \     |     |      /
                   \    |     |     /
                    Gmail API (OAuth2)
                    SQLite Index (~/.langmail/gmail.db)
                    Encrypted Cache (~/.langmail/cache/)
```

### Agents

| Agent | Model | Role |
|-------|-------|------|
| **Supervisor** | Claude Sonnet | Orchestrates all sub-agents, routes tasks, owns the ReAct loop |
| **Email Reader** | Claude Haiku | Fetches and classifies unread emails by priority and category |
| **Security Assessor** | Claude Haiku | Scans email threads for phishing, social engineering, and threats |
| **Task Extractor** | Claude Haiku | Pulls action items and deadlines from email threads |
| **Draft Writer** | Claude Sonnet | Writes tone-matched reply drafts using full relationship context |

Sub-agents run with **isolated context** — each receives only its task description, not the full parent conversation. This prevents context confusion and keeps reasoning focused.

### Tools

#### Gmail API Tools (`tools/gmail_tools.py`)

| Tool | Description |
|------|-------------|
| `gmail_fetch` | List recent inbox emails (metadata only, no bodies) |
| `gmail_read_thread` | Fetch full thread with decoded body text (handles multipart MIME) |
| `gmail_search` | Search with full Gmail query syntax (`from:`, `subject:`, `after:`, etc.) |
| `gmail_create_draft` | Create a reply draft — **never sends automatically** |
| `gmail_label` | Add or remove labels (UNREAD, STARRED, IMPORTANT, custom) |

#### Database Tools (`tools/db_tools.py`)

| Tool | Description |
|------|-------------|
| `db_lookup_contact` | Contact profile: name, relationship, message counts, first/last seen |
| `db_search_contacts` | Fuzzy search contacts by name or email |
| `db_get_thread_summaries` | Previous thread summaries with a contact (for context) |
| `db_get_sent_snippets` | Snippets of emails you sent to a contact (for tone matching) |
| `db_get_inbox_stats` | High-level counts: total messages, unread, contacts, threads |

#### Supervisor-Only Tools (`agents/supervisor.py`)

| Tool | Description |
|------|-------------|
| `task()` | Delegate to a sub-agent with isolated context |
| `load_draft_context()` | Pre-load 4 context files into state before delegating to Draft Writer |
| `think_tool()` | Internal reasoning step (reflection without tool call) |

### Tool Assignment per Agent

| Agent | Tools available |
|-------|----------------|
| **Supervisor** | `task`, `load_draft_context`, `think_tool`, `gmail_fetch`, `gmail_search`, `gmail_label`, `db_get_inbox_stats`, `db_lookup_contact`, `db_search_contacts`, `db_get_thread_summaries`, `db_get_sent_snippets` |
| **Email Reader** | `gmail_fetch`, `gmail_search` |
| **Security Assessor** | `gmail_read_thread`, `think_security` |
| **Task Extractor** | `gmail_read_thread`, `think_tasks` |
| **Draft Writer** | `gmail_read_thread`, `db_lookup_contact`, `db_get_thread_summaries`, `db_get_sent_snippets`, `gmail_create_draft`, `think_draft` |

Sub-agents are intentionally narrow — Email Reader only needs to list/search, Security Assessor only reads threads deeply, Draft Writer has everything needed to write a full context-aware reply. `think_*` tools are private reasoning steps inside each sub-agent (not exposed to the supervisor).

### Data Flow

```
Gmail API
   │
   ├─► gmail_fetch / gmail_read_thread  ──► Agent reasoning
   │
   └─► sync engine (sync.py)
            │
            ▼
       SQLite Index (~/.langmail/gmail.db)
            ├── messages  (metadata: subject, from, date, labels, snippet)
            ├── contacts  (name, domain, sent/received counts, relationship)
            └── thread_summaries  (LLM-generated, keyed by thread_id)
            │
            ▼
       Context Assembler (context.py)
            ├── current_thread.md     (full decoded thread)
            ├── contact_profile.md    (relationship stats)
            ├── recent_history.md     (past thread summaries)
            └── writing_style.md      (sent snippets for tone matching)
            │
            ▼
       Draft Writer  ──► gmail_create_draft  ──► Gmail Drafts folder
```

---

## Storage

All data stays on your machine at `~/.langmail/`:

| File | Contents |
|------|----------|
| `credentials.enc` | Gmail OAuth2 token, AES-256 encrypted with Fernet |
| `.key` | Fernet encryption key (chmod 600, never leave this directory) |
| `gmail.db` | SQLite index of email metadata and contacts |
| `cache/threads/` | Encrypted JSON cache of full thread bodies (TTL: 7 days) |
| `last_history_id.txt` | Gmail history ID for resumable incremental sync |
| `config.json` | User settings overrides |

**Nothing is ever sent to a third-party server.** The only external calls are to the Gmail API and the Anthropic API.

---

## Quickstart

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Anthropic API key
- Google Cloud project with Gmail API enabled

### Installation

```bash
git clone https://github.com/YOUR_USERNAME/langmail.git
cd langmail
uv sync
```

### Setup

```bash
# 1. Copy and fill in your API key
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY

# 2. Place credentials.json from Google Cloud Console in this directory
#    (OAuth2 Desktop app type, Gmail API enabled)

# 3. Run Gmail OAuth2 setup — opens browser for authorization
uv run langmail setup --credentials credentials.json
```

> **Windows users:** prefix every command with `PYTHONUTF8=1` to enable Unicode output:
> `PYTHONUTF8=1 uv run langmail check`

### First sync

```bash
# Index the last 3 months of email metadata (recommended first run)
uv run langmail sync --months 3
```

### Usage

```bash
uv run langmail check                    # Triage unread emails
uv run langmail check --urgent           # Urgent/high-priority only
uv run langmail draft <email-id>         # Draft a reply
uv run langmail tasks                    # Show extracted action items
uv run langmail watch                    # Continuous polling mode
uv run langmail sync --incremental       # Sync recent changes only
```

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `langmail setup` | OAuth2 setup wizard |
| `langmail check` | Check and triage unread emails |
| `langmail check --urgent` | Show urgent/actionable only |
| `langmail check --max N` | Limit to N emails (default: 20) |
| `langmail draft <id>` | Draft a reply with optional `--instruction` |
| `langmail tasks` | Show all extracted action items |
| `langmail watch` | Continuous polling (default: every 5 min) |
| `langmail sync` | Full email history sync |
| `langmail sync --incremental` | Sync recent changes only |
| `langmail sync --months N` | Sync last N months |
| `langmail privacy show` | Show what data is stored locally |
| `langmail privacy clear-cache` | Delete cached email bodies |
| `langmail privacy clear-all` | Delete all local data (requires re-setup) |
| `langmail config` | View all settings |
| `langmail config <key> <value>` | Set a config value |

---

## Configuration

Settings live in `~/.langmail/config.json` and can be overridden with environment variables.

| Key | Default | Description |
|-----|---------|-------------|
| `model` | `anthropic:claude-sonnet-4-20250514` | Main agent model |
| `summarization_model` | `anthropic:claude-haiku-4-5-20251001` | Fast model for sub-agents |
| `sync_depth_months` | `6` | Months of history to sync on first run |
| `cache_ttl_days` | `7` | Thread body cache expiry in days |
| `encrypt_cache` | `true` | Encrypt cached email bodies |
| `max_context_threads` | `5` | Max thread summaries for Draft Writer context |
| `max_style_samples` | `10` | Max sent snippets for tone matching |
| `auto_sync_on_check` | `true` | Run incremental sync before `check` |
| `poll_interval_minutes` | `5` | Polling interval for `watch` mode |

```bash
# Example: switch to a faster model
uv run langmail config model anthropic:claude-haiku-4-5-20251001
```

---

## Project Structure

```
langmail/
├── src/langmail/
│   ├── cli.py              # Typer CLI — all user-facing commands
│   ├── config.py           # Pydantic settings, paths, dotenv loading
│   ├── auth.py             # OAuth2 flow + Fernet token encryption
│   ├── state.py            # LangGraph agent state (TypedDicts + reducers)
│   ├── sync.py             # Email sync engine (initial + incremental)
│   ├── context.py          # Context assembler for Draft Writer
│   ├── agents/
│   │   ├── supervisor.py   # Supervisor agent + task delegation tool
│   │   ├── reader.py       # Email Reader sub-agent
│   │   ├── security.py     # Security Assessor sub-agent
│   │   ├── extractor.py    # Task Extractor sub-agent
│   │   └── writer.py       # Draft Writer sub-agent
│   ├── tools/
│   │   ├── gmail_tools.py  # Gmail API LangChain tools
│   │   └── db_tools.py     # SQLite index LangChain tools
│   └── prompts/
│       └── templates.py    # All system prompts and task prompts
├── tests/
│   ├── conftest.py         # Shared fixtures
│   ├── test_tools.py       # Gmail and DB tool tests
│   ├── test_sync.py        # Sync engine + context assembler tests
│   └── test_agents.py      # Agent creation + _process_message tests
├── docs/
│   ├── PLAN.md             # Original design and architecture notes
│   └── DATA_ARCHITECTURE.md # Storage layer design decisions
└── .github/workflows/
    └── ci.yml              # CI: lint + test on Python 3.11/3.12/3.13
```

---

## What's Left

These components are designed and partially scaffolded but not yet fully implemented:

### Incomplete / Stub

| Component | Status | Notes |
|-----------|--------|-------|
| **Thread summaries** | Stub | `save_thread_summary()` in `db_tools.py` exists but no agent currently writes summaries after reading threads. The Draft Writer reads them but they'll be empty until this is wired up. |
| **`auto_sync_on_check`** | Config only | The setting exists but `langmail check` does not yet run incremental sync automatically before fetching. Needs a call to `run_incremental_sync()` at the start of the `check` command. |
| **`langmail tasks`** | Wired to supervisor | The Task Extractor agent exists and works, but there's no persistent task storage — tasks are extracted fresh each run and not saved to the DB. |
| **Contact category classification** | Schema only | The `category` column exists in `contacts` (e.g. `work`, `personal`, `newsletter`) but nothing sets it. Could be added to the sync or reader agent. |
| **`langmail watch` auto-sync** | Polling only | Currently calls `check` in a loop but doesn't run incremental sync between polls. |

### Not Yet Built

| Feature | Notes |
|---------|-------|
| **Web UI** | Original plan included a simple web interface. CLI-only for now. |
| **Multi-account support** | Single Gmail account only. Architecture doesn't block adding more accounts but auth/config would need extending. |
| **Email send approval flow** | Currently creates drafts only. A TUI confirmation step before sending was planned but not built. |
| **Tests for auth.py** | OAuth2 flow is hard to unit test without a live Google session. No tests currently. |
| **Ruff lint in CI passing** | CI runs `ruff check` but the codebase hasn't been fully linted yet — CI may fail on first push to `main` until this is clean. |

---

## Security & Privacy

- **OAuth2** — Gmail password never stored or seen
- **Encrypted credentials** — OAuth token encrypted with AES-256 (Fernet) at `~/.langmail/credentials.enc`
- **Encrypted cache** — Email bodies cached locally with the same key; set `encrypt_cache = false` to disable
- **Draft-only** — `gmail_create_draft` is the only write tool; `messages.send` is never called
- **Minimum scopes** — `gmail.readonly` + `gmail.compose` + `gmail.modify` (for labeling only)
- **Local DB** — SQLite index at `~/.langmail/gmail.db` contains metadata only (no full bodies)
- **No telemetry** — nothing phoned home except Gmail API and Anthropic API calls

---

## Development

```bash
# Install with dev dependencies
uv sync --extra dev

# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/

# Run a specific command without installing
PYTHONUTF8=1 uv run langmail check
```

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Yes | Anthropic API key |
| `LANGSMITH_API_KEY` | No | LangSmith tracing (set `LANGSMITH_TRACING=true` to enable) |
| `LANGMAIL_MODEL` | No | Override main model |
| `LANGMAIL_SUMMARIZATION_MODEL` | No | Override summarization model |

---

## CI

GitHub Actions runs on every push to `main`/`dev` and on pull requests:

1. Lint with `ruff`
2. Run pytest on Python 3.11, 3.12, and 3.13

Set `ANTHROPIC_API_KEY` as a repository secret for CI to pass (the agent smoke tests import LangChain model initializers that validate the key at import time).

---

## Roadmap

- [x] Project scaffold and planning
- [x] Gmail OAuth2 authentication (encrypted token storage)
- [x] SQLite email index schema + contact tracking
- [x] Gmail API tools (fetch, read thread, search, draft, label)
- [x] Email Reader agent (priority + category classification)
- [x] Security Assessor agent (phishing + social engineering detection)
- [x] Task Extractor agent (action items + deadlines)
- [x] Draft Writer agent (context-aware with tone matching)
- [x] Supervisor orchestration (sub-agent delegation with isolated context)
- [x] CLI interface (check, draft, tasks, watch, sync, privacy, config)
- [x] Email sync engine (initial bulk + incremental history API)
- [x] Context assembler (thread cache, contact profile, writing style)
- [x] Tests (23 passing, covering tools, sync, context, agents)
- [x] CI (GitHub Actions, Python 3.11–3.13 matrix)
- [ ] Thread summary persistence (wire Task Extractor output to DB)
- [ ] Auto-sync on check
- [ ] Task storage (persist extracted tasks across runs)
- [ ] Contact category classification
- [ ] Web UI

---

## Contributing

Contributions welcome. See [docs/PLAN.md](docs/PLAN.md) for architecture decisions.

## License

MIT — see [LICENSE](LICENSE)
