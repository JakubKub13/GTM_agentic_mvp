<!-- GENERATED from .claude/rules/ by scripts/build_agents_md.py — do not edit by hand; re-run after changing a rule. -->

# AGENTS.md — duvo-signal-loop house rules

Coding conventions for this repo, generated from `.claude/rules/` so Codex and Claude Code share one source of truth. Edit the rule files, not this one.

# Architecture & house rules

`duvo-signal-loop` is a multi-agent GTM pipeline. Per account:

```
companies.csv → scout_all (4 scouts ∥) → run_analyst (+apply_guards) → run_router → HTML report
```

Orchestrated in `duvo/orchestrator.py`. Read it first when in doubt.

**Two entry surfaces, one orchestrator.** The same `orchestrator.run(...)` is driven either
from the **CLI** (`main.py` → `duvo.orchestrator:main`) or the **web app** (`duvo/api/` —
FastAPI + a Vite/React SPA in `frontend/`). The web path adds auth, durable run-state, and a
live SSE feed, but never forks the pipeline — see the web conventions before touching `api/`.

## Where code goes

The package mirrors the `duvo.*` logger tree — one folder per concern:

| Layer | Path | Holds |
|---|---|---|
| Kernel | `duvo/config.py`, `models.py`, `agent_core.py` | env/constants, the Pydantic contract, the one agent runtime |
| Orchestration | `duvo/orchestrator.py` | semaphore-bounded `gather` → report; sets the event-feed context |
| Cross-cutting | `duvo/infra/` | `logging_setup.py`, `http_client.py`, `retry.py`, `tracing.py`, `events.py` (live-feed bus) |
| Shared agentic tools | `duvo/shared_agentic_tools/` | cross-agent tools such as `exa_tool.py` |
| Agents | `duvo/agents/` | scouts, analyst, router packages with local `prompts/` and `tools/` |
| Write-backs | `duvo/writeback/` | pluggable CRM / Slack / outreach adapters + dispatchers |
| Reporting | `duvo/reporting/` | sync HTML audit report |
| Durable state | `duvo/store/` | SQLite run-state: `db.py`, `runs.py`, `account_runs.py`, `events.py`, `queries.py`, `schema.sql` |
| Web API | `duvo/api/` | FastAPI: `app.py` (factory + SPA mount), `auth.py` (Google SSO + CSRF + #14 gate), `routes_runs.py` (run launch + REST + SSE), `jobs.py` (in-process task registry) |
| Frontend | `frontend/` | Vite + React + TS + Tailwind SPA (pnpm); built to `frontend/dist/`, served by the API |

Put new code in the folder that owns its concern. Extend an existing module before adding a new one. `main.py` is a thin shim → `duvo.orchestrator:main`; keep it thin.

## Non-negotiables

- **Async-first end-to-end.** Use `asyncio`, fan out with `asyncio.gather`, bound concurrency with `asyncio.Semaphore`, and prefer a library's native async client (Exa via `AsyncExa`, HTTP via `httpx.AsyncClient`) — never block the event loop. For a sync-only lib with no async client, offload via `asyncio.to_thread`. Reporting stays sync (pure CPU/file I/O).
- **One runtime.** Every agent runs on `agent_core.run_agent`. Do not hand-roll a model loop, call an LLM provider outside `duvo/llm/`, or call the Exa client outside its owning module. See the agent conventions.
- **One LLM boundary.** `duvo/llm/` owns all model-provider concerns: `agent_core` and the agents speak only the neutral types in `duvo/llm/base.py`. LiteLLM is a sanctioned dependency, but **only `duvo/llm/litellm_provider.py` may import it** — no wire format (OpenAI/Anthropic) leaks past that file. Add a model provider by dropping `duvo/llm/<name>_provider.py` and `@register_llm("<name>")`.
- **Config via env with defaults** in `config.py`. Guard a key with `require()` only when it is *always* needed before a live network call (Exa; provider-specific LLM keys are validated by the provider); leave write-back keys lazy so `--dry-run` and the offline tests work without a full `.env`.
- **Per-task isolation.** A failing unit (account / scout beat / write-back tool / persistence op) is logged and degrades to `None` / `[]` / a `failed: …` status — it never aborts the batch. See `_process_account`, `agents/scouts/isolation.py:run_scout_safe`, and `store/db.py` (every op degrades to a safe default).
- **Bounded agency.** Guarantees live in deterministic code, not prompts (see the agent conventions).
- **One tracing boundary.** Only `duvo/infra/tracing.py` may import `langfuse`, and it imports it lazily — tracing is OFF unless `LANGFUSE_ENABLED=true` with keys, so `--dry-run` and the offline tests never import it or hit the network. Instrument through `tracing.span()` / `tracing.trace_context()`; never import `langfuse` elsewhere.
- **One live-feed boundary.** Only `duvo/infra/events.py` owns the in-process event bus. Mirror the tracing on/off pattern: `events.publish()` is a **no-op when no one is subscribed**, so the CLI path and offline tests are unaffected. Producers tag events through the contextvar (`set_context`/`enrich_context`); never block the pipeline on a slow subscriber (bounded queue, drop-on-full). Events are bounded + redacted — a `tool` event never carries raw arguments.
- **Single-process web app.** Run the API with **`uvicorn --workers 1`**: the run/task registry (`api/jobs.py`) and the live event bus are in-process, so a second worker wouldn't see them. The SPA is served by the same process from `frontend/dist/` — one deployable, no separate web server.
- **Dry-run isolation in the store.** Persistence is real-runs-only on the CLI (`persist = not dry_run`) but **always on** from the web (`persist=True`, so dry-runs are observable too). Real-run queries (diff baseline, idempotency, `--skip-done-today`) filter `dry_run = 0` so a persisted dry-run never baselines, gates, or suppresses a real run.

> These rules are guidance Claude reads, like CLAUDE.md — not enforcement. Guaranteed behavior belongs in code, guards, and tests.

# Python style

> Applies when working in `**/*.py`.

Write code that reads like the file next to it — senior, plain, no speculative abstraction.

## Tooling

- **uv only** for dependencies: `uv add 'pkg>=x'`, `uv add --dev …`, `uv lock`, `uv sync`, `uv run …`. Never `pip`.
- Lint & format with **ruff** (config in `pyproject.toml`): line length 100, target `py311`, lints `E,W,F,I,N,UP`. `duvo` is first-party for import sorting. Run `uv run ruff check` / `ruff format` before claiming done.

## Conventions

- **Typing:** built-in generics (`list[str]`, `dict`, `X | None`); `from __future__ import annotations` where it removes quoting. Type every public signature.
- **Docstrings:** Google-style with `Args:` / `Returns:` / `Raises:` on public functions. Match the density already in `config.py:require` and `agent_core.run_agent` — terse private helpers can use a one-liner.
- **Logging:** `from duvo.infra.logging_setup import get_logger` then `_log = get_logger(__name__)`. Use lazy `%`-style args (`_log.info("x=%s", x)`), not f-strings. **Never log secrets** — API keys, tokens, webhook URLs. Logging company/domain/email is fine.

## Idioms to reuse (don't reinvent)

- **Lazy module-level singleton + `require()` at call time** for any external client, so the module imports without secrets and the key is validated only on real use. Canonical: `infra/http_client.py:get_client`, `shared_agentic_tools/exa_tool._get_exa`.
- **Defensive coercion over trust.** Inputs from the LLM or JSON are clamped/coerced before use, with a conservative fallback — never trusted to be in range. Canonical: `agents/analyst/guards.py:score_from_assessment_payload` (score clamp to 1–10, `_conservative_default`).

## Anti-bloat

Prefer extending an existing helper/module over adding a new one. No frameworks, no config layers, no abstractions for a single caller. If it isn't used today, don't build it.

# Agent conventions

> Applies when working in `duvo/agents/**`.

Every agent is the same runtime with a different prompt + toolset. Copy the shape of `scouts.py` / `analyst.py` / `router.py`.

## The runtime

- Drive the agent with `await run_agent(system, user, tools, impls, max_turns=…, final_tools={…}, log=…, max_tokens=…)`. Nothing in `agents/` calls an LLM provider directly — only `agent_core` does, and it speaks the neutral types in `duvo/llm/base.py` (never a wire format).
- **Tools** are `ToolSpec` objects built with `tool_schema(name, description, parameters)` from `duvo.llm.base` (provider-neutral; the provider translates them to wire format). **`impls`** maps tool name → callable (sync or async — `run_agent` auto-awaits awaitables). Reuse `EXA_SEARCH_TOOL` / `exa_search` from `shared_agentic_tools/exa_tool.py` for search.
- Name the terminating tool(s) in `final_tools` (e.g. `submit_signals`, `record_assessment`, `finish`). Capture its payload into a closure dict and read it back after the loop returns — the loop returns the transcript, not the result.
- Set `max_tokens` high enough for large terminal payloads (see `analyst.ANALYST_MAX_TOKENS = 4096`) so the final tool JSON isn't truncated.
- Pass the shared `log` list through so tool calls land in the audit report.

## Prompts are externalized

System prompts live with the agent that owns them: `duvo/agents/<agent>/prompts/<name>.md`, loaded by that package's local prompt loader with `{placeholder}` substitution. **Never inline a multi-line prompt string.** Keep the section layout: `# Role`, `## Objective`, `## Tools`, `## Guidelines`, `## When done`. To add an agent: drop its prompt under the new agent package's `prompts/` folder and expose a small local loader.

## Isolation

Wrap fan-out units so one failure returns `[]` and siblings survive — see `run_scout_safe`. Tolerate sloppy model calls (e.g. `submit_signals()` with no args → treat as "found nothing"), don't raise.

## Determinism over the model

Validation, score clamping, tiering, and safety gates are **plain code, not prompt instructions**:

- `analyst.apply_guards` — undated/thin evidence can't yield a confident high score; tier is derived from the score, overriding the model.
- score clamp to 1–10 + `_conservative_default` when the agent produces nothing usable.
- `router` Tier-1 guard fires **before** the lazy `import` of the send module — a non-confident account can't even load it.

Add any new bounded-agency rule the same way: as code the model cannot talk its way past.

# Write-back adapters

> Applies when working in `duvo/writeback/**`.

One interface, swappable providers. Copy `attio.py` / `brevo.py` for an adapter, `crm.py` / `outreach.py` for a dispatcher.

## Dispatcher pattern (`crm.py`, `outreach.py`)

- Read the provider from `config.<X>_PROVIDER` **at call time** (not import time) so tests can monkeypatch it.
- **Lazy-import** the chosen adapter inside the function: `from duvo.writeback import hubspot`.
- Unknown provider → `log.warning("unknown … — defaulting to <default>")` then fall back to the default adapter. Never raise on a bad provider value.
- Return the adapter's status string unchanged.

## Adapter pattern

- **Share the pooled client**: `client = http_client.get_client()` — one `httpx.AsyncClient`, one connection pool, central `HTTP_TIMEOUT_SECONDS`. Never construct an `AsyncClient` ad hoc.
- **Validate keys at call time** via a `_headers()` (or equivalent) helper using `require("KEY_NAME", config.KEY)`. Never validate at import — the module must import cleanly for `--dry-run` and offline tests.
- Build a payload **dict**, then `await client.post(url, json=payload, headers=_headers())`. Keep provider-specific shaping (Block Kit, Basic auth, search-then-upsert) inside that adapter.
- **Return a short human-readable status string** on success (e.g. `"contact queued in Brevo review list … (not sent — rep reviews & sends)"`).
- **On failure**: `log.error(…)` with company/domain context, then `raise_for_status()` / re-raise. The router catches it and records `failed: …`. Don't swallow.
- **Never log secrets** — keys/tokens go only into headers.

## Boundaries

- `dry_run` is handled at the **router** layer (`agents/router/router.py`), not inside adapters — adapters always do the real call.
- Outreach **queues for review** (Brevo list / paused lemlist campaign); it never auto-sends. Preserve that in any new outreach adapter.

# Testing conventions

> Applies when working in `tests/**`.

The suite is **fully offline** — no API keys, no network, no real model calls. Keep it that way. Test-first for new behavior.

## Layout & config

- Pytest with `asyncio_mode = "auto"` (in `pyproject.toml`): write plain `async def test_…`, **no `@pytest.mark.asyncio`**.
- One `test_<module>.py` per source module (`analyst.py` ↔ `test_analyst.py`).

## Mock at the boundaries

- **Model loop**: patch `duvo.agents.<mod>.run_agent` with an async fake that calls `impls[...]` directly to simulate the model's tool choices — never hit the configured LLM provider. (See the fakes in `test_scouts.py` / `test_router.py`.)
- **Search**: patch `duvo.shared_agentic_tools.exa_tool._get_exa`.
- **HTTP**: patch `duvo.infra.http_client.get_client` with `conftest.make_fake_async_client(...)`; assert on `client.post.call_args_list`.
- **Config**: swap providers/keys with `monkeypatch.setattr(config, "CRM_PROVIDER", …)` — never read real env.

## Reuse conftest factories

`make_score(**kw)`, `make_fake_async_client(post=…, get=…, patch=…)`, `_fake_response(status, json, …)`. Add module-local `_make_company` / `_make_signal` builders the way existing tests do.

## Cover the contract, not just the happy path

- **Safety guards don't mutate state**: a refused `slack_alert` / `outreach_queue` returns a `"refused …"` string *and* leaves `rr.slack_status == "skipped"`.
- **Guard fires before the lazy import** (no `ImportError` when the send module is absent).
- **Failures surface, don't crash**: adapter exception → status `startswith("failed:")`; one failing scout beat / account doesn't sink `scout_all` / the batch.
- **Malformed/partial LLM output** → conservative fallback (score coerced into 1–10, empty `OutreachDraft`, `_conservative_default`).
- **Pure functions** (`apply_guards`) get direct unit tests — no mocks.

Run `uv run pytest -q` and confirm green before claiming done.

# Web app conventions (API · durable store · SPA)

> Applies when working in `duvo/api/**`, `duvo/store/**`, `frontend/**`.

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
