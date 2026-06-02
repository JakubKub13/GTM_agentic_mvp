# duvo-signal-loop — Architecture reference

The deep map. Use it to go deep on one area or to verify your reading of the code.
The code is the source of truth; if this drifts, trust the code and flag the drift.

## Table of contents

1. What the project is
2. The core idea: one runtime, many agents
3. The per-account pipeline (orchestration)
4. The three agent roles
5. The pluggable write-back layer
6. The data contract
7. Cross-cutting infrastructure
8. Testing philosophy
9. Bounded agency — the throughline
10. Known limits & roadmap

---

## 1. What the project is

A multi-agent GTM (go-to-market) pipeline for **Duvo** — a company selling AI agents
that automate retail/CPG back-office work (reconciliation, PO/invoice matching). It
takes a CSV of target retail/CPG accounts and, per account, runs a **scouts → analyst
→ router** chain of tool-using Claude agents that scout intent signals, score ICP
(Ideal Customer Profile) fit, and route the account into a sales stack (CRM + Slack +
outreach), then emits an HTML audit report. It is built as a polished demo/portfolio
project — note the sourced `.env.example`, the honest "where it breaks" section, and
the fully-mocked offline test suite.

## 2. The core idea: one runtime, many agents

`agent_core.run_agent()` (`agent_core.py:32`) is the single primitive. Every agent is a
different `(system, tools, impls)` triple over the same loop:

1. `messages.create` with conversation + tools.
2. Append assistant response; extract `tool_use` blocks. None → return.
3. For each tool call: look up impl, call it, **await if `inspect.isawaitable`** (this is
   what lets sync and async tools coexist); catch exceptions → `"tool error: …"`;
   unknown tool → `"unknown tool: …"`.
4. Feed all `tool_result`s back as one user message.
5. Any called tool in `final_tools` → return. Else loop to `max_turns`.

Design choices to notice:
- **Sync/async polymorphism** via `inspect.isawaitable` — and the explicit falsy edge
  case (returning `None`/`0` must NOT be awaited; tested).
- **Errors never crash the loop** — a raising tool becomes a recoverable tool_result.
- **`max_turns`** is the infinite-loop backstop (default 8; scouts `MAX_SCOUT_SEARCHES+2`,
  analyst `MAX_ANALYST_SEARCHES+3`, router 6).
- **`log` list** threads through to record every tool call as `"name(args)"` for the report.
- **Lazy `AsyncAnthropic` singleton** (`_get_client`) — imports without a key; key
  required (`config.require`) only on first real call.

## 3. The per-account pipeline (orchestration)

`main.run()` (`main.py:74`):
- Loads `companies.csv` → `list[Company]`.
- `asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)` (default 5).
- `asyncio.gather` over all accounts, each in `_process_account`.
- `finally: await http_client.aclose()` — pool always drained, even if every account fails.
- Filters `None`, renders report.

`_process_account` (`main.py:29`) — triple isolation:
- `async with semaphore` → bounded concurrency.
- `async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS)` (300s) → one hung account can't
  stall the batch.
- `try/except Exception: return None` → a bad account logs and yields `None`; never
  cancels siblings or propagates. (Because it never raises, a bare `gather` is safe —
  noted in a comment.)
- Chain: `scout_all` → `run_analyst` → build `RunResult` → `run_router`; a shared
  `agent_log` threads through all three and is attached to the result.

## 4. The three agent roles

### Scouts (`scouts.py`) — 4 concurrent signal hunters
- Four `BEATS`: `erp_migration`, `hiring`, `ma_leadership`, `pain` — matching
  `Signal.signal_type` `Literal`s.
- `scout_all` fans out via `asyncio.gather`; each wrapped in `_run_scout_safe` so one
  failing beat returns `[]` instead of killing the other three (per-beat isolation,
  below the per-account layer).
- Each scout = `run_agent` with `exa_search` (async) + `submit_signals` (sync, final).
  Prompt: search iteratively, max `MAX_SCOUT_SEARCHES` (4), **never invent**, empty
  results fine, discard off-beat/off-company.
- Results re-validated into `Signal`s with `signal_type` forced to the beat's key — the
  model never picks the type.

### Analyst (`analyst.py`) — validate, score, draft + deterministic guards
- `run_agent` with `exa_search` (verify doubtful signals, max `MAX_ANALYST_SEARCHES`=2)
  + `record_assessment` (final). Given the full `ICP_DEFINITION` and the signals as JSON.
