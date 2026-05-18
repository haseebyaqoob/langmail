# Gmail Agent - Multi-Agent Email Assistant

## Vision

An open-source, multi-agent email assistant built with LangChain + LangGraph that reads your Gmail, drafts context-aware replies, extracts action items, and flags security risks. Runs locally with full user control.

---

## Architecture Overview

```
                    User (CLI / Simple Web UI)
                            |
                    [Supervisor Agent]
                     /      |       \
                    /       |        \
        [Email Reader]  [Draft Writer]  [Task Extractor]
                    \       |        /
                     \      |       /
                  [Security Assessor]
                            |
                    Gmail API (OAuth2)
```

### Agent Roles

| Agent | Role | Tools |
|-------|------|-------|
| **Supervisor** | Orchestrates workflow, decides which sub-agents to invoke, presents results to user | `task()` delegation, `read_todos()`, `write_todos()`, file tools |
| **Email Reader** | Fetches and categorizes emails (urgent, actionable, FYI, spam) | `gmail_fetch()`, `gmail_search()`, `think_tool()` |
| **Draft Writer** | Writes context-aware reply drafts based on email thread history | `gmail_read_thread()`, `write_file()`, `think_tool()` |
| **Task Extractor** | Pulls action items, deadlines, and commitments from emails | `read_file()`, `write_todos()`, `think_tool()` |
| **Security Assessor** | Scans emails for phishing, suspicious links, social engineering | `read_file()`, `think_tool()` |

---

## Context Efficiency & Isolation Strategy (Critical)

Long-running agent sessions can fail due to context growth, token waste, and repeated tool calls. The architecture must treat context as a constrained resource.

### Core Design Rules

1. **Keep message history compact**
- Conversation history stores summaries, decisions, and artifact references.
- Full payloads (threads, long analyses, extracted lists) are never kept verbatim in chat history.

2. **Store large outputs as file artifacts**
- Any output over a threshold (for example 1-2 KB) is written to local files.
- Messages include only a short summary + `artifact_id`/path.

3. **Context isolation per sub-agent**
- Each sub-agent receives only the minimum required context files.
- Sub-agents do not inherit global history by default.
- Supervisor chooses scoped files per task (`security`, `draft`, `tasks`).

4. **Tool call minimization**
- Prefer cache-first lookups before API/tool calls.
- Deduplicate repeated requests in a run (memoized fetch/read operations).
- Batch operations when possible instead of many small calls.

5. **Budgeted prompts**
- Define hard token budgets per workflow stage.
- Truncate/clip optional context first (style samples, old summaries).
- Keep mandatory context (`current_thread`, minimal contact profile, risk flags).

Model rollout guardrails:
- Validate provider/model capability readiness per operation before enabling LLM orchestration paths (tool calls, structured outputs, summarization).
- Keep provider selectors explicit in config (`model`, `summarization_model`) and enforce capability validation at startup.

6. **Summarize-on-write policy**
- When writing any large artifact, also write a compact summary record.
- Supervisor reads summary first; opens full artifact only when needed.

7. **Structured state over raw text**
- Keep machine-readable compact state (`emails`, `tasks`, `alerts`, `draft_refs`).
- Avoid repeatedly serializing large markdown in the main state.

### Artifact-First Message Pattern

```json
{
    "event": "draft_context_prepared",
    "summary": "Prepared context for john@client.com with 1 active thread, 3 history summaries, 4 style samples.",
    "artifacts": [
        "artifacts/context/current_thread_t1.md",
        "artifacts/context/recent_history_john_client_com.md"
    ],
    "token_estimate": 1850
}
```

### Retrieval Policy

- **Read summaries first** -> decide if deep read is needed.
- **Read only the selected artifact sections** (head/tail or chunked reads).
- **Never automatically inject full artifacts into the next prompt.**

### Operational Safeguards

