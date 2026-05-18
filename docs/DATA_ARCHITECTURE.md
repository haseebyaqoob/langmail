# Data Architecture: Email Storage, Context & Retrieval

## The Core Problem

The Draft Writer needs context to write good replies. But:
- A Gmail account can have 50,000+ emails
- LLM context windows are limited (~200K tokens max)
- Fetching everything from Gmail API every time is slow and rate-limited
- We need conversation history WITH a specific person to match tone and context

**Solution:** Download, index, and store email history locally in a structured way. Retrieve only what's relevant for each task.

## LLM Provider Baseline

Operational reference:
- `docs/ROLLOUT_GATE_GUIDE.md`

- Runtime model selectors use `provider:model` format.
- Default main/summarization provider is Anthropic Haiku.
- OpenAI selectors are supported as optional alternatives without changing storage architecture.
- Runtime orchestration mode is explicit: `deterministic` (default) or `langchain`.
- LangChain runtime path is optional and enabled via package extras install (`.[langchain]`).

### Provider Capability Matrix

The runtime enforces capability checks so cross-provider model swaps are safe by default.

| Provider selector | Example | Tool calls | Structured output | Summarization |
|---|---|---|---|---|
| `anthropic:claude-*` | `anthropic:claude-haiku-4-5-20251001` | yes | yes | yes |
| `openai:gpt-*` | `openai:gpt-4.1-mini` | yes | yes | yes |
| `openai:text-*` | `openai:text-legacy-001` | no | no | yes |

Runtime requirements:
- Main model requires: `tool_calls`, `structured_output`
- Summarization model requires: `summarization`

Config-level capability controls:

| Config field | Example selector | Required capabilities | Validation behavior |
|---|---|---|---|
| `model` | `anthropic:claude-haiku-4-5-20251001` | `tool_calls`, `structured_output` | App startup fails fast if requirements are not met. |
| `summarization_model` | `openai:gpt-4.1-mini` | `summarization` | App startup fails fast if summarization capability is missing. |

Recommended rollout check order:
1. Set `model` and `summarization_model` in local config using `provider:model` format.
2. Run `gmail-agent model-diagnostics --summary-only` for quick readiness confirmation.
3. If needed, run full `gmail-agent model-diagnostics` to inspect operation-level blockers.
4. Resolve any `missing_env_providers` before enabling live orchestration paths.

Operator diagnostics:
- `gmail-agent model-diagnostics` surfaces supported providers, required capabilities, model capability maps, and any compatibility issues.
- `model-diagnostics --summary-only` emits compact readiness signals (validity, rollout/env readiness, issue counts) for low-noise operator checks.
- `model-diagnostics --output-json <path>` persists full diagnostics JSON for scheduled checks and external automation.
- Diagnostics include orchestration runtime readiness (`orchestration.mode`, `orchestration.ready`, `orchestration.missing_packages`).
- Diagnostics include active supervisor engine (`orchestration.active_engine`) to confirm which runtime path is executing.
- Diagnostics include adapter activation state (`orchestration.adapter_active`) for LangChain runnable-path verification.
- Diagnostics now include operation-level rollout readiness (`operation_readiness`, `rollout_issues`, `rollout_ready`) so cross-provider swaps can be validated before enabling orchestration paths.
- `gmail-agent audit-rollups --days N` provides severity/status/operation aggregates from daily JSONL logs for external dashboard ingestion.
- `audit-rollups --output-json <path>` exports the aggregate report for offline dashboard pipelines.
- Rollup output includes period-over-period `trend_deltas` (entry volume, severity/status shifts, operation deltas) for dashboard alert triggers.
- `model-diagnostics` also includes provider environment-key readiness (`env_readiness`) to show missing API key blockers before runtime orchestration is enabled.
- Missing provider API keys now produce non-fatal startup warnings plus audit entries (`provider_env_readiness`) so local workflows remain usable while operators resolve credentials.
- If `orchestration_mode=langchain` is configured without required packages, startup remains non-fatal and emits readiness warnings plus `orchestration_runtime_readiness` audit entries.
- `check_state` now routes through an orchestration engine layer, enabling staged migration from deterministic rules to LangChain-backed supervisor execution.
- LangChain mode uses a runnable-adapter path when available, with deterministic fallback to preserve non-fatal local operation.
- `gmail-agent check` now prints startup warnings inline so operators see readiness blockers during routine inbox status checks.
- `gmail-agent check` now also prints orchestration runtime summary (mode, active engine, readiness) for live-path verification.

