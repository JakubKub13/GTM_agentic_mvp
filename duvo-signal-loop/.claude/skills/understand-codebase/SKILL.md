---
name: understand-codebase
description: >-
  Use when a new session needs to understand the duvo-signal-loop codebase — its
  architecture, the scouts → analyst → router agent pipeline, the shared async
  tool-use runtime, the write-back adapters, or the bounded-agency safety design —
  before answering questions about it or changing it. Trigger whenever someone asks
  to "understand the codebase", "get up to speed", "onboard", "explain how this
  works / how the agents work", "walk me through the pipeline", OR before any
  non-trivial change to agent_core, scouts/analyst/router, the writeback adapters,
  or the orchestrator — even if they never say the word "skill" or "architecture".
  Prefer this over ad-hoc grepping: it gives the correct reading order and the load-
  bearing mental model so you reach deep understanding fast and don't miss the
  safety invariants.
---

# Understanding the duvo-signal-loop codebase

This skill gets you to a deep, accurate understanding of this project quickly and
in the right order — so you can answer questions or make changes without missing
the architectural spine or the safety invariants that hold the system together.

**Read the real code, don't just trust this skill.** The code is the source of
truth and it evolves; this skill tells you *what to read, in what order, and what
to look for*, plus the load-bearing mental model. Treat `references/architecture.md`
as a map to verify against the files — if it disagrees with the code, the code wins
(and the map is stale — flag it).

## The one idea to hold onto

Everything rests on a single reusable primitive: **`agent_core.run_agent()`**, one
async provider-neutral tool-use loop backed by the configured `LLMProvider`
(LiteLLM by default). Every agent role — 4 scout beats, 1 analyst, 1 router — is
just a different `(system_prompt, tools, impls)` triple fed into that same loop.
Per account the flow is **scouts → analyst → router**. The project's guiding
principle is **bounded agency**: the LLM proposes, but every *consequential*
decision (scoring, tiering, sending) is pulled out of the model into deterministic,
testable Python guards. Keep both of those in mind and the rest of the code reads
as variations on them.

## Reading order (do this, in this order)

Read bottom-up: contract and runtime first, then the agents that ride on them, then
orchestration. Each step says *why it comes here* and *what to extract*. Open the
files directly — most are short. The runtime lives under the `duvo/` package; `main.py`
at the repo root is a thin shim that calls `duvo.orchestrator.main`.

1. **`README.md`** — the author's own framing, the architecture diagram, the
   "where agency is bounded" and "where it breaks" sections. Orients you fast.
2. **`duvo/config.py`** — every env var, the `require()` pattern (required keys raise;
   write-back keys validated lazily so `--dry-run` works without a full `.env`),
   the LLM provider/model selection, the concurrency/timeout knobs, and `DUVO_DB_PATH`
   (the durable run-state SQLite store, default `state/duvo.db`). *Extract:* what's
   required vs optional, and the tunable limits.
3. **`duvo/models.py`** — the Pydantic contract shared by all agents. *Extract:* the 4
   signal-type `Literal`s, `ICPScore.score = Field(ge=1, le=10)`, the tier/confidence
   `Literal`s. These constraints are *why* the analyst defends so hard downstream.
4. **`duvo/agent_core.py`** — the heart. Read `run_agent` line by line. *Extract:* the
   loop (call → dispatch tool_use → feed results → repeat to a final tool or
   `max_turns`); sync/async tool polymorphism via `inspect.isawaitable`; errors
   and unknown tools become tool results instead of crashing; the provider-neutral
   `LLMRequest`/`Message` boundary and lazy `get_llm_provider()` lookup; the `log`
   list that captures every tool call.