- Per-run context budget accounting (`input_tokens_used`, `tool_calls_used`).
- Hard stop/compaction step when budget threshold is reached.
- Automatic history compaction every N turns.
- Severity-based diagnostics for maintenance commands so operators can prioritize cleanup (`metadata_trends`, `artifact_validate`).
- Severity rollup telemetry for logs (`audit-rollups`) to support dashboard-driven operations triage.
- Period-over-period rollup deltas for operational alerting and anomaly escalation workflows.
- Compact diagnostics mode for operator quick checks (`model-diagnostics --summary-only`) to reduce payload noise during daily readiness reviews.
- CLI status checks now surface startup provider-env warnings inline to keep readiness blockers visible without failing local workflows.
- Add file export hooks for diagnostics commands (`model-diagnostics --output-json`, `audit-rollups --output-json`) to support operator automation pipelines.
- Define and document trend alert thresholds for `audit-rollups` `trend_deltas` to standardize warning/critical dashboard behavior.
- Configurable limits in `config.json`:
    - `max_history_summary_tokens`
    - `max_artifacts_per_step`
    - `max_tool_calls_per_step`
    - `max_prompt_tokens_per_subagent`

### Deliverables

- [ ] Artifact store for large outputs with summary metadata
- [ ] Compact message-history layer with automatic summarization
- [ ] Context budget manager and per-step enforcement
- [ ] Supervisor policies for scoped context per sub-agent
- [ ] Tests for token budget enforcement and artifact fallback

---

## Phase 1: Foundation & Gmail Integration

### 1.1 Gmail API Setup
- Google Cloud Console project
- OAuth2 consent screen (desktop app)
- Scopes: `gmail.readonly`, `gmail.compose`, `gmail.modify`
- Token stored locally in encrypted file (never committed to git)
- Refresh token auto-renewal

### 1.2 Core Gmail Tools
```python
# Tools the agents will use
gmail_fetch(max_results, label, unread_only)  # Fetch email list
gmail_read_thread(thread_id)                  # Read full thread
gmail_search(query)                           # Search emails
gmail_draft(to, subject, body, thread_id)     # Create draft (NOT send)
gmail_label(message_id, labels)               # Add/remove labels
```

### 1.3 Security Constraints
- **Never auto-send emails** - drafts only, user confirms
- **Encrypted local cache** - full email bodies cached temporarily, encrypted at rest, auto-expire (see [DATA_ARCHITECTURE.md](DATA_ARCHITECTURE.md))
- **Token encryption** - AES-256 encrypted credentials file
- **Scoped access** - request minimum OAuth scopes needed
- **Rate limiting** - respect Gmail API quotas (250 units/sec)
- **Audit log** - every API call logged with timestamp

### 1.4 Deliverables
- [ ] Gmail API OAuth2 authentication flow
- [ ] Core Gmail tool functions with error handling
- [ ] Token storage with encryption
- [ ] Unit tests with mock Gmail data
- **Commit: `feat: gmail api integration with oauth2 and core tools`**

---

## Phase 2: Sub-Agent Design

### 2.1 Email Reader Agent
**Purpose:** Fetch and categorize unread emails

**Flow:**
```
Fetch unread emails
    -> For each email:
        -> Classify: urgent | actionable | fyi | spam | security_risk
        -> Extract: sender, subject, preview, priority score (1-5)
    -> Return sorted list to supervisor
```

**Prompt design:**
- System prompt defines classification criteria
- Uses structured output for consistent categorization
- Saves email metadata to files for other agents

### 2.2 Draft Writer Agent
**Purpose:** Generate context-aware reply drafts

**Flow:**
```
Receives: email thread + instruction (e.g. "accept meeting", "decline politely")
    -> Read full thread history
    -> Analyze tone, context, relationships
    -> Draft reply matching user's writing style
    -> Save draft to Gmail (not sent)
    -> Return draft for user review
```

**Key features:**
- Learns user's tone from sent emails (optional)
- Supports instructions like "say yes but suggest Tuesday instead"
- Always creates Gmail draft, never sends directly

### 2.3 Task Extractor Agent
**Purpose:** Pull action items from emails

**Flow:**
```
Receives: email content
    -> Identify commitments, deadlines, requests
    -> Structure as TODO items with due dates
    -> Flag items that need immediate attention
    -> Return structured task list
```