### Audit Rollup Trend Threshold Tuning

Use `trend_deltas` from `audit-rollups` to trigger dashboards with stable, low-noise thresholds.

Suggested baseline thresholds:

| Signal (`trend_deltas`) | Warning threshold | Critical threshold | Notes |
|---|---|---|---|
| `total_entries_delta` | `>= 20` | `>= 50` | Indicates broad activity spikes across operations. |
| `overall_level.delta_rank` | `>= 1` | `>= 2` | Captures severity-level escalation between periods. |
| `status_counts_delta.error` | `>= 3` | `>= 8` | Best early signal for workflow instability. |
| `level_counts_delta.high` | `>= 2` | `>= 5` | Useful when statuses stay mixed but risk rises. |
| `operation_total_delta.<op>` | `>= 10` | `>= 25` | Detects single-operation floods (for example `artifact_validate`). |

Tuning guidance:
1. Start with `--days 7` to reduce day-of-week volatility.
2. Raise thresholds by 25-50% for high-volume inboxes with expected burst traffic.
3. Lower `status_counts_delta.error` thresholds for sensitive production rollouts.
4. Keep warning and critical thresholds at least 2x apart to avoid alert flapping.
5. Export daily with `audit-rollups --output-json` and tune from 2-4 weeks of baseline history.

### Provider Environment Remediation Runbook

When diagnostics report missing provider keys (`missing_env_providers`), follow this operator flow:
1. Run `model-diagnostics --summary-only` to confirm readiness deltas quickly.
2. Set missing provider env keys in the current runtime environment.
3. Re-run `model-diagnostics --output-json <path>` to persist the full post-fix state.
4. Run `audit-rollups --days 7 --output-json <path>` to verify no new error trend spikes.

Recommended success criteria after remediation:
- `valid = true`
- `rollout_ready = true`
- `env_ready = true`
- `startup_warning_count = 0`

### Scheduled Rollup Automation Pattern

For operator automation, run `audit-rollups --output-json` on a schedule and evaluate `trend_deltas` against thresholds.

Reference cadence:
1. Execute every 6-12 hours with `--days 7`.
2. Persist each JSON export to an append-only archive directory.
3. Trigger warning notifications when warning thresholds are crossed.
4. Trigger paging/incident workflow when critical thresholds are crossed.

Minimum critical checks:
- `total_entries_delta >= 50`
- `overall_level.delta_rank >= 2`
- `status_counts_delta.error >= 8`

### CI/Cron Export Ingestion Pattern

To centralize diagnostics, run scheduled exports in CI or cron and publish JSON artifacts to your observability pipeline.

Recommended export set per run:
- `model-diagnostics --output-json <path>/model_diagnostics.json`
- `audit-rollups --days 7 --output-json <path>/audit_rollups.json`

Ingestion checklist:
1. Include run timestamp and commit SHA as pipeline metadata.
2. Store artifacts in append-only retention storage (14-30 days minimum).
3. Parse `trend_deltas` and readiness fields (`valid`, `rollout_ready`, `env_ready`) into alert dashboards.
4. Emit a pipeline failure or incident event on critical threshold triggers.

### Diagnostics Payload Stability Notes

For external automation, treat diagnostics exports as versioned contracts.

Versioning recommendations:
1. Add pipeline-side `schema_version` metadata when storing exported JSON artifacts.
2. Keep parsers tolerant to unknown keys and optional fields.
3. Alert only on required fields listed below.

Required fields for model diagnostics automation:
- `valid` (boolean)
- `rollout_ready` (boolean)
- `env_readiness.ready` (boolean)
- `env_readiness.missing_providers` (array)

Required fields for audit rollup automation:
- `total_entries` (integer)
- `overall_level` (string)
- `trend_deltas.total_entries_delta` (integer)
- `trend_deltas.overall_level.delta_rank` (integer)
- `trend_deltas.status_counts_delta.error` (integer, optional fallback `0`)

Retention/versioning policy for exported diagnostics:
1. Keep raw JSON artifacts for at least 14-30 days to support baseline recalibration.
2. Use timestamped filenames and avoid in-place overwrite.
3. Store sidecar metadata per export (`schema_version`, `generated_at_utc`, `git_commit_sha`, `pipeline_run_id`).
4. Use parser compatibility gates keyed by `schema_version` for downstream ingest jobs.