5. **`duvo/infra/`** — the cross-cutting layer: `http_client.py` (shared pooled
   `httpx.AsyncClient` singleton, drained in the orchestrator's `finally`), `logging_setup.py`
   (single `duvo.*` logger tree — idempotent, `propagate=False`, secrets never logged),
   `retry.py` (`with_retries()` — transient-failure backoff around write-back POSTs), and
   `tracing.py` (the lazy Langfuse boundary — OFF unless `LANGFUSE_ENABLED`; the only module
   that imports `langfuse`, so `--dry-run` and the offline tests stay key/network-free).
6. **`duvo/shared_agentic_tools/exa_tool.py`** — the one search tool both scouts and analyst use.
   *Extract:* the native `AsyncExa` client is awaited directly to keep the loop responsive;
   results are formatted to a compact string; failures return a string, not an exception.
7. **`duvo/agents/scouts/scouts.py`** — 4 concurrent scout agents, with prompt copy in
   **`duvo/agents/scouts/prompts/scout.md`**. *Extract:* the 4 `BEATS`
   (matching the signal `Literal`s), `asyncio.gather` fan-out, `run_scout_safe`
   per-beat isolation, the "never invent / sourced only" prompt, and that
   `signal_type` is set by the beat, not the model.
8. **`duvo/agents/analyst/analyst.py`** — the defensive core. *Extract:* the ICP definition;
   prompt copy in **`duvo/agents/analyst/prompts/analyst.md`**; that the model may run verification searches; and especially the two-layer
   defense — (a) per-field coercion/clamping before constructing `ICPScore`, and (b)
   `apply_guards()`, a pure deterministic function that overrides the model's tier and
   caps hallucinated confidence. Read `apply_guards` rules in order; this is half the
   "bounded agency".
9. **`duvo/agents/router/router.py`** — the never-send safety layer. *Extract:* `confident_t1`
   computed in code (not by the model); prompt copy in **`duvo/agents/router/prompts/router.md`**;
   the self-guarding tools where the safety check
   runs *before* the lazy `import` and any `await`; that nothing is ever auto-sent
   (queue / paused only); and `dry_run` returning `[dry-run] …` strings.
10. **`duvo/writeback/`** — the pluggable stack. Read `crm.py` and `outreach.py`
    (dispatchers: provider read at call time, unknown → warn + default), then one
    adapter pair (`attio.py`, `brevo.py`) for the payload-fallback robustness pattern.
    `slack.py`, `hubspot.py`, `lemlist.py` follow the same shape. *Extract:* both CRM
    adapters now **dedup by domain** — `attio.py` and `hubspot.py` search for an existing
    record by domain, then PATCH it or create one (`_upsert_company`), so re-runs no
    longer create duplicate records.
11. **`duvo/orchestrator.py`** (entry shim: `main.py`) — the conductor. *Extract:*
    `asyncio.gather` over all accounts, `Semaphore` bound, `_process_account`'s triple
    isolation (semaphore + `asyncio.timeout` + try/except → `None`), and
    `http_client.aclose()` in `finally`. Also (the DB/dedup work): on real runs it
    persists run/account state via `duvo.store` and writes a per-channel write-back
    **event ledger with a score/tier diff** (`_persist_success`), and supports
    `--resume <run_id>` / `--skip-done-today` to skip already-`done` domains. Dry-runs
    never persist (`persist = not dry_run`).
12. **`duvo/store/`** — the durable run-state layer (SQLite), added with the DB/dedup
    work. One file per concern like `infra/`: `db.py` (sync `sqlite3` offloaded via
    `asyncio.to_thread`, WAL + `busy_timeout`, schema-on-connect, and **every op degrades
    to a safe default on error so persistence can never abort the pipeline**), plus
    `runs.py` / `account_runs.py` / `events.py` over the three tables in `schema.sql`
    (`runs`, `account_runs`, `writeback_events`). *Extract:* `last_done_for_domain`
    (feeds the diff), `done_domains_for_run` / `done_domains_for_date` (feed `--resume` /
    `--skip-done-today`), and `derive_action` (status string → ledger verb).
13. **`duvo/reporting/reporter.py`** + **`duvo/reporting/templates/report.html`** — the
    only deliberately *sync* module (pure CPU/file I/O) and the audit dashboard it
    renders (run-scoped under `output/run_reports/`; the score diff is not yet surfaced here).
14. **`tests/` (skim)** — confirm the contracts. Start with `test_agent_core.py` (loop
    edge cases incl. falsy-return non-await) and `test_router.py` (the
    guard-fires-before-lazy-import proof via `sys.modules`). The pattern across
    agent tests: patch `run_agent` itself with a fake that calls a chosen tool
    sequence — so logic is tested deterministically without simulating model turns.
    The durable store (tables, `--resume` / `--skip-done-today` skip logic, the
    write-back diff) is covered too.

## After reading: the mental model to confirm you have

You understand the codebase when you can explain, from the code:

- **One runtime, three agent roles.** How `run_agent` powers four scout beat runs,
  the analyst, and the router identically, and what each role's tools and final
  tool are.
- **The three deterministic safety gates** and where each lives: scouts can't invent
  (prompt + `source_url` required); `apply_guards()` overrides scoring/tiering; the
  router's tool guards refuse non-confident-Tier-1 *before importing the send module*,
  and nothing auto-sends.
- **The async isolation layers** stacked so no single failure sinks a run: per-beat
  (`run_scout_safe`), per-account (semaphore + timeout + try/except → `None`), pooled
  HTTP closed in `finally`, bounded concurrency.
- **The pluggable seam:** how `CRM_PROVIDER` / `OUTREACH_PROVIDER` swap adapters behind
  one interface, with lazy import and default-fallback.
- **Durable state & idempotent re-runs:** how `duvo.store` (SQLite) records every run,
  account outcome, and write-back event; how CRM adapters dedup by domain; and how
  `--resume` / `--skip-done-today` make re-runs safe and crash-recoverable. Persistence
  is best-effort (degrades to a safe default) and dry-runs never persist.
- **The honest limits** (Exa noise, agent-loop latency, test email; and what's still out
  of scope — score-unchanged suppression of Slack/outreach, surfacing the diff in the
  HTML report, a Postgres backend) and the roadmap.

If you can walk all six of those without re-opening files, you're at the target depth.
For the full synthesized breakdown — module responsibilities, the guard rules spelled
out, the data contract, testing philosophy, and known limits — read
`references/architecture.md` (resolve it relative to this `SKILL.md`). Use it to go
deep on one area or to check your understanding, not as a substitute for reading the code.

## How to report your understanding

When asked to explain the codebase, lead with the one idea (one runtime + bounded
agency), then the per-account flow, then the safety gates — that ordering mirrors how
the system is actually built and lands fastest. Reference files as `path:line` so the
reader can jump straight to the code.