**Output format:**
```python
{
    "tasks": [
        {"content": "Reply to John about Q3 budget", "due": "2026-04-11", "priority": "high"},
        {"content": "Review attached proposal", "due": "2026-04-15", "priority": "medium"},
    ]
}
```

### 2.4 Security Assessor Agent
**Purpose:** Scan for phishing, suspicious content

**Flow:**
```
Receives: email content + metadata
    -> Check sender domain reputation
    -> Scan for phishing indicators (urgency, fake links, impersonation)
    -> Flag suspicious attachments
    -> Return risk assessment (safe | caution | danger)
```

**Checks:**
- Sender domain mismatch (display name vs actual email)
- Suspicious URLs (URL shorteners, misspelled domains)
- Social engineering patterns (fake urgency, authority claims)
- Unusual request patterns (wire transfers, credential requests)

### 2.5 Deliverables
- [ ] Email Reader with classification
- [ ] Draft Writer with thread context
- [ ] Task Extractor with structured output
- [ ] Security Assessor with risk scoring
- [ ] Tests for each sub-agent with mock data
- **Commit: `feat: implement email reader, draft writer, task extractor, and security assessor sub-agents`**

---

## Phase 3: Supervisor & Orchestration

### 3.1 Supervisor Agent
**Purpose:** Orchestrate sub-agents, present results to user

**Workflow:**
```
1. User triggers check (manual or polling)
2. Supervisor delegates to Email Reader -> get categorized emails
3. For each email:
   a. Security Assessor scans it
   b. If safe + actionable -> Task Extractor processes it
   c. If user requests -> Draft Writer creates reply
4. Supervisor compiles report
5. Present to user via CLI
```

**Parallel execution:**
- Security + Task extraction can run in parallel per email
- Multiple emails processed concurrently via parallel sub-agent calls

### 3.2 State Management
```python
class GmailAgentState(AgentState):
    emails: list[EmailSummary]           # Fetched email summaries
    tasks: list[Task]                    # Extracted action items  
    drafts: list[Draft]                  # Generated reply drafts
    security_alerts: list[SecurityAlert] # Flagged risks
    files: dict[str, str]               # Virtual filesystem
```

### 3.3 Notification Strategy
**Option A (MVP): Manual polling**
- User runs `gmail-agent check` from CLI
- Agent fetches recent unread emails

**Option B (v2): Scheduled polling**
- Background process polls every N minutes
- Desktop notification on urgent items

**Option C (v3): Gmail push notifications**
- Google Pub/Sub webhook
- Requires public endpoint (ngrok for local dev)

### 3.4 Deliverables
- [ ] Supervisor agent with delegation logic
- [ ] State schema for email workflow
- [ ] Parallel sub-agent execution
- [ ] Integration tests (full pipeline with mock data)
- **Commit: `feat: supervisor agent with orchestrated sub-agent delegation`**

---

## Phase 4: CLI Interface & User Experience

### 4.1 CLI Commands
```bash
gmail-agent setup          # OAuth2 setup wizard
gmail-agent check          # Check unread emails
gmail-agent check --urgent # Only urgent/actionable
gmail-agent draft <id>     # Draft reply to specific email
gmail-agent tasks          # Show extracted action items
gmail-agent watch          # Continuous polling mode
gmail-agent config         # Edit settings
```

### 4.2 Interactive Mode
```
$ gmail-agent check

Found 5 unread emails:

 #  Priority  From              Subject                    Security
 1  HIGH      boss@company.com  Q3 Budget Review           SAFE
 2  MEDIUM    john@client.com   Meeting reschedule         SAFE
 3  LOW       news@medium.com   Weekly digest              SAFE
 4  DANGER    support@paypa1.com Account verification      PHISHING
 5  LOW       team@slack.com    New message notification   SAFE

Actions: [r]eply  [t]asks  [s]kip  [d]etail  [q]uit
> r 1
Instruction (or Enter for auto): accept and ask for the deck beforehand

Drafting reply... Done!
Draft saved to Gmail. Review in Gmail before sending.
```

### 4.3 Deliverables
- [ ] CLI with rich terminal output
- [ ] Interactive email review flow
- [ ] Configuration management
- [ ] User setup wizard for OAuth
- **Commit: `feat: cli interface with interactive email management`**

