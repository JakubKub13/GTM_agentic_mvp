# duvo-signal-loop — Production-Readiness Findings

> Senior-engineer review of what the codebase is missing to run **unattended in production**.
> Framing: this is a **batch, one-shot CLI pipeline** — not a long-running service. The review
> deliberately skips things that don't apply at this shape (health endpoints, autoscaling, a web
> framework) and focuses on what genuinely blocks a real recurring run. The codebase is already
> strong where most demos are weak: bounded concurrency, per-unit isolation, retries with backoff,
> a clean LLM provider seam, and 295 offline tests.

---

## Tier 1 — blocks a trustworthy recurring run

### 1. Observability / tracing (Langfuse) — missing entirely
Today there's a `duvo.*` logger tree and a per-run HTML report, but nothing giving per-account
latency, token usage, cost, or a replayable trace of each agent's turns across runs. Critically,
`litellm_provider._from_wire_response` (`duvo/llm/litellm_provider.py:77`) **throws away
`resp.usage`** — cost/token data is already on the floor.

**How:** Capture `resp.usage` into `LLMResponse` (add a `usage` field to the dataclass in
`duvo/llm/base.py`), then wrap the one chokepoint — `run_agent` in `duvo/agent_core.py` — with a
Langfuse `@observe` span per agent, nesting tool calls as child spans. Because every agent rides one
loop, you instrument **one function** and get traces for scouts/analyst/router for free. Pass account
name + beat as trace metadata. Gate behind a `LANGFUSE_ENABLED` env flag so `--dry-run` / offline
tests stay network-free. Use the Langfuse SDK directly behind that one seam — don't build a custom
tracing abstraction.

### 2. Evals — missing, and the architecture is begging for it
All 295 tests mock `run_agent`, so they verify *plumbing* (guards fire, payloads parse) but never
*agent quality* (does the analyst score a known-good account as Tier 1? do scouts return real sourced
signals?). With no eval harness you can't safely change a prompt or swap models — the README invites
model swapping but gives no way to detect a regression.

**How:** A small `evals/` dir: a fixtures file of ~10–15 labeled companies with expected
tier/score-range/persona; run the *real* pipeline against them (or replayed Exa fixtures for
determinism/cost) and assert with tolerances (tier exact-match rate ≥ X, score within ±2, outreach
non-empty). LLM-as-judge for the outreach drafts. Wire it as a separate `uv run python -m evals`
command, **not** in the unit suite (it costs money/network). If you adopt Langfuse, use its
datasets/scores so evals and traces share one home.

---

## Tier 2 — correctness gaps for re-runs

### 3. No idempotency / CRM dedup — the biggest correctness hole
The README admits it: re-running creates duplicate CRM records, duplicate Slack alerts, duplicate
queued leads. Fine for a one-shot demo; for the "scheduled run" the roadmap describes, it's a
data-integrity bug that pollutes the rep's CRM on day two.

**How:** Make `crm.upsert_account` a true upsert keyed on `domain` (Attio assert-by-matching-attribute;
HubSpot search-then-update). For Slack/outreach, the durable run-state from #4 is the dedup key — only
alert if the score *changed* since last run (the roadmap's "score-diff alerting"). No queue/event bus
needed; a keyed upsert is enough.

### 4. No durable run state / persistence
Output exists only as ephemeral HTML in a gitignored `output/`. There's no record of "what score did
account X get last week," which blocks both score-diff alerting and resumability after a crash
mid-batch.

**How:** Append each `RunResult` to a JSONL (or a tiny SQLite table) keyed by `(domain, run_date)`
right after `_process_account` returns — a dozen lines in the orchestrator. That single store unlocks
dedup (#3), diff-alerting, and a cheap eval/replay corpus. Resist a real DB/migrations until volume
demands it.

### 5. No cost / spend guardrail
Concurrency is bounded (`Semaphore`) but **spend is not**. A bad `companies.csv` of 5,000 rows ×
(4 scouts + analyst + router) × multi-turn loops is an unbounded LLM/Exa bill with no circuit breaker.
Turn caps limit *per-agent* turns but nothing caps the *run*.

**How:** Once #1 captures token usage, accumulate cost in the orchestrator and abort (or warn) past a
`MAX_RUN_COST_USD` env ceiling. ~15 lines of cheap insurance.

---

## Tier 3 — delivery hygiene

### 6. No CI
There's `.gitignore`, ruff config, `pyrightconfig.json`, and a full test suite — but **no
`.github/workflows/`**, so none of it runs on push. "295 passing" is a manual claim.

**How:** One GitHub Actions workflow: `uv sync` → `ruff check` → `ruff format --check` → `pyright`
(configured but never run in CI) → `pytest`. Half a day, high leverage — the gate that keeps
everything else honest.

### 7. No deployment / scheduling artifact
Production = "scheduled run" per the README, but there's no Dockerfile and no cron/scheduler
definition. It only runs from a laptop today.

**How:** A slim Dockerfile (`uv sync --frozen`, entrypoint `python main.py`) plus whatever scheduler
the infra uses (GitHub Actions `schedule:`, a k8s CronJob, or Cloud Run Job). Pick one; don't build a
scheduler.

### 8. Failure visibility is passive
A failed account is logged and dropped to `None` (`duvo/orchestrator.py:71`) — correct for isolation,
but in an unattended scheduled run no human reads stderr. A run where 40% of accounts silently failed
looks identical to a clean one.

**How:** Track failures in `run()` and surface a one-line summary — into the HTML report header and,
if non-zero, a single Slack message ("batch done: 47 ok, 12 failed"). Reuse the existing `slack.py`
adapter; no new infra.

---

## Deliberately NOT recommending (avoiding overengineering)

- Web framework / health checks / readiness probes — it's a batch job, not a service.
- A secrets manager — `.env` + lazy `require()` is fine at this scale; revisit with a real deploy target.
- Forcing structured output / a model-loop rewrite — the `conservative_default` fallback already
  handles a missing `record_assessment` gracefully.
- A message queue, Kubernetes, or microservices — `asyncio.gather` + semaphore is correct until
  volume is orders of magnitude higher.

---

## Suggested one-week ordering

1. **CI (#6)** first — the safety net.
2. **Tracing + token capture (#1)** — instrument the one `run_agent` seam.
3. **JSONL run-state (#4)** — unlocks dedup (#3), diff-alerting, *and* the eval corpus (#2) in one stroke.
4. **Evals (#2)** on top of that corpus.

This gets the project from "great demo" to "safely runs unattended every morning" with the least new
surface area.