### Troubleshooting Noisy Trend Alerts

Common operator remediation patterns:
1. If alerts are volume-only, raise warning thresholds (25-50%) for `total_entries_delta` and `operation_total_delta.<op>` first.
2. If alerts flap around the same boundary, require two consecutive breaches before notification and use clear-threshold hysteresis.
3. If weekly backlog causes predictable spikes, keep `--days 7` and tune warning thresholds by weekday baseline rather than changing critical thresholds.
4. If one maintenance operation is noisy, isolate it with operation-specific thresholds and a separate low-priority notification channel.
5. During sensitive rollouts, lower severity/error thresholds first and avoid lowering broad volume thresholds.

Hysteresis recommendation:
- For warning-level signals, require 2 consecutive breached runs before notifying.
- For clear-state logic, require one healthy run below half-threshold before closing alert.
- Keep critical alerts immediate (no hysteresis) for `overall_level.delta_rank >= 2` or severe error spikes.

### Threshold Profile Presets

Suggested presets for operator dashboards:

| Profile | `total_entries_delta` (warn/crit) | `overall_level.delta_rank` (warn/crit) | `status_counts_delta.error` (warn/crit) | `operation_total_delta.<op>` (warn/crit) |
|---|---:|---:|---:|---:|
| Conservative | 40 / 90 | 2 / 2 | 6 / 12 | 20 / 40 |
| Balanced | 20 / 50 | 1 / 2 | 3 / 8 | 10 / 25 |
| Aggressive | 10 / 25 | 1 / 1 | 2 / 4 | 6 / 12 |

Preset usage policy:
1. Default to `Balanced` for steady-state operations.
2. Switch to `Conservative` when high-volume predictable spikes produce warning fatigue.
3. Switch to `Aggressive` only for short-lived rollout windows or incident response.

### Alert Routing Policy by Severity

Recommended routing tiers:
1. `info`: retain artifact only, no operator interruption.
2. `warning`: route to asynchronous ops channel with batching.
3. `critical`: route to immediate incident paging path.

Recommended mapping:
- `overall_level.delta_rank >= 2` -> `critical`
- `status_counts_delta.error >= critical threshold` -> `critical`
- warning-threshold breaches without severity escalation -> `warning`

### Threshold Override Governance

For temporary threshold overrides:
1. Require explicit owner, rationale, and expiry timestamp.
2. Scope overrides to a bounded window and specific signals.
3. Revert automatically at expiry and compare pre/post alert rates.
4. Keep an audit trail for rollback and post-incident review.

### CLI Profile Mapping Pattern

For operator simplicity, use one profile input (`conservative|balanced|aggressive`) and map it to threshold bundles in shell/CI wrappers before evaluating `trend_deltas`.

Recommended implementation traits:
1. Profile defaults live in one map/dictionary.
2. Commands run `audit-rollups --output-json` first, then evaluate JSON against selected thresholds.
3. Exit codes encode severity (`0=ok`, `1=warning`, `2=critical`) for automation compatibility.

### Operator Playbook (Triage -> Remediate -> Verify)

Triage:
1. Run compact model readiness diagnostics.
2. Run 7-day audit rollups and classify against active profile thresholds.

Remediate:
1. Resolve provider/env readiness blockers first.
2. Tune warning noise with hysteresis or profile shifts.
3. Escalate immediately on critical severity transitions.

Verify:
1. Re-run diagnostics and export artifacts for traceability.
2. Confirm readiness booleans and error trend stabilization.
3. Revert temporary threshold overrides at expiry.

Ownership and handoff:
1. Assign a named on-call owner for each incident window.
2. Require handoff notes containing active profile, override status, and artifact links.

### Staged Orchestration Migration Checklist

1. Install optional LangChain extras in target runtime.
2. Enable `orchestration_mode=langchain` for a narrow cohort.
3. Validate `orchestration.active_engine` and `orchestration.adapter_active` signals.
4. Compare warning/critical rates versus deterministic baseline over 7 days.
5. Roll back to deterministic mode immediately on regression criteria.

### Rollout Signoff Template (Deterministic vs LangChain)

Record before broad rollout:
1. Baseline artifacts:
    - deterministic rollup export path
    - langchain rollup export path
2. Runtime verification:
    - `orchestration_mode=langchain`
    - `orchestration_ready=true`
    - `orchestration.active_engine=langchain_supervisor_runnable`
    - `orchestration.adapter_active=true`