---

## Phase 5: Polish & Open Source Readiness

### 5.1 Documentation
- README with demo GIF
- Quick start guide (< 5 minutes to first run)
- Architecture diagram
- Security model explanation
- Contributing guide

### 5.2 Testing
- Unit tests for each tool and agent
- Integration tests with mock Gmail
- Security tests (token handling, data leakage)
- CI/CD with GitHub Actions

### 5.3 Open Source Checklist
- [ ] MIT License
- [ ] `.env.example` with clear instructions
- [ ] `.gitignore` covers all secrets
- [ ] GitHub Actions CI
- [ ] Issue templates
- [ ] Contributing guidelines
- [ ] Demo video/GIF in README

### 5.4 Deliverables
- **Commit: `docs: readme, contributing guide, and setup instructions`**
- **Commit: `ci: github actions for tests and linting`**

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Agent Framework | LangChain + LangGraph |
| LLM | Anthropic Claude (default: Haiku), optional OpenAI models |
| Email API | Gmail API (google-api-python-client) |
| Auth | OAuth2 (google-auth-oauthlib) |
| CLI | Typer + Rich |
| Testing | pytest + pytest-mock |
| CI/CD | GitHub Actions |
| Package Manager | uv |

---

## Project Structure

```
gmail-agent/
├── src/
│   └── gmail_agent/
│       ├── __init__.py
│       ├── cli.py              # Typer CLI app
│       ├── config.py           # Settings management
│       ├── auth.py             # OAuth2 flow + token management
│       ├── state.py            # Agent state definitions
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── gmail_tools.py  # Gmail API tools
│       │   └── file_tools.py   # Virtual filesystem tools
│       ├── agents/
│       │   ├── __init__.py
│       │   ├── supervisor.py   # Supervisor agent
│       │   ├── reader.py       # Email reader/classifier
│       │   ├── writer.py       # Draft writer
│       │   ├── extractor.py    # Task extractor
│       │   └── security.py     # Security assessor
│       └── prompts/
│           ├── __init__.py
│           └── templates.py    # All prompt templates
├── tests/
│   ├── conftest.py
│   ├── test_auth.py
│   ├── test_tools.py
│   ├── test_agents/
│   │   ├── test_reader.py
│   │   ├── test_writer.py
│   │   ├── test_extractor.py
│   │   └── test_security.py
│   └── fixtures/
│       └── mock_emails.json
├── docs/
│   ├── PLAN.md
│   ├── ARCHITECTURE.md
│   └── SECURITY.md
├── .github/
│   └── workflows/
│       └── ci.yml
├── .env.example
├── .gitignore
├── LICENSE
├── README.md
├── pyproject.toml
└── CLAUDE.md
```

---

## Implementation Order

1. **Project scaffold + Gmail auth** -> commit
2. **SQLite schema + initial sync** -> commit
3. **Gmail tools (fetch, read, search, draft)** -> commit
4. **Email Reader agent** -> commit
5. **Security Assessor agent** -> commit
6. **Task Extractor agent** -> commit
7. **Context assembler + Draft Writer agent** -> commit
8. **Supervisor orchestration** -> commit
9. **CLI interface** -> commit
10. **Tests + CI** -> commit
11. **README + docs + demo** -> commit

Each commit is a working, testable milestone.

---

## Security Model

### Data Flow
```
Gmail API -> Agent (in-memory only) -> CLI display
                                    -> Gmail Drafts (via API)
                                    -> Summary files (no PII)
```

### What is stored locally
- OAuth tokens (encrypted)
- Configuration (settings.json)
- Logs (no email content, only metadata)

### What is stored with encryption + expiry
- Email bodies in cache (encrypted, auto-expire after 7 days)
- Contact metadata in SQLite (email, name, message counts)
- LLM-generated thread summaries (no direct quotes)

### What is NOT stored locally
- Attachments
- Passwords or credentials from email content
- Full email bodies in SQLite (only in encrypted cache)

### API Key Safety
- `.env` for local development
- `.gitignore` excludes all secrets
- Pre-commit hook warns on potential secret commits
