# duvo-signal-loop — Architecture reference

The deep map. Use it to go deep on one area or to verify your reading of the code.
The code is the source of truth; if this drifts, trust the code and flag the drift.

The runtime lives under the `duvo/` package; `main.py` at the repo root is a thin
shim that calls `duvo.orchestrator.main` (so `uv run python main.py` and
`python -m duvo.orchestrator` both work). `duvo/api/` is a second entry surface — a
FastAPI app that serves a Vite/React SPA (`frontend/`) over the *same* orchestrator.

## Table of contents

1. What the project is
2. The core idea: one runtime, many agents
3. The per-account pipeline (orchestration)
4. The three agent roles
5. The pluggable write-back layer
6. The data contract
7. Cross-cutting infrastructure
8. Durable run-state store (SQLite)
9. The web console (FastAPI API + React SPA)
10. Testing philosophy
11. Bounded agency — the throughline
12. Known limits & roadmap

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

`orchestrator.run()` (`duvo/orchestrator.py:398`):
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

`_process_account` (`duvo/orchestrator.py:169`) — triple isolation:
- `async with semaphore` → bounded concurrency.
- `async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS)` (300s) → one hung account can't
  stall the batch.
- `try/except Exception: return None` → a bad account logs, marks the row `failed` (real
  runs), and yields `None`; never cancels siblings or propagates. (Because it never
  raises, a bare `gather` is safe — noted in a comment.)
- Chain: `scout_all` → `run_analyst` → build `RunResult` → `run_router`; a shared
  `agent_log` threads through all three and is attached to the result.
- On real runs, `_persist_success` (`duvo/orchestrator.py:105`) marks the account `done`
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
- **`duvo/infra/events.py`** — the **one live-feed boundary**: an in-process pub/sub bus for the
  web SSE stream, with the same on/off shape as tracing. `publish(run_id, event)` is a **no-op when
  no subscriber is registered** (the CLI/offline guarantee), and on a full bounded queue it *drops*
  the event rather than awaiting the producer — no backpressure into the pipeline. Producers stamp
  events through a `contextvars` context (`set_context(run_id, account)` in `_process_account`,
  `enrich_context(agent=…, beat=…)` in each agent) so no agent signatures change and tags stay
  isolated across `asyncio.gather`. Events are bounded + redacted: `make_tool_event` runs args
  through `agent_core._short` + `tracing._mask`, so a `tool` event never carries raw arguments.
  Three event types flow: `tool`, `account` (status + terminal score/tier), `status` (run-level).
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
- **Wired in the orchestrator** (`_persist_success`, real runs only — dry-runs never persist on
  the CLI). The score diff is stored in `writeback_events` for future score-change alerting; it is
  not yet surfaced in the HTML report.

## 9. The web console (FastAPI API + React SPA)

A second entry surface over the *same* `orchestrator.run(...)`: a single FastAPI process that both
exposes the run API and serves the built SPA. Nothing here forks the pipeline — it adds auth,
durable run-state (§8), and a live feed (`infra/events.py`, §7).

- **`duvo/api/app.py`** — `create_app()` is the app factory (mounted by uvicorn `--factory`). The
  lifespan owns process-level setup/teardown: configure logging + tracing, sweep stale `running`
  rows to `interrupted` on boot, and on shutdown drain `jobs`, close the shared http/Exa clients,
  and flush tracing. The SPA is mounted **last** from `DUVO_FRONTEND_DIR` (default `frontend/dist/`)
  with an `index.html` fallback for client-side routes; a missing build dir is tolerated (API-only).
- **`duvo/api/auth.py`** — Google SSO (`authlib`) + signed session/CSRF cookies (`itsdangerous`).
  **Auth env is read lazily here, not in `config.py`**, so the app imports and the offline tests run
  without secrets. `get_current_user` guards every non-auth route; `require_csrf` is a double-submit
  guard on state-changing routes; `require_real_run_authorization(user, confirm)` is the **#14 gate**
  — a non-dry run needs an allowlisted role (`AUTH_REAL_RUN_ROLES`, default `admin,operator`) **and**
  an explicit `confirm`, else 403 before any persistence. This is the web mirror of the router's
  bounded-agency guard: the dangerous default is refusal, enforced in code.