3. Acceptance criteria:
    - no aggregate severity-rank escalation
    - no unacceptable increase in error-count deltas
    - no unresolved startup warnings for orchestration dependencies
4. Approvals:
    - reviewer identity
    - UTC approval timestamp
    - rollback owner

### Rollout Gate Command Bundle

Reference sequence for CI/pipeline promotion checks:

```text
gmail-agent orchestration-readiness
gmail-agent model-diagnostics --summary-only
gmail-agent audit-rollups --days 7 --output-json .\exports\rollout_gate_rollups.json
gmail-agent rollout-baseline-diff .\exports\deterministic_rollups.json .\exports\rollout_gate_rollups.json --policy-tier staging
```

Local single-command equivalent:

```text
gmail-agent rollout-gate-run ops/baselines/deterministic_rollups.json --policy-tier staging --output-dir .\exports\rollout_gate
```

Artifact diagnosis helper:

```text
gmail-agent rollout-gate-diagnose .\exports\rollout_gate\rollout_gate_summary.json
```

Incident-response bundle helper:

```text
gmail-agent incident-bundle-run ops/baselines/deterministic_rollups.json --policy-tier staging --output-dir .\exports\incident_bundle
```

Incident channel update helper:

```text
gmail-agent incident-update-snippet .\exports\incident_bundle\incident_bundle_summary.json --channel ops-room
```

Runtime readiness shortcut:

```text
gmail-agent runtime-doctor --strict
```

Live Gmail preflight shortcut:

```text
gmail-agent live-ready --client-secret .\client_secret.json --strict
```

Daily live operations shortcut:

```text
gmail-agent daily-live-smoke --strict --output-json .\exports\daily_live_smoke.json
```

Operator interpretation reference:
- `docs/ROLLOUT_GATE_GUIDE.md` -> `Gate Artifact Interpretation Cheatsheet`
- `docs/ROLLOUT_GATE_GUIDE.md` -> `Policy-Tier Override Audit Template`
- `docs/ROLLOUT_GATE_GUIDE.md` -> `When to Run Gate-Only vs Incident Bundle`

Recommended failure conditions:
1. Orchestration readiness is false.
2. Startup warning count remains non-zero due to orchestration dependency gaps.
3. Rollup severity/error deltas breach approved baseline criteria.

### Environment-Tier Regression Policy Defaults

Suggested `rollout-baseline-diff` thresholds:

| Tier | max overall rank regression | max error delta regression | max total entries delta regression |
| --- | ---: | ---: | ---: |
| dev | 1 | 3 | 50 |
| staging | 0 | 1 | 30 |
| prod | 0 | 0 | 20 |

Aligned audit threshold profiles:
- `dev` -> `conservative`
- `staging` -> `balanced`
- `prod` -> `aggressive`

Guidance:
1. Allow wider variance in `dev` for rapid iteration.
2. Keep `staging` close to production but tolerate minor noise.
3. Use strict zero-regression defaults in `prod` for severity and error deltas.

Operator command:

```text
gmail-agent rollout-policy-presets
```

### CI Promotion Gate Workflow

Reference workflow: `.github/workflows/rollout-gate.yml`

Workflow responsibilities:
1. Generate candidate rollup JSON from current runtime.
2. Capture orchestration readiness signals.
3. Enforce baseline regression policy using `rollout-baseline-diff --policy-tier`.
4. Upload gate artifacts for auditability (candidate rollup, readiness JSON, diff JSON).

Baseline source:
- `ops/baselines/deterministic_rollups.json` (seeded placeholder; replace with environment baseline export).

Scheduled baseline maintenance:
- Weekly cron baseline refresh job exports deterministic rollup snapshot as a workflow artifact.
- Manual workflow dispatch supports baseline-only refresh with `refresh_baseline=true`.

Baseline handoff process:
1. Download `deterministic-rollup-baseline` artifact from workflow run.
2. Verify freshness and window settings.
3. Diff artifact against repository baseline with `rollout-baseline-diff`.
4. Promote accepted artifact into `ops/baselines/deterministic_rollups.json` via PR.
5. Attach rollout-gate JSON artifacts as review evidence.

Policy tier resolution in workflow:
- `policy_tier=auto` maps `pull_request` runs to `staging`.
- `main/master` refs map to `prod`.
- Other refs map to `dev` unless explicitly overridden.

