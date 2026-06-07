# Plan: Wire duvo-signal-loop to a web frontend (Vite + React SPA over FastAPI)
_Locked via grill — by Claude + jakubkubala3 · revised after Codex Round 1_

## Goal
Give duvo-signal-loop a browser UI. The backend is currently a CLI-only async batch
tool (`main.py` → `asyncio.run(orchestrator.run(...))`) that persists run state to SQLite
and writes a static HTML report. We will add a **FastAPI HTTP layer** in-process and a
**Vite + React SPA** that talks directly to it. The end state is a *full operational tool*
(trigger runs, watch them live, approve outreach, browse history, manage accounts),
delivered in **three phases**. Phase 1 (this plan's MVP) is **run control**: sign in with
Google, start a run from the browser, and watch it execute live — per-account status **and**
a streaming feed of each agent tool call — with an account drill-down that reads
persisted data. Phases 2 (approval cockpit) and 3 (history + account management) are named
and scoped but deferred.

## Approach

### Pipeline refactor — make `orchestrator.run()` API-drivable (prerequisite for everything)
1. **Generalize `run()`'s signature.** Today `run()` generates its own `run_id`, reads
   `companies.csv` itself (orchestrator.py:193/235), and ties `persist`/client-teardown to
   `dry_run`. Refactor to:
   `run(*, run_id, companies, triggered_by, dry_run, test_email, persist, manage_clients,
   limit=None, concurrency=None, resume_run_id=None, skip_done_today=False, log_level=...)`.
   - **`run_id` is unambiguous:** `run_id = resume_run_id or uuid4().hex` (the existing
     orchestrator.py:232 rule). There is no separate caller-supplied `run_id` racing
     `resume_run_id`.
   - **CLI** (`main()`): `run_id = args.resume_run_id or uuid4().hex`, calls
     `load_companies()`, passes `triggered_by="cli"`, `persist = not dry_run`,
     `manage_clients=True`. Behavior unchanged — preserves the offline tests (incl.
     tests/test_main.py:641, dry-run persists nothing on the CLI path).
   - **API**: `run_id = uuid4().hex` (no resume from the UI in MVP), parses the
     uploaded/default `companies`, passes `triggered_by=<user.email>`, `persist=True`
     (always — see #3), `manage_clients=False`.
   - **Persist the run's input.** The exact `companies` a run was launched with (full JSON,
     incl. `description`) is stored on the `runs` row (`companies_json`, #15). Resume
     reconstructs companies from this stored input — **never** from repo-local
     `companies.csv` — so an API run launched from an uploaded CSV is faithfully recoverable.
2. **One owner of the full `runs` insert.** `start_run()` gains a `triggered_by` column and
   remains the single writer of the `runs` row (model, env, concurrency, accounts_total,
   dry_run, triggered_by). The API does **not** pre-insert a partial row (that would collide
   with `start_run(... ON CONFLICT DO NOTHING)`, runs.py:29). Instead the API calls a strict
   variant (#4) before launching the task so it can fail fast.
3. **Decouple `persist` from `dry_run`.** `persist` becomes an explicit parameter.
   - CLI: `persist = not dry_run` (unchanged; CLI dry-runs still persist nothing).
   - **Server: `persist=True` always**, so the live view, drill-down, and history are
     coherent even for a dry-run. `dry_run` now controls **only** write-back simulation, not
     persistence. README + a new server-path test document this; the existing CLI test stays
     green because it exercises the CLI path.
   - **Dry-run rows are isolated from real-run logic (first-class `dry_run` filter).**
     Persisted dry-runs must never leak into real-run semantics:
     - **Write-back events:** `derive_action()` detects the `[dry-run]` prefix (and/or the
       run's `dry_run` flag is threaded into `record_event`) → action **`simulated`**, never
       `alerted`/`queued`/`upserted`. The idempotency guard (#6a) **ignores `simulated`** rows.
     - **Baselines/skip:** `last_done_for_domain` (diff baseline), `done_domains_for_date`
       (`--skip-done-today`), and the idempotency query **JOIN `runs` and filter `dry_run=0`**.
       A dry-run can never become a diff baseline, be skipped-as-done, or gate a real send.
4. **Strict create-run preflight (one transaction).** Add `store.start_run_strict(...)` that
   does **not** swallow errors (unlike db.py:63's `_safe`) and, in a **single transaction**,
   writes both the `runs` row **and** all `pending` `account_runs` rows (#5). `POST /runs`
   calls it and returns **5xx** if it fails — never a phantom `201 {run_id}` for an
   unrecorded run, and never a `201` with a blank account snapshot. The async task is
   launched only after this preflight commits. In-loop status updates stay best-effort.
5. **Pre-seed all accounts as `pending`** — done inside the #4 preflight transaction (not
   best-effort inside the run path), so the snapshot is guaranteed present at `201`. Fixes
   the "queued accounts absent" gap (orchestrator.py:142) with no race. **Resetting an account
   to `pending`/`running` (on resume) clears its terminal fields** — `upsert_account_run`
   (account_runs.py:22) must null out `score`/`tier`/`confidence`/`error`/`finished_at`/
   `signals_*`/`agent_log_json` in the same statement, so a restarted account never shows the
   previous attempt's stale terminal data.
6. **Cancellation / interrupt semantics — surface, don't auto-resume.** Add `cancelled` /
   `interrupted` account+run statuses. Handle `asyncio.CancelledError` at both run and
   account level (don't blindly mark `done` in `finally`). On app shutdown, mark still-active
   runs `interrupted`. On app startup, **mark stale `running` runs as `interrupted` and
   surface them in the UI** — resume is **human-initiated**, NOT automatic, because a crash
   *after* Slack/outreach fired but *before* the account was marked `done` would otherwise
   re-fire those side effects on resume. Safe resume is enabled by the per-channel
   idempotency guard (#6a).
6a. **Run-scoped write-back idempotency guard with a delivery lifecycle (makes resume safe
   without muting future runs).** Two parts:
   - **Scope = `(run_id, domain, channel)`, not `domain:channel`.** Suppression applies only
     to retries *within the same run* (a resume reuses the same `run_id`, orchestrator.py:232).
     A fresh future run (new `run_id`, new score) re-fires normally — we must NOT suppress a
     domain's Slack/outreach forever.
   - **Delivery lifecycle on the ledger.** `writeback_events` gains a `delivery_status`
     (`attempting` → `succeeded` / `failed`). Write `attempting` **before** the Slack/outreach
     call, update to `succeeded`/`failed` **after**. On resume: **suppress only `succeeded`**;
     **retry definite `failed`**; a dangling `attempting` (crash mid-call → effectively
     `unknown`) is **surfaced for a human decision**, never silently re-fired — consistent with
     human-initiated resume (#6). CRM stays dedup-safe via upsert-by-domain regardless.

### Backend — FastAPI layer (new `duvo/api/` package, concern-per-folder per house rules)
7. **App + lifespan.** `duvo/api/app.py`. The `lifespan` context: at **startup** calls
   `configure_logging()` and `tracing.init_tracing()` **once** (today these run inside
   `run()` — wrong for a long-lived process) and runs the interrupted-run sweep (#6); at
   **shutdown** drains active run tasks, then calls `http_client.aclose()`,
   `exa_tool.aclose()`. **Exa stays lazy** — the lifespan does NOT eagerly construct it
   (that would force `EXA_API_KEY` even for auth/history-only startup, exa_tool.py:27); it
   only *owns the close* on shutdown. Tracing is **flushed per-run-completion** (decoupled
   from client ownership), so observability isn't lost when `manage_clients=False`.
8. **In-process job execution.** `POST /runs`: authenticate → authorize (real-run gate, #14)
   → validate request → generate `run_id` → `start_run_strict()` (#4, commits run + pending
   rows) → `asyncio.create_task(run(..., run_id=run_id, companies=...,
   triggered_by=user.email, manage_clients=False, persist=True))` → `201 {run_id}`. A
   process-level registry maps `run_id` → task. Each task gets
   **`task.add_done_callback(...)`** that: removes it from the registry, logs any unhandled
   exception, and marks the run `failed`/`interrupted` if the task died without a clean
   terminal status — so tasks never leak and exceptions never get swallowed. The registry is
   also drained on shutdown (#6).
9. **In-process event bus** (new `duvo/infra/events.py`, mirrors `infra/tracing.py`'s
   optional-boundary pattern):
   - Maintains, **per `run_id`**, a set of **bounded** subscriber `asyncio.Queue`s.
     `publish(run_id, event)` is a no-op with no subscribers (CLI + offline tests
     unaffected — same guarantee tracing gives). On a full queue it **drops/coalesces**,
     never awaits the producer (no backpressure into the pipeline).
   - Producer threads `run_id` + structured context via a **`contextvar`**, so no agent
     signatures change. **Context is enriched where the metadata lives** (fixing the
     "feed lacks account/beat/role" gap): `_process_account` sets `run_id`+account;
     `run_scout` adds `agent="scout"`+beat; `run_analyst`/`run_router` add their role.
     `agent_core.run_agent` publishes a `tool` event at its existing tool-call site
     (agent_core.py:91). `asyncio.gather` copies context per task, so tags stay isolated
     across interleaved runs.
   - **Bounded, redacted event payload.** Events never carry raw `tc.arguments` (which for
     `record_assessment` include the full outreach draft + reasoning). The `tool` event uses
     the existing **`_short(tc.arguments)`** truncation (agent_core.py:140) plus email
     masking (reuse `tracing._mask`). Define a small typed event schema (`type`, `run_id`,
     `account`, `agent`, `beat`, `tool`, `arg_summary`, `ts`).
10. **Persisted audit trail + honest live-feed semantics.** Add an `agent_log_json` column to
    `account_runs`; persist each account's `agent_log` **at account completion** (today
    in-memory only, orchestrator.py:152/157). **MVP semantics, stated plainly:** the live
    `ToolCallFeed` is **live-only/ephemeral**; the SSE snapshot on (re)connect replays
    account *status* (pending/running/terminal) + **completed** accounts' `agent_log_json` —
    it does **not** replay an in-flight account's earlier tool lines (a reconnect catches up
    live from that point on). Full in-flight replay would need an **append-only `tool_events`
    table** written per event — named here as the durability upgrade, deliberately out of the
    MVP to avoid high-frequency SQLite writes under concurrency.
11. **SSE endpoint.** `GET /runs/{id}/stream` → `text/event-stream` (`sse-starlette`):
    register a bounded subscriber queue, flush the DB snapshot (#10), stream `account` /
    `tool` / `status` events until terminal status, then close. Heartbeat comments +
    `Cache-Control: no-cache` + disabled proxy buffering to survive proxies.
12. **Read endpoints.** `GET /runs` (history), `GET /runs/{id}` (run + `account_runs`
    snapshot), `GET /runs/{id}/accounts/{domain}` (detail: `score_json`, `signals_json`,
    `agent_log_json`, and the score/tier diff already computed for `writeback_events`). New
    `duvo/store/` query helpers, one-file-per-concern.
13. **Auth — Google SSO (OAuth2), hardened as Phase 1 acceptance criteria.**
    `GET /auth/login` → Google consent **with `state`**; `GET /auth/callback` validates the
    `id_token` (issuer, audience, expiry, signature) and **enforces Workspace `hd` /
    allowlist**; issues an **httpOnly + Secure + SameSite** session cookie signed with an
    **env session secret**. `GET /me`, `POST /auth/logout`. A dependency guards every
    non-auth route; **CSRF protection** on state-changing requests. These are tested, not
    just listed.
14. **Authorization guardrail (identity ≠ permission to cause side effects).** Until the
    Phase 2 approval cockpit exists, a **real (non-dry) run** — which writes CRM records and
    posts Slack alerts (outreach is queue-for-review only, never auto-sent by design) —
    requires an **allowlisted role + an explicit confirmation**. The UI `DryRunToggle`
    **defaults ON**; launching a real run is a deliberate, confirmed action.
15. **Schema migrations (versioned, not `IF NOT EXISTS`).** Add a `migrate()` step keyed on
    `PRAGMA user_version`, using `PRAGMA table_info`-guarded `ALTER TABLE` to add, on existing
    DBs: `triggered_by` + `companies_json` (runs), `agent_log_json` (account_runs), and
    `delivery_status` (writeback_events, default `succeeded` for legacy rows so old data isn't
    mistaken for retryable). `_INITED` (db.py:36) gates per-process; migration runs on first
    connect of a process.
16. **Serve the SPA.** Mount the Vite build via `StaticFiles` with SPA fallback to
    `index.html` → one deployable (one Python process). Separate static deploy remains
    possible later.
17. **Cross-run rate cap (decided).** Add a **process-wide global account semaphore**
    (`DUVO_GLOBAL_MAX_ACCOUNTS`, default e.g. 8) bounding **total** concurrent accounts
    across ALL runs, on top of each run's per-run `Semaphore(concurrency)`. Preserves
    concurrent runs (the chosen design) while capping LLM/Exa pressure.

### Frontend — Vite + React SPA (new `frontend/` at repo root)
Stack: **Vite + React + TypeScript**, **TanStack Query**, **React Router**,
**Tailwind + shadcn/ui**, native **`EventSource`** for SSE.
18. **App shell:** `AuthGuard`, `LoginScreen` (Google button), `AppLayout` (top nav + user
    menu + sign out), `Toast`, `ErrorBoundary`.
19. **New Run:** `NewRunForm` = `FileDropzone` (companies.csv or repo default),
    `ConcurrencyInput`, `DryRunToggle` (**default ON**), `SkipDoneTodayToggle`,
    `TestEmailInput`, `StartRunButton` → `useStartRun` → `POST /runs`. A **confirm dialog**
    gates a real (non-dry) run. Navigate to the live run view on success.
20. **Live Run view (centerpiece):** `RunHeader` (status badge incl. `interrupted`/
    `cancelled`, `ProgressBar` done/total, `ElapsedTimer`, `triggered_by`, model/env tags) ·
    `AccountsTable` of `AccountRow` (live status from `pending`→`running`→terminal,
    `ScoreBadge`, `TierBadge`, confidence, signal count) · **`ToolCallFeed`** — auto-scroll
    stream of `account · agent:beat · tool(args)` from SSE `tool` events, filterable.
    `useRunStream(runId)` wraps `EventSource`, replays the snapshot, reconnects on drop, and
    **falls back to `GET /runs/{id}` polling** if SSE fails.
21. **Account drill-down:** `AccountDetailDrawer` = `ScoreCard` (why_fit/why_not),
    `SignalList` (source links, dates, type), `OutreachDraftPreview`, `DiffBadge`
    (▲+2 vs last run), `AgentLogViewer` — from `GET /runs/{id}/accounts/{domain}`.
22. **Cross-cutting:** loading skeletons, empty states, error toasts, confirm dialog.

### Dependencies & tooling (explicit — none of these are in the repo today)
25. **Backend (`uv add`, per the uv-only house rule):**
    `uv add fastapi "uvicorn[standard]" sse-starlette python-multipart authlib itsdangerous`
    (`httpx` is already a dependency). `authlib` for Google OAuth, `itsdangerous` for signed
    session cookies, `python-multipart` for the CSV upload, `sse-starlette` for the event
    stream. Commit the updated `uv.lock`.
26. **Frontend package manager: pnpm**, with a committed `pnpm-lock.yaml` under `frontend/`.
    Vite + React + TypeScript scaffold; Tailwind + shadcn/ui; TanStack Query + React Router.

### Tests (keep the suite fully offline — house rule)
23. Backend: FastAPI `TestClient` / `httpx.ASGITransport`; mock `run()` for endpoint tests.
    Unit-test: event-bus per-`run_id` isolation + no-subscriber no-op + bounded-queue drop
    (no producer block); the `manage_clients=False` path (shared clients NOT closed); the
    `persist`-decoupled-from-`dry_run` server path; `start_run_strict` raising → 5xx;
    pre-seed pending in the strict preflight (5xx leaves no task + no rows); interrupt/cancel
    status transitions; the startup interrupt-surface (no auto-resume of side-effecting runs);
    the **run-scoped idempotency guard** suppresses a same-`run_id` Slack/outreach retry but a
    **fresh `run_id` re-fires**; `delivery_status` lifecycle (suppress `succeeded`, retry
    `failed`, surface dangling `attempting`); resetting an account to `pending` clears stale
    terminal fields; resume reconstructs companies from `runs.companies_json`, not
    `companies.csv`; **a persisted server dry-run does NOT suppress, skip-as-done, or alter the
    score-diff of a later real run for the same domain** (dry-run isolation); the task
    `done_callback` marks a crashed run `failed` and de-registers;
    the event payload is `_short`/email-masked, never raw `tc.arguments`; the migration
    (`user_version` upgrade adds `triggered_by` + `agent_log_json`). Auth: mock Google token
    verification; assert `state`/CSRF/cookie-flags/allowlist enforcement. SSE: event ordering
    + snapshot replay + terminal close on a fake stream. Migration test covers the
    `user_version` upgrade adding **all** new columns: `triggered_by` + `companies_json`
    (runs), `agent_log_json` (account_runs), `delivery_status` (writeback_events, legacy rows
    default `succeeded`).
24. Frontend: Vitest + Testing Library for `NewRunForm` (incl. real-run confirm),
    `AccountRow`, `ToolCallFeed`; mocked `useRunStream`. No network in CI.

## Key decisions & tradeoffs (the contestable choices the grill resolved)
- **Vite + React SPA, NOT Next.js.** Backend is already Python/FastAPI; Next's server half
  (SSR/RSC/API routes) would be a redundant BFF proxying to FastAPI = a second runtime to
  deploy. Internal tool, no SEO, no public SSR; a live SSE cockpit is the SPA sweet spot.
  FastAPI serves the static build → one deployable. (Flip only if public/unauthenticated SSR
  pages appear; none do.)
- **In-process asyncio task, NOT a task queue.** No Redis/broker (anti-bloat). Crash recovery
  via `--resume` + a startup sweep; progress from `account_runs` rows + the event bus.
- **Concurrent runs with app-scoped client lifecycle (Option 2).** Shared `httpx`/Exa client
  ownership moves to the lifespan; `run(manage_clients=False)` stops per-run teardown so
  parallel runs are safe. CLI keeps `manage_clients=True`. Bounded globally by
  `DUVO_GLOBAL_MAX_ACCOUNTS` so concurrency doesn't blow LLM/Exa limits.
- **SSE + live tool-call feed, with persisted replay.** Optional `infra/events.py` boundary
  (no-op without subscribers), `contextvar`-fed so no agent signatures change; durability via
  a persisted `agent_log_json` so late joiners/reconnects don't lose history.
- **`persist` decoupled from `dry_run`.** Server always persists (coherent UI); `dry_run`
  only simulates write-backs. CLI behavior unchanged.
- **Google SSO from day one, + an authorization gate on real runs.** Identity for
  attribution/Phase-2; real (side-effecting) runs need role + confirmation and the UI
  defaults to dry-run until the approval cockpit lands.
- **Phased delivery.** MVP = run control + live view + read-only drill-down. Approval
  (Phase 2) and history/account-mgmt (Phase 3) deferred but designed-for.

## Risks / open questions
- **Multi-process scale-out.** The active-run registry + in-process event bus are
  **single-process**. MVP assumption: **one API process** (`uvicorn --workers 1`); documented.
  Redis pub/sub is the future upgrade if we scale workers.
- **SSE through proxies.** Heartbeats + `no-cache` + disabled buffering are required; verify
  against the actual deployment proxy.
- **`contextvar` correctness under `asyncio.gather`** with interleaved runs — must be unit-tested.
- **Resume idempotency (now mechanised, still verify).** Safe resume relies on the per-channel
  idempotency guard (#6a) + CRM upsert-by-domain. The residual edge: a crash *between* firing
  Slack/outreach and writing its `writeback_events` marker. Mitigated by recording the marker
  around (not only after) the call; verify the window is closed under test.
- **CSV upload trust.** Validate size/columns, cap row count before parsing into `Company`.
- **Global semaphore default.** `DUVO_GLOBAL_MAX_ACCOUNTS=8` is a guess; tune against real
  LLM/Exa rate limits.

## Out of scope (bounds the grill established)
- Next.js / SSR / any Node backend or BFF.
- Task queue / broker (Redis, Celery, ARQ) and multi-worker horizontal scale.
- Per-run isolated HTTP/Exa clients (keep the shared pool, app-scoped).
- Phase 2 approval cockpit (approve/edit/reject, send endpoints, pending-approval state) and
  Phase 3 (runs history UI, company CRUD, score-trend charts) — designed-for, not built here.
- Postgres backend for the store (SQLite stays).
- Changing the agent pipeline's bounded-agency guards, scoring, or write-back semantics.

---

## IMPLEMENTATION CONSTRAINTS FOR THE WORKFLOW (read before writing any code)
- **Branch:** work on the local `master` branch working tree only.
- **NO git operations by any agent.** Never `git add`, `git commit`, `git push`, `git stash`,
  `git checkout -b`, or create git worktrees. Every change stays as **uncommitted working-tree
  edits** for the human to review and commit manually. The deliverable is a dirty working tree,
  not commits.
- **TDD + offline tests (house rule):** write the failing test first, then the implementation;
  the entire `pytest` suite must run with **no network**. Frontend tests run under Vitest with
  no network.
- **House rules:** async-first; one LLM boundary (`duvo/llm/`); one tracing boundary
  (`duvo/infra/tracing.py`); concern-per-folder; `uv add` for Python deps (commit `uv.lock`
  to the working tree as a file change, do NOT `git commit`); pnpm for frontend.
- **Bounded agency:** do NOT change the agent pipeline's guards, scoring, or write-back
  semantics (out of scope) beyond what items #1–#17 explicitly require.
