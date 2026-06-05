# duvo-signal-loop — Architecture reference

The deep map. Use it to go deep on one area or to verify your reading of the code.
The code is the source of truth; if this drifts, trust the code and flag the drift.

The runtime lives under the `duvo/` package; `main.py` at the repo root is a thin
shim that calls `duvo.orchestrator.main` (so `uv run python main.py` and
`python -m duvo.orchestrator` both work).

## Table of contents

1. What the project is
2. The core idea: one runtime, many agents
3. The per-account pipeline (orchestration)
4. The three agent roles
5. The pluggable write-back layer
6. The data contract
7. Cross-cutting infrastructure
8. Durable run-state store (SQLite)
9. Testing philosophy
10. Bounded agency — the throughline
11. Known limits & roadmap

---

## 1. What the project is

A multi-agent GTM (go-to-market) pipeline for **Duvo** — a company selling AI agents
that automate retail/CPG back-office work (reconciliation, PO/invoice matching). It
takes a CSV of target retail/CPG accounts and, per account, runs a **scouts → analyst
→ router** chain of tool-using LLM agents that scout intent signals, score ICP
(Ideal Customer Profile) fit, and route the account into a sales stack (CRM + Slack +
outreach), then emits an HTML audit report. It is built as a polished demo/portfolio
project — note the sourced `.env.example`, the honest "where it breaks" section, and
the fully-mocked offline test suite.

## 2. The core idea: one runtime, many agents

`agent_core.run_agent()` is the single primitive. Every agent is a different
`(system, tools, impls)` triple over the same provider-neutral loop:

1. Build an `LLMRequest` with model, system prompt, neutral messages, tools, and token cap.
2. Resolve the configured provider through `duvo/llm/registry.py` and call
   `LLMProvider.complete()`.
3. Append the normalized assistant `Message`; inspect `response.tool_calls`. None → return.
4. For each tool call: look up impl, call it, **await if `inspect.isawaitable`** (this is
   what lets sync and async tools coexist); catch exceptions → `"tool error: …"`;
   unknown tool → `"unknown tool: …"`.
5. Feed each result back as a neutral tool `Message`.
6. Any called tool in `final_tools` → return. Else loop to `max_turns`.

Design choices to notice:
- **Sync/async polymorphism** via `inspect.isawaitable` — and the explicit falsy edge
  case (returning `None`/`0` must NOT be awaited; tested).
- **Errors never crash the loop** — a raising tool becomes a recoverable tool_result.
- **`max_turns`** is the infinite-loop backstop (default 8; scouts `MAX_SCOUT_SEARCHES+2`,
  analyst `MAX_ANALYST_SEARCHES+3`, router 6).
- **`log` list** threads through to record every tool call as `"name(args)"` for the report.
- **One LLM boundary** — `agent_core` and agents only speak the neutral types in
  `duvo/llm/base.py`; `duvo/llm/litellm_provider.py` is the default provider and the
  only module that knows LiteLLM/OpenAI-style wire format.

## 3. The per-account pipeline (orchestration)

`orchestrator.run()` (`duvo/orchestrator.py:193`):
- Loads `companies.csv` → `list[Company]`; `--limit` truncates.
- Mints (or reuses, via `--resume <run_id>`) the `run_id`; computes the UTC `run_date`.
- On real runs, applies the skip set: `--resume` skips domains already `done` in that
  run_id, `--skip-done-today` skips domains already `done` for today's `run_date`
  (`done_domains_for_run` / `done_domains_for_date`).
- `asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)` (default 5; overridable via `--concurrency`).
- On real runs (`persist = not dry_run`), `store.start_run(...)` opens the `runs` row.
- `asyncio.gather` over all accounts, each in `_process_account`.
- `finally:` always drains `http_client.aclose()` + `exa_tool.aclose()` and `tracing.flush()`,
  and on real runs `store.finish_run(...)` records succeeded/failed counts — pools closed,
  traces flushed, and the run closed even if every account fails.
- Filters `None`, renders a run-scoped report.

`_process_account` (`duvo/orchestrator.py:99`) — triple isolation:
- `async with semaphore` → bounded concurrency.
- `async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS)` (300s) → one hung account can't
  stall the batch.