### Rollout Gate Failure Triage

Failure investigation order:
1. `rollout_gate_metadata.json`: confirm resolved tier and baseline path.
2. `orchestration_readiness.json`: check orchestration readiness/runtime path.
3. `rollout_baseline_diff.json`: identify breached checks and regression values.
4. `rollout_gate_rollups.json`: inspect underlying trend deltas.

Common remediation paths:
1. Runtime readiness issues -> remediate missing packages/keys or fallback mode.
2. Regression threshold breach -> reduce rollout scope or revert to deterministic mode.
3. Baseline drift -> refresh deterministic baseline artifact and re-evaluate.

### Deterministic Reversion Drill

Recommended periodic drill steps:
1. Force `orchestration_mode=deterministic`.
2. Confirm orchestration readiness reflects deterministic engine.
3. Execute inbox processing smoke command (`check`) for runtime validation.
4. Export post-reversion diagnostics artifacts for audit trail.
5. Record owner/time/reason in incident log or change ticket.

Single-command rollback drill:

```text
gmail-agent rollback-drill-run --output-dir .\exports\rollback_drill
```

### False-Positive Guidance

When rollout gates fail without true regressions:
1. Confirm whether breach is volume-only (`total_entries_delta`) versus severity/error drift.
2. Verify baseline freshness and matching window before changing policy thresholds.
3. Prefer staging/dev tolerance changes before touching production defaults.
4. Require repeated failures across runs before accepting policy changes.

### Policy Override Approval Controls

Before changing tier defaults or threshold overrides:
1. Require multi-run evidence (minimum three runs).
2. Record false-positive versus false-negative tradeoff.
3. Define rollback trigger + owner.
4. Set expiry/review date for temporary policies.

### Policy-Tier Selection Strategy

Recommended by stage:
1. Feature branch validation -> `dev`
2. Pull request checks -> `staging`
3. Mainline release gate -> `prod`

Decision rule:
- keep strict `prod` defaults unless repeated evidence justifies policy review.

Baseline refresh command:

```text
gmail-agent baseline-capture --output-json ops/baselines/deterministic_rollups.json --overwrite
```

### `trend_deltas` Field Glossary

- `total_entries_delta`: Net event-count delta between current and previous periods.
- `overall_level.delta_rank`: Aggregate severity-rank shift; positive values mean escalation.
- `level_counts_delta`: Per-level count shifts to localize severity movement.
- `status_counts_delta.error`: Error-count shift for reliability regression detection.
- `operation_total_delta`: Per-operation volume shifts for hotspot detection.

### Critical Alert Checklist

When critical thresholds are breached:
1. Capture diagnostics exports for the same time window.
2. Verify provider/model/env readiness did not regress.
3. Identify whether escalation is broad or operation-localized.
4. Trigger incident routing and assign owner.
5. Document temporary overrides with explicit expiry and rollback path.

---

## Storage Design

### Local Data Directory

```
~/.gmail-agent/                          # All local data lives here
├── config.json                          # User settings
├── credentials.enc                      # Encrypted OAuth tokens
├── gmail.db                             # SQLite database (metadata + search index)
├── logs/
│   └── 2026-04-09.log                   # Daily audit logs (no email content)
└── cache/
    └── threads/
        ├── 18f3a2b1c4d5e6f7.json        # Cached thread content (encrypted)
        └── 19a4b3c2d5e6f7g8.json
```

### Why SQLite + Cached Thread Files

| Approach | Pros | Cons |
|----------|------|------|
| All in SQLite | Single file, SQL queries | Large email bodies bloat DB |
| All in JSON files | Simple | No indexing, slow search |
| **SQLite metadata + thread cache files** | Fast queries on metadata, bodies only loaded when needed | Two systems to manage |

We use the hybrid: SQLite for fast lookup, thread files for full content when needed.

---

## SQLite Schema

