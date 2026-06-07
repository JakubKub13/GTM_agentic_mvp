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