- `try/except Exception: return None` → a bad account logs, marks the row `failed` (real
  runs), and yields `None`; never cancels siblings or propagates. (Because it never
  raises, a bare `gather` is safe — noted in a comment.)
- Chain: `scout_all` → `run_analyst` → build `RunResult` → `run_router`; a shared
  `agent_log` threads through all three and is attached to the result.
- On real runs, `_persist_success` (`duvo/orchestrator.py:43`) marks the account `done`
  with its score/tier/signals and writes one `writeback_events` row per channel
  (crm/slack/outreach) carrying a **score/tier diff** vs the domain's last `done` run
  (`changed`, `prev_score`, `prev_tier`). Serialization failures never abort a successful
  account. Dry-runs persist nothing.

## 4. The three agent roles

### Scouts (`duvo/agents/scouts/scouts.py`) — 4 concurrent signal hunters
- Prompt copy lives with the agent at `duvo/agents/scouts/prompts/scout.md`; the runner
  loads it through `duvo/agents/scouts/prompts/__init__.py`.
- Four `BEATS`: `erp_migration`, `hiring`, `ma_leadership`, `pain` — matching
  `Signal.signal_type` `Literal`s.
- `scout_all` fans out via `asyncio.gather`; each wrapped in `run_scout_safe` so one
  failing beat returns `[]` instead of killing the other three (per-beat isolation,
  below the per-account layer).
- Each scout = `run_agent` with `exa_search` (async) + `submit_signals` (sync, final).
  Prompt: search iteratively, max `MAX_SCOUT_SEARCHES` (4), **never invent**, empty
  results fine, discard off-beat/off-company.
- Results re-validated into `Signal`s with `signal_type` forced to the beat's key — the
  model never picks the type.

### Analyst (`duvo/agents/analyst/analyst.py`) — validate, score, draft + deterministic guards
- Prompt copy lives with the agent at `duvo/agents/analyst/prompts/analyst.md`; the runner
  loads it through `duvo/agents/analyst/prompts/__init__.py`.
- `run_agent` with `exa_search` (verify doubtful signals, max `MAX_ANALYST_SEARCHES`=2)
  + `record_assessment` (final). Given the full `ICP_DEFINITION` and the signals as JSON.