```sql
-- Every email message
CREATE TABLE messages (
    message_id    TEXT PRIMARY KEY,      -- Gmail message ID
    thread_id     TEXT NOT NULL,         -- Gmail thread ID
    subject       TEXT,
    snippet       TEXT,                  -- First ~200 chars
    from_email    TEXT NOT NULL,
    from_name     TEXT,
    to_emails     TEXT,                  -- JSON array
    cc_emails     TEXT,                  -- JSON array
    date          DATETIME NOT NULL,
    labels        TEXT,                  -- JSON array ["INBOX", "UNREAD"]
    is_sent       BOOLEAN DEFAULT 0,    -- Did the user send this?
    is_read       BOOLEAN DEFAULT 1,
    has_attachment BOOLEAN DEFAULT 0,
    size_bytes    INTEGER DEFAULT 0
);

-- Contact relationship tracking
CREATE TABLE contacts (
    email         TEXT PRIMARY KEY,
    name          TEXT,
    domain        TEXT,                  -- extracted from email
    first_seen    DATETIME,
    last_seen     DATETIME,
    message_count INTEGER DEFAULT 0,
    sent_count    INTEGER DEFAULT 0,    -- how many times user emailed them
    received_count INTEGER DEFAULT 0,   -- how many times they emailed user
    relationship  TEXT,                  -- "frequent", "occasional", "rare"
    category      TEXT                   -- "work", "personal", "newsletter", "unknown"
);

-- Thread summaries (LLM-generated, stored to avoid re-summarizing)
CREATE TABLE thread_summaries (
    thread_id     TEXT PRIMARY KEY,
    contact_email TEXT,
    subject       TEXT,
    message_count INTEGER,
    summary       TEXT,                 -- LLM-generated summary
    last_updated  DATETIME,
    status        TEXT                  -- "active", "resolved", "waiting_reply"
);

-- Indexes for fast retrieval
CREATE INDEX idx_messages_thread ON messages(thread_id);
CREATE INDEX idx_messages_from ON messages(from_email);
CREATE INDEX idx_messages_date ON messages(date DESC);
CREATE INDEX idx_contacts_domain ON contacts(domain);
CREATE INDEX idx_contacts_last_seen ON contacts(last_seen DESC);
```

---

## Download Strategy

### Initial Sync (First Run)

```
gmail-agent sync
```

**Flow:**
```
1. Fetch message list (metadata only) via Gmail API
   - GET /messages?maxResults=500&pageToken=...
   - Paginate through all messages (or last N months)

2. For each batch of messages:
   - Bulk fetch metadata (subject, from, to, date, labels, snippet)
   - Insert into SQLite messages table
   - Build/update contacts table

3. Do NOT download full bodies yet
   - Bodies are fetched on-demand when an agent needs them
   - Saves storage, respects privacy

4. Generate contact stats
   - Count messages per contact
   - Categorize relationships (frequent/occasional/rare)
   - Detect domains (work, personal, newsletter)
```

**Rate limiting:**
- Gmail API allows ~250 quota units/sec
- messages.list = 5 units, messages.get = 5 units
- Batch requests: up to 100 per batch call
- ~10,000 messages indexed per minute safely

### Incremental Sync (Subsequent Runs)

```
gmail-agent sync --incremental
```

**Flow:**
```
1. Use Gmail API history endpoint
   - GET /history?startHistoryId=<last_known_id>
   - Returns only changes since last sync

2. Process changes:
   - New messages -> insert into messages table
   - Label changes -> update messages table
   - Deletions -> mark as deleted

3. Update contact stats for affected contacts
```

This runs automatically on every `gmail-agent check`.

### On-Demand Thread Fetch

When an agent needs full email content (e.g., Draft Writer needs thread history):

```
1. Check cache: ~/.gmail-agent/cache/threads/<thread_id>.json
2. If cached and fresh (< 1 hour old) -> use cache
3. If not cached or stale:
   a. Fetch full thread via Gmail API
   b. Parse MIME, extract plain text body
   c. Save to cache file (encrypted)
   d. Return to agent
```

---

## Context Assembly: How Agents Get What They Need

### The Context Problem

When replying to an email from `john@client.com`, the Draft Writer needs:
- The current thread (the email being replied to)
- Recent threads with John (relationship context)
- User's writing style with John (tone matching)
- Any pending tasks related to John (commitments)

All of this must fit in one LLM context window.

### Context Assembly Pipeline

```
User says: "Reply to email #3 from John, accept the meeting"
                    |
            [Context Assembler]
                    |
    ┌───────────────┼───────────────────┐
    |               |                   |
[Current Thread] [Contact History] [User Style]
    |               |                   |
    v               v                   v
Full thread      Last 5 threads     Last 10 sent
with John        with John          emails to John
(from cache)     (summaries only)   (snippets only)
    |               |                   |
    └───────┬───────┘                   |
            |                           |
     [Assembled Context File]           |
            |                           |
     ┌──────┴───────┐                   |
     |              |                   |
 [Draft Writer Agent]──────────────────┘
     |
 [Draft saved to Gmail]
```