- **`duvo/api/routes_runs.py`** — `POST /runs` runs a strict preflight (`start_run_strict`: the run
  row + all `pending` accounts in one transaction, re-raising → no phantom 201) then spawns
  `asyncio.create_task(run(..., persist=True, manage_clients=False, triggered_by=user.email))`. Read
  endpoints (`GET /runs`, `/runs/{id}`, `/runs/{id}/accounts/{domain}`) are served from `store/queries.py`.
  The **SSE** `GET /runs/{id}/stream` subscribes to the event bus, **replays the persisted snapshot**
  (one `account` event per row + a `status` event), then forwards live `tool`/`account`/`status`
  events until terminal. Tool events are **live-only — never persisted** (MVP semantics); the durable
  record of an account's tool calls is `account_runs.agent_log_json`.
- **`duvo/api/jobs.py`** — the in-process `run_id → asyncio.Task` registry. A done-callback marks a
  crashed/cancelled task's run `failed`/`interrupted` (it can't clobber a terminal status). This
  registry + the in-process event bus are **why the app must run `--workers 1`**.
- **Event production seam** — `agent_core` publishes a `tool` event at each tool call (guarded by
  `events.get_context().get("run_id")`, so it's inert on the CLI); the orchestrator publishes
  `account` (running/terminal) and run-level `status` events. The context is set once per account
  and enriched per agent — no agent signature changes.
- **`frontend/`** — Vite + React 18 + TypeScript (strict) + Tailwind + shadcn/ui + TanStack Query +
  React Router, managed with **pnpm**, built to `dist/`. One typed client (`src/lib/api.ts`) is the
  whole backend contract (fetch with `credentials: 'include'` + CSRF echo; `EventSource` for the
  stream). Features: `new-run` (the launch form + the #14 confirm dialog), `live-run` (the
  `useRunStream` hook — snapshot then SSE, with a polling fallback), and `account-detail` (the
  drill-down drawer). Shared chrome lives in `components/{shell,common,ui}`, auth in `auth/`.

## 10. Testing philosophy

518 Python tests + 25 frontend Vitest suites, fully offline — the LLM provider, Exa, all HTTP, and
(on the frontend) the api client / `EventSource` mocked; no keys, no network.
`asyncio_mode = auto`. `conftest.py` supplies `make_fake_async_client`, `_fake_response`,
`make_score`. The agent-testing trick: **patch `run_agent` itself** with a fake that calls
a chosen tool sequence — so router/analyst logic is tested deterministically without
simulating model turns. The safety guards get the most rigor (the `sys.modules`
import-order proof is the standout). `test_agent_core.py` covers the loop edge cases
(final tools, max turns, tool errors, unknown tools, falsy-return non-await). The durable
store is covered too: the three tables, `--resume` / `--skip-done-today` skip logic, the
CRM dedup adapters, and the write-back diff. The web layer has its own suites —
`test_api_*` (app factory, auth + the #14 gate, run launch, the SSE stream, the jobs registry),
`test_infra_events` / `test_event_wiring` (the bus + producer context) — and the React SPA runs
offline **Vitest** suites under `frontend/src/**` (`pnpm test`), mocking the api client / `EventSource`.

## 11. Bounded agency — the throughline

The whole design refuses to trust the LLM with irreversible actions. Three deterministic
gates, each outside the model:
1. **Scouts** can't invent — prompt + `source_url`-required schema; undated signals
   penalized downstream.
2. **`apply_guards()`** — deterministic scoring/tiering that overrides the model and caps
   hallucinated confidence.
3. **Router tool guards** — `confident_t1` in code; send-path modules don't even import
   for non-qualifying accounts; nothing auto-sends (review list / paused campaign only).
4. **Web #14 gate** — a real (non-dry) run from the API needs an allowlisted role *and* an
   explicit `confirm`, enforced in `api/auth.py` before any persistence (the UI confirm dialog
   is a second layer, not a replacement). Same principle: irreversible actions are code-gated.

## 12. Known limits & roadmap

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