- **Two-layer defense** (assumes the LLM misbehaves):
  - **Layer A — coercion before model construction:** no assessment → `_conservative_default`
    (score 3, Tier 3, needs research); non-numeric score → 3; out-of-range → **clamped
    [1,10]**; invalid tier/confidence → safe defaults; malformed outreach → empty draft;
    any `ICPScore` failure → conservative default.
  - **Layer B — `apply_guards()`** (`analyst.py:184`), pure/sync/deterministic, rules in
    order: (1) no dated signals → `confidence=low`, `needs_human_research=True`; (2)
    `confidence==low` & `score>=7` → cap to 6; (3) tier derived from score —
    `>=8 & not needs_human_research`→Tier 1, `>=5`→Tier 2, else Tier 3 (**overrides the
    model's tier**); (4) `needs_human_research` & Tier 1 → downgrade to Tier 2.
  - Net effect: a hallucinated high score can never *alone* yield a confident Tier 1.

### Router (`router.py`) — the never-send safety layer
- `run_agent` with four no-input tools: `crm_upsert`, `slack_alert`, `outreach_queue`,
  `finish`.
- `confident_t1 = (tier == "Tier 1") and (not needs_human_research)` — computed once,
  deterministically, outside the model.
- **Tools self-guard:** `crm_upsert` always allowed; `slack_alert`/`outreach_queue` put
  the safety check *first*, **before the lazy `from writeback import …` and any await** —
  a non-confident-Tier-1 returns `"refused: not a confident Tier 1 (safety guard)"` and
  the send module is never even imported. Proven by `test_guard_fires_before_lazy_import`
  (deletes the modules from `sys.modules`, asserts they never reappear).
- **Nothing auto-sends:** outreach only *queues* into a review list (Brevo) / *paused*
  campaign (lemlist) for a human rep. `dry_run` returns `[dry-run] …` strings; agents
  still really decide.

## 5. The pluggable write-back layer (`writeback/`)

Two dispatchers, two adapters each, swap by one env var, all sharing one HTTP pool:

| Interface | Default | Alt | Switch |
|---|---|---|---|
| `crm.upsert_account(score)` | Attio | HubSpot | `CRM_PROVIDER` |
| `outreach.queue_lead(score, email)` | Brevo | lemlist | `OUTREACH_PROVIDER` |
| `slack.alert_tier1(score)` | Slack webhook | — | — |

- Dispatchers read the provider **at call time** (so tests monkeypatch freely); unknown
  provider → warn + fall back to default; adapters imported lazily.
- Adapter robustness: **Attio** tries `{name, domains}` then `name`-only; **HubSpot** does
  a real upsert (search by domain → PATCH or POST) plus an idempotent custom-property
  ensure; **Brevo** tries rich attributes then minimal `email + listIds`; **lemlist** posts
  to a paused campaign with `deduplicate=true` and validates the API key before the
  campaign id.
- Shared client (`http_client.py`): lazy module-level `httpx.AsyncClient` with uniform
  `HTTP_TIMEOUT_SECONDS`, drained by `aclose()` in `main`'s `finally`.

## 6. The data contract (`models.py`)

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

- **`config.py`** — centralized env reads; `require(name, value)` raises a clear error for
  required keys (Exa, Anthropic) but leaves write-back keys lazy so `--dry-run` runs
  without a full `.env`. Model hardcoded `claude-sonnet-4-6`. All concurrency/timeout
  knobs env-overridable with sane defaults.
- **`logging_setup.py`** — single `duvo.*` logger tree; idempotent `configure_logging`
  (no duplicate handlers); `propagate=False`; level resolvable from string with INFO
  fallback; secrets never logged.
- **`reporter.py`** — the only deliberately **sync** module (pure CPU + file I/O, safe from
  async); Jinja2 autoescape; sorts accounts by score desc; writes
  `output/run-report.html`. The template renders per account: tier badge, why-fit,
  linked/dated signals, drafted outreach, the agent tool-call log, and the three
  write-back statuses.

## 8. Testing philosophy

281 tests, fully offline — Anthropic, Exa, and all HTTP mocked; no keys, no network.
`asyncio_mode = auto`. `conftest.py` supplies `make_fake_async_client`, `_fake_response`,
`make_score`. The agent-testing trick: **patch `run_agent` itself** with a fake that calls
a chosen tool sequence — so router/analyst logic is tested deterministically without
simulating model turns. The safety guards get the most rigor (the `sys.modules`
import-order proof is the standout). `test_agent_core.py` covers the loop edge cases
(final tools, max turns, tool errors, unknown tools, falsy-return non-await).

## 9. Bounded agency — the throughline

The whole design refuses to trust the LLM with irreversible actions. Three deterministic
gates, each outside the model:
1. **Scouts** can't invent — prompt + `source_url`-required schema; undated signals
   penalized downstream.
2. **`apply_guards()`** — deterministic scoring/tiering that overrides the model and caps
   hallucinated confidence.
3. **Router tool guards** — `confident_t1` in code; send-path modules don't even import
   for non-qualifying accounts; nothing auto-sends (review list / paused campaign only).

## 10. Known limits & roadmap

Author-flagged limits: Exa noise/staleness on big brands; agent-loop latency/variance
(bounded by turn caps); no real person-level email (uses a test email); one-shot run with
no CRM dedup (re-runs create duplicate records — production would upsert-on-domain and
alert only on score *change*). Roadmap: scouts as MCP-tool agents (Apollo/LinkedIn/Gong),
a discovery agent for net-new accounts, a Gong call-outcome agent, a reply-handling
agent — all keeping the `run_agent` runtime, only growing toolsets.