### Context Assembly Tool

```python
def assemble_context(
    thread_id: str,
    contact_email: str,
    db: sqlite3.Connection,
) -> dict[str, str]:
    """Build context files for the Draft Writer.

    Returns dict of virtual files to inject into agent state.
    """
    files = {}

    # 1. Current thread (full content)
    thread = fetch_thread(thread_id)  # from cache or Gmail API
    files["current_thread.md"] = format_thread(thread)

    # 2. Contact profile
    contact = db.execute(
        "SELECT * FROM contacts WHERE email = ?", (contact_email,)
    ).fetchone()
    files["contact_profile.md"] = format_contact(contact)

    # 3. Recent thread summaries with this contact (last 5)
    summaries = db.execute("""
        SELECT subject, summary, last_updated, status
        FROM thread_summaries
        WHERE contact_email = ?
        ORDER BY last_updated DESC LIMIT 5
    """, (contact_email,)).fetchall()
    files["recent_history.md"] = format_summaries(summaries)

    # 4. User's writing style samples (last 10 sent to this contact)
    sent = db.execute("""
        SELECT snippet, date FROM messages
        WHERE from_email = ? AND to_emails LIKE ?
        ORDER BY date DESC LIMIT 10
    """, (user_email, f"%{contact_email}%")).fetchall()
    files["writing_style.md"] = format_style_samples(sent)

    return files
```

### What Each Context File Contains

**`current_thread.md`** — full conversation being replied to
```markdown
# Thread: Q3 Budget Review

## Message 1 — John Smith <john@client.com> — Apr 8, 2026 2:30 PM
Hi, can we schedule a meeting to review the Q3 budget?
I've attached the preliminary numbers. Let me know your availability.

## Message 2 — You — Apr 8, 2026 4:15 PM
Thanks John, I'll take a look at the numbers tonight.

## Message 3 — John Smith <john@client.com> — Apr 9, 2026 9:00 AM
Great! How about Thursday at 2pm?
```

**`contact_profile.md`** — who you're talking to
```markdown
# Contact: John Smith
- Email: john@client.com
- Domain: client.com
- Category: work
- Relationship: frequent (47 emails total)
- You sent: 22 | They sent: 25
- First contact: Jan 2025
- Last contact: Apr 9, 2026
- Common topics: budgets, quarterly reviews, project updates
```

**`recent_history.md`** — recent threads (summaries, not full content)
```markdown
# Recent Threads with John Smith

1. **Q3 Budget Review** (active, Apr 8-9)
   - Discussing meeting to review Q3 budget numbers
   
2. **Project Alpha Status** (resolved, Mar 28)
   - Agreed on Phase 2 timeline, deliverables confirmed

3. **Team Dinner** (resolved, Mar 15)
   - Planned team dinner at Italian place, went well
```

**`writing_style.md`** — how you write to this person
```markdown
# Your Writing Style with John Smith

## Sample 1 (Apr 8):
"Thanks John, I'll take a look at the numbers tonight."

## Sample 2 (Mar 28):
"Looks good to me. Let's lock in the March 30 deadline for Phase 2."

## Sample 3 (Mar 15):
"How about that Italian place on 5th? I can book for 7pm."

## Observed patterns:
- Tone: casual-professional, first name basis
- Length: short and direct
- Style: no formal greetings, sometimes skips "Hi"
```

---

## Token Budget Per Agent Call

| Context File | Estimated Tokens | When Loaded |
|-------------|-----------------|-------------|
| `current_thread.md` | 500-3,000 | Always |
| `contact_profile.md` | 100-200 | Always |
| `recent_history.md` | 300-800 | Always |
| `writing_style.md` | 400-1,000 | Draft Writer only |
| System prompt | 500-1,000 | Always |
| **Total per call** | **~2,000-6,000** | Well within limits |

This leaves plenty of room in the context window for the LLM to reason and generate.

---

## Complete Tool List

### Gmail API Tools (agents use these)

| Tool | Purpose | Used By |
|------|---------|---------|
| `gmail_fetch(max_results, label, unread_only)` | Fetch email list from Gmail | Email Reader |
| `gmail_read_thread(thread_id)` | Fetch full thread content | Draft Writer, Context Assembler |
| `gmail_search(query)` | Search emails by query | Email Reader, Supervisor |
| `gmail_create_draft(to, subject, body, thread_id)` | Create draft reply in Gmail | Draft Writer |
| `gmail_label(message_id, add_labels, remove_labels)` | Modify email labels | Supervisor |

