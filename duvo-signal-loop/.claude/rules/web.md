---
paths:
  - "duvo/api/**"
  - "duvo/store/**"
  - "frontend/**"
---

# Web app conventions (API · durable store · SPA)

The web surface wraps the same `orchestrator.run(...)` the CLI uses — it adds auth, durable
run-state, and a live feed, but never forks the pipeline. Single process: **`uvicorn
duvo.api.app:create_app --factory --workers 1`** serves both the API and the built SPA.

## Durable store (`duvo/store/`)

- **One async seam over stdlib `sqlite3`** (`db.py`): sync calls are offloaded via
  `asyncio.to_thread` (the house rule for sync-only libs). Open with WAL + `synchronous=NORMAL`
  + `busy_timeout` so concurrently-`gather`-ed accounts can write without "database is locked".
- **Persistence can never abort the pipeline.** `execute` / `query_one` / `query_all` **log and
  return a safe default on any error** (mirrors `infra.tracing`). Only the strict preflight
  (`start_run_strict` / `execute_tx`) re-raises — `POST /runs` must fail fast, not spawn a phantom run.
- **Schema lives in `schema.sql`** (`PRAGMA user_version` + guarded `ALTER`s for migrations).
  Three tables: `runs`, `account_runs`, `writeback_events`. Read-side helpers for the API live in
  `queries.py` (`list_runs`, `get_run`, `get_account_detail` with the prior-run diff).
- **Dry-run isolation:** real-run queries filter `dry_run = 0` and `action != "simulated"` so a
  persisted dry-run never baselines a diff, gates idempotency, or counts for `--skip-done-today`.

## API (`duvo/api/`)

- **App factory only** (`app.py:create_app()`), mounted by uvicorn `--factory`. The lifespan owns
  process-level setup/teardown (logging, tracing, interrupted-run sweep, draining `jobs`, closing
  the shared http/Exa clients). The SPA is mounted last from `DUVO_FRONTEND_DIR` (default
  `frontend/dist/`) with an `index.html` fallback; a missing build dir is tolerated (API-only).
- **Auth env is read lazily in `auth.py`, not `config.py`** — so the app imports and the offline
  tests run without secrets; only live auth/session use needs `SESSION_SECRET`, `GOOGLE_CLIENT_*`,
  `AUTH_ALLOWED_*`. Session + CSRF are signed cookies (`itsdangerous`); state-changing routes take
  the `require_csrf` double-submit guard; every non-auth route takes `get_current_user`.
- **The #14 real-run gate is code, not a prompt:** a non-dry run must pass
  `require_real_run_authorization(user, confirm)` (allowlisted role **and** explicit `confirm`) →
  403 before any persistence. The minted dev-login token is `admin`; mirror this gate for any new
  side-effecting endpoint.
- **In-process registry** (`jobs.py`): `POST /runs` spawns `asyncio.create_task(run(...))` and
  registers it; a done-callback marks a crashed/cancelled run `failed`/`interrupted`. This is why
  the app is single-worker. Server run path is `persist=True, manage_clients=False,
  triggered_by=user.email`; the CLI owns its own client lifecycle (`manage_clients=True`).
- **Live updates over SSE** (`GET /runs/{id}/stream`): replay the persisted snapshot (account +
  status events) on connect, then forward live `tool`/`account`/`status` events until terminal.
  Tool events are **live-only and not persisted** (plan #10 MVP semantics) — don't assume the feed
  backfills tool calls for a completed run; the per-account `agent_log_json` is the durable record.

## Frontend (`frontend/`)

- **pnpm only** (`pnpm install` / `dev` / `build` / `test`). Stack: Vite + React 18 + TypeScript
  (strict) + Tailwind + shadcn/ui primitives + TanStack Query + React Router. Build with
  `pnpm build` → `dist/`; the API serves it.
- **One typed API client** (`src/lib/api.ts`) is the single backend contract — all `fetch`/SSE goes
  through it with `credentials: 'include'` and the CSRF echo header. Don't scatter `fetch` calls or
  hard-code routes in components.
- **Feature-first layout** under `src/features/` (`new-run`, `live-run`, `account-detail`), shared
  chrome in `src/components/{shell,common,ui}`, auth in `src/auth/`. Mirror the neighbouring file.
- **Test-first with Vitest + Testing Library** (jsdom), one `*.test.tsx` beside the unit. Tests run
  fully offline (mock the api client / EventSource) — keep them key/network-free, like the Python suite.
- **The real-run confirm dialog** (#14) is a UI guard in front of the server gate, not a replacement —
  keep both. Dry-run defaults ON.