- **Two-layer defense** (assumes the LLM misbehaves):
  - **Layer A — coercion before model construction:** no assessment → `_conservative_default`
    (score 3, Tier 3, needs research); non-numeric score → 3; out-of-range → **clamped
    [1,10]**; invalid tier/confidence → safe defaults; malformed outreach → empty draft;
    any `ICPScore` failure → conservative default.
  - **Layer B — `apply_guards()`** (`duvo/agents/analyst/guards.py:apply_guards`), pure/sync/deterministic,
    rules in order: (1) no dated signals → `confidence=low`, `needs_human_research=True`;
    (2) `confidence==low` & `score>=7` → cap to 6; (3) tier derived from score —
    `>=8 & not needs_human_research`→Tier 1, `>=5`→Tier 2, else Tier 3 (**overrides the
    model's tier**); (4) `needs_human_research` & Tier 1 → downgrade to Tier 2.
  - Net effect: a hallucinated high score can never *alone* yield a confident Tier 1.

### Router (`duvo/agents/router/router.py`) — the never-send safety layer
- Prompt copy lives with the agent at `duvo/agents/router/prompts/router.md`; the runner
  loads it through `duvo/agents/router/prompts/__init__.py`.
- `run_agent` with four no-input tools: `crm_upsert`, `slack_alert`, `outreach_queue`,
  `finish`.
- `confident_t1 = (tier == "Tier 1") and (not needs_human_research)` — computed once,
  deterministically, outside the model.
- **Tools self-guard:** `crm_upsert` always allowed; `slack_alert`/`outreach_queue` put
  the safety check *first*, **before the lazy `from duvo.writeback import …` and any await** —
  a non-confident-Tier-1 returns `"refused: not a confident Tier 1 (safety guard)"` and
  the send module is never even imported. Proven by `test_guard_fires_before_lazy_import`
  (deletes the modules from `sys.modules`, asserts they never reappear).
- **Nothing auto-sends:** outreach only *queues* into a review list (Brevo) / *paused*
  campaign (lemlist) for a human rep. `dry_run` returns `[dry-run] …` strings; agents
  still really decide.

## 5. The pluggable write-back layer (`duvo/writeback/`)

Two dispatchers, two adapters each, swap by one env var, all sharing one HTTP pool:

| Interface | Default | Alt | Switch |
|---|---|---|---|
| `crm.upsert_account(score)` | Attio | HubSpot | `CRM_PROVIDER` |
| `outreach.queue_lead(score, email)` | Brevo | lemlist | `OUTREACH_PROVIDER` |
| `slack.alert_tier1(score)` | Slack webhook | — | — |

- Dispatchers read the provider **at call time** (so tests monkeypatch freely); unknown
  provider → warn + fall back to default; adapters imported lazily.
- Adapter robustness + CRM dedup: **both CRM adapters upsert by domain** — **Attio**
  queries companies by domain and PATCHes the match or creates one (`_upsert_company`),
  with `{name, domains}` → `name`-only as the create-payload fallback, then attaches an
  evidence note; **HubSpot** does the same search-by-domain upsert (PATCH or POST) plus an
  idempotent custom-property ensure. So re-runs no longer create duplicate records.
  **Brevo** tries rich attributes then minimal `email + listIds`; **lemlist** posts to a
  paused campaign with `deduplicate=true` and validates the API key before the campaign id.
- Shared client (`duvo/infra/http_client.py`): lazy module-level `httpx.AsyncClient` with
  uniform `HTTP_TIMEOUT_SECONDS`, drained by `aclose()` in the orchestrator's `finally`.

## 6. The data contract (`duvo/models.py`)

Five Pydantic models are the spine every agent agrees on:
- `Company` (CSV input).
- `Signal` — `signal_type` ∈ the 4 beat `Literal`s; `source_url` mandatory (sourcing
  enforced by schema).
- `OutreachDraft` — persona/subject/first_line/body.
- `ICPScore` — `score = Field(ge=1, le=10)` (the constraint the analyst clamping
  protects), tier/confidence `Literal`s, embeds `OutreachDraft`.
- `RunResult` — score + signals + three write-back status strings (default `"skipped"`)
  + `agent_log`.

## 7. Cross-cutting infrastructure

- **`duvo/config.py`** — centralized env reads; `require(name, value)` raises a clear error
  for credentials that must exist before a live network call (Exa; the selected LLM
  provider key is validated by its provider) but leaves write-back keys lazy so
  `--dry-run` runs without a full `.env`. `LLM_PROVIDER` selects the provider registry
  entry and `LLM_MODEL` selects the model string; all concurrency/timeout knobs are
  env-overridable with sane defaults.
- **`duvo/infra/logging_setup.py`** — single `duvo.*` logger tree; idempotent `configure_logging`
  (no duplicate handlers); `propagate=False`; level resolvable from string with INFO
  fallback; secrets never logged.
- **`duvo/infra/retry.py`** — `with_retries()`: transient-failure retry (408/425/429/5xx) with
  capped exponential backoff + jitter (`HTTP_MAX_RETRIES`), wrapped around every write-back POST.
- **`duvo/infra/tracing.py`** — the **one tracing boundary**: the only module that imports
  `langfuse`, lazily. OFF unless `LANGFUSE_ENABLED=true` (+ keys), so `--dry-run` and the offline
  tests never import it or hit the network. Agents instrument through `tracing.span()` /
  `tracing.trace_context()`; the orchestrator emits one trace per run (batch span wraps every
  account-run) and `tracing.flush()` in its `finally`.
- **`duvo/reporting/reporter.py`** — the only deliberately **sync** module (pure CPU + file
  I/O, safe from async); Jinja2 autoescape; sorts accounts by score desc; writes a run-scoped
  `output/run_reports/{run_date}_{run_id}-run-report.html` (falling back to `output/run-report.html`
  when no run id) and renders `batch_run_id` in the header so a report links back to its trace.
  The template renders per account: tier badge, why-fit,
  linked/dated signals, drafted outreach, the agent tool-call log, and the three
  write-back statuses.

## 8. Durable run-state store (SQLite)

`duvo/store/` — added with the dedup/DB work, structured one-file-per-concern like
`infra/` and `writeback/`. It makes runs observable and re-runs safe. Path is
`config.DUVO_DB_PATH` (default `state/duvo.db`, outside the ephemeral `output/`).

- **`db.py`** — the async seam over stdlib `sqlite3` (sync), so every op is offloaded via
  `asyncio.to_thread` (the house rule for sync-only libs); the event loop never blocks.
  Opens with WAL + `synchronous=NORMAL` + `busy_timeout=5000` so the concurrently
  `gather`-ed account coroutines can write without "database is locked". Schema is applied
  on first connect from `schema.sql` (idempotent `CREATE TABLE IF NOT EXISTS`). Every
  public helper (`execute` / `query_one` / `query_all`) wraps the call in `_safe`, which
  **logs and returns a default on any error** — exactly like `infra.tracing`, so a
  persistence failure can never abort the pipeline.
- **Three tables** (`schema.sql`): `runs` (one row per batch — id, date, model, env,
  concurrency, counts, status), `account_runs` (one row per `(run_id, domain)` — status
  `pending|running|done|failed` + score/tier/confidence/signals/error), and
  `writeback_events` (one row per `(run_id, domain, channel)` — provider, derived action,
  `changed` flag, `prev_score`/`prev_tier`, idempotency key `domain:channel`).
- **`runs.py`** — `start_run` (idempotent: resume keeps the original row) / `finish_run`.
- **`account_runs.py`** — `upsert_account_run` / `mark_status`; the read helpers that drive
  re-run logic: `last_done_for_domain` (most recent `done` row for a domain from any *other*
  run — feeds the diff), `done_domains_for_run` (`--resume`), `done_domains_for_date`
  (`--skip-done-today`).
- **`events.py`** — `record_event` (upsert on `(run_id, domain, channel)`) + `derive_action`
  (status string → coarse ledger verb: `refused` / `failed` / `skipped`, else the channel's
  success verb crm→`upserted`, slack→`alerted`, outreach→`queued`).
- **Wired in the orchestrator** (`_persist_success`, real runs only — dry-runs never persist).
  The score diff is stored in `writeback_events` for future score-change alerting; it is not
  yet surfaced in the HTML report.

## 9. Testing philosophy

383 tests, fully offline — the LLM provider, Exa, and all HTTP mocked; no keys, no network.
`asyncio_mode = auto`. `conftest.py` supplies `make_fake_async_client`, `_fake_response`,
`make_score`. The agent-testing trick: **patch `run_agent` itself** with a fake that calls
a chosen tool sequence — so router/analyst logic is tested deterministically without
simulating model turns. The safety guards get the most rigor (the `sys.modules`
import-order proof is the standout). `test_agent_core.py` covers the loop edge cases
(final tools, max turns, tool errors, unknown tools, falsy-return non-await). The durable
store is covered too: the three tables, `--resume` / `--skip-done-today` skip logic, the
CRM dedup adapters, and the write-back diff.

## 10. Bounded agency — the throughline

The whole design refuses to trust the LLM with irreversible actions. Three deterministic
gates, each outside the model:
1. **Scouts** can't invent — prompt + `source_url`-required schema; undated signals
   penalized downstream.
2. **`apply_guards()`** — deterministic scoring/tiering that overrides the model and caps
   hallucinated confidence.
3. **Router tool guards** — `confident_t1` in code; send-path modules don't even import
   for non-qualifying accounts; nothing auto-sends (review list / paused campaign only).

## 11. Known limits & roadmap

Author-flagged limits: Exa noise/staleness on big brands; agent-loop latency/variance
(bounded by turn caps); no real person-level email (uses a test email + plus-addressing).
**Re-runs are now safe** — both CRM adapters upsert-on-domain (no duplicates) and the
SQLite store tracks every run, so `--resume <run_id>` recovers from a crash and
`--skip-done-today` keeps scheduled runs idempotent; per-run score/tier diffs are recorded
in `writeback_events`. Still out of scope: suppressing Slack/outreach when the score is
*unchanged* between runs, surfacing the score diff in the HTML report, and a Postgres
backend for the run-state store. Roadmap: scouts as MCP-tool agents (Apollo/LinkedIn/Gong),
a discovery agent for net-new accounts, a Gong call-outcome agent, a reply-handling
agent — all keeping the `run_agent` runtime, only growing toolsets.