### Local Data Tools (agents use these)

| Tool | Purpose | Used By |
|------|---------|---------|
| `db_lookup_contact(email)` | Get contact profile from SQLite | Context Assembler |
| `db_search_contacts(query)` | Search contacts by name/domain | Supervisor |
| `db_get_thread_summaries(contact_email, limit)` | Get recent thread summaries | Context Assembler |
| `db_get_sent_snippets(contact_email, limit)` | Get user's sent email snippets | Context Assembler |
| `db_get_email_stats()` | Get overall inbox stats | Supervisor |

### Context Tools (internal, not exposed to LLM)

| Tool | Purpose | Used By |
|------|---------|---------|
| `assemble_context(thread_id, contact_email)` | Build context files for agents | Supervisor (before delegating) |
| `summarize_thread(thread_id)` | Generate/cache thread summary | Background sync |
| `classify_contact(email)` | Auto-categorize contact | Background sync |

### Virtual File System Tools (from deep-agents course)

| Tool | Purpose | Used By |
|------|---------|---------|
| `ls()` | List files in agent state | All agents |
| `read_file(path)` | Read file content | All agents |
| `write_file(path, content)` | Write file to state | All agents |
| `write_todos(todos)` | Update task list | Task Extractor, Supervisor |
| `read_todos()` | Read current tasks | Supervisor |

### Agent Delegation Tools

| Tool | Purpose | Used By |
|------|---------|---------|
| `task(description, subagent_type)` | Delegate to sub-agent | Supervisor |
| `think_tool(reflection)` | Strategic reflection | All agents |

---

## Data Flow Diagram

```
                         GMAIL API
                            |
                     [Initial Sync]
                            |
              ┌─────────────┼─────────────┐
              v             v             v
         messages      contacts     thread_summaries
         (SQLite)      (SQLite)       (SQLite)
              |             |             |
              └──────┬──────┘             |
                     |                    |
              [On-Demand Fetch]           |
                     |                    |
                     v                    |
              cache/threads/              |
              (encrypted JSON)            |
                     |                    |
                     v                    v
              [Context Assembler] --------┘
                     |
         ┌───────────┼───────────┐
         v           v           v
   current_     contact_    recent_
   thread.md   profile.md  history.md
         |           |           |
         └─────┬─────┘           |
               v                 |
        [Agent State]            |
         (files dict)  <---------┘
               |
         ┌─────┼──────┬──────┐
         v     v      v      v
      Reader Writer Extract Security
```

---

## Privacy Tiers

Since we ARE storing data locally now, we need clear privacy controls:

### Tier 1: Always Stored (metadata only)
- Message IDs, thread IDs
- From/to email addresses
- Subject lines
- Dates, labels
- Snippet (first ~200 chars)

### Tier 2: Cached On-Demand (full content, encrypted)
- Full email bodies (fetched when agent needs them)
- Cached in encrypted files
- Auto-expire after configurable period (default: 7 days)
- User can clear cache: `gmail-agent cache clear`

### Tier 3: Generated (LLM summaries)
- Thread summaries (stored in SQLite)
- Contact categories
- These contain NO direct quotes, only LLM-generated summaries

### Tier 4: Never Stored
- Attachments (referenced but not downloaded)
- Passwords or sensitive credentials mentioned in emails
- Full email bodies are never in SQLite — only in encrypted cache

### User Controls
```bash
gmail-agent privacy show              # Show what's stored
gmail-agent privacy clear-cache       # Delete cached thread content
gmail-agent privacy clear-all         # Delete everything, start fresh
gmail-agent privacy export            # Export all stored data (GDPR-style)
gmail-agent config set cache-ttl 3d   # Cache expires after 3 days
gmail-agent config set sync-depth 6m  # Only sync last 6 months
```

---

## Configuration Defaults

```json
{
    "sync_depth_months": 6,
    "cache_ttl_days": 7,
    "max_context_threads": 5,
    "max_style_samples": 10,
    "auto_sync_on_check": true,
    "encrypt_cache": true,
    "log_level": "info",
    "model": "anthropic:claude-sonnet-4-20250514",
    "summarization_model": "anthropic:claude-haiku-4-5-20251001"
}
```
