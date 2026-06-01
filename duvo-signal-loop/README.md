# duvo-signal-loop

A team of tool-using AI agents that turns a target list of retail/CPG accounts into rep-ready
pipeline, against Duvo's stack (Exa, a CRM, Slack, an outreach tool).

## The agents
- **Scout agents (×4, concurrent)** — each owns a beat (ERP, hiring, M&A, pain), uses an
  `exa_search` tool, decides its own queries, returns only sourced signals.  The four scouts fan out
  via `asyncio.gather`; a failure in one beat is isolated — the other three still produce signals.
  Because `exa-py` is a sync-only library, `exa_search` offloads to a thread via
  `asyncio.to_thread` so the event loop stays responsive.
- **Analyst agent** — validates signals (can run its own verification searches before trusting a
  claim), scores ICP fit, drafts personalized outreach. A deterministic `apply_guards()` caps any
  hallucinated confidence, and the score is clamped to the valid 1–10 range before it is trusted.
- **Router agent** — decides how to action the account; its tools are the write-backs. They
  self-guard: Slack/outreach refuse anything but a confident Tier 1, and the guard fires *before*
  the write-back module is even imported.

Every agent runs on one shared async tool-use loop (`agent_core.run_agent`), backed by an
`AsyncAnthropic` client.  Write-backs (`crm`, `slack`, `outreach`) use a single shared
`httpx.AsyncClient` via `http_client.get_client()` — one connection pool for the whole process.

Python (`main.py`) only orchestrates the hand-offs.

## Pluggable CRM + outreach
Both write-back steps are one interface with two adapters each:
- `crm.upsert_account()` — **Attio** (default, instant self-serve free API) | **HubSpot** (`CRM_PROVIDER=hubspot`).
- `outreach.queue_lead()` — **Brevo** (default, free API, no card) | **lemlist** (`OUTREACH_PROVIDER=lemlist`).

Target Duvo's real stack (HubSpot + lemlist) by flipping the two env vars — no other change. An
unknown provider value logs a warning and falls back to the default.

## Setup
1. `python3.11 -m venv .venv && source .venv/bin/activate`
2. Runtime only: `pip install -r requirements.txt` — or, to run the tests too:
   `pip install -r requirements-dev.txt`
3. `cp .env.example .env`; fill Exa, Anthropic, `ATTIO_API_KEY` (keep `CRM_PROVIDER=attio`), Slack
   webhook, `BREVO_API_KEY` + `BREVO_LIST_ID` (keep `OUTREACH_PROVIDER=brevo`). See `.env.example`
   for exact, sourced steps.

Optional env knobs (all have sane defaults):

| Variable | Default | Purpose |
|---|---|---|
| `MAX_CONCURRENT_ACCOUNTS` | `5` | Account-level concurrency cap (semaphore bound) |
| `HTTP_TIMEOUT_SECONDS` | `30` | Per write-back HTTP request timeout |
| `ANTHROPIC_TIMEOUT_SECONDS` | `120` | Per Anthropic model-call timeout |
| `ACCOUNT_TIMEOUT_SECONDS` | `300` | Wall-clock timeout per account (prevents a single hung account stalling the batch) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

## Run
- `python main.py --dry-run` — agents run and really decide; write-back tools simulate (safe demo fallback).
- `python main.py --limit 3` — first 3 accounts only (fast live demo).
- `python main.py` — full concurrent loop with write-backs. Audit log: `output/run-report.html`.
- `python main.py --log-level DEBUG` — verbose logs (every turn, tool call, and guard decision).
- `python main.py --concurrency 3` — override `MAX_CONCURRENT_ACCOUNTS` for this run.

> Note: even `--dry-run` makes **real** Exa + Anthropic calls (only the write-backs are simulated),
> so it needs valid `EXA_API_KEY` and `ANTHROPIC_API_KEY`.

## Async architecture
The entire pipeline is async end-to-end:

- **Orchestrator** (`main.py`) — `async def run(...)` fans out all accounts concurrently via
  `asyncio.gather`, bounded by `asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)`.  Each account runs
  in its own `_process_account` coroutine so a single failing account logs an error and yields
  `None`; it never aborts the batch.  The shared `httpx.AsyncClient` is always closed via
  `await http_client.aclose()` in a `finally` block — even if every account fails.
- **Scouts** — four beats fan out via `asyncio.gather` inside `scout_all`; `exa_search` uses
  `asyncio.to_thread` to keep the loop unblocked.
- **Analyst / Router** — `async def run_analyst` / `async def run_router` each drive the shared
  `async run_agent` loop; write-backs are awaited async `httpx` calls.
- **Reporter** — `generate_report(results)` stays **sync** (pure CPU + file I/O, no network);
  calling it from an async context is safe and correct.
- **Entry point** — `asyncio.run(run(...))`.

## Logging
Every module logs through a single `duvo.*` logger tree configured by `logging_setup.configure_logging()`
(called at startup, level set by `--log-level` / `LOG_LEVEL`). You get a timestamped, levelled trace
of each agent turn, every tool call, every guard decision, and every write-back result — enough to
debug a run from the console alone. Secrets (API keys, the Slack webhook URL) are never logged.

## Tests
The whole system is built test-first and runs offline — every external dependency (Anthropic, Exa,
and all HTTP write-backs) is mocked, so the suite needs no API keys and makes no network calls.

```bash
python -m pytest -q          # 278 tests, ~1s
```

Coverage includes: the model contract (enforced via Pydantic `Literal`/range constraints), the
async tool-use loop (incl. unknown-tool and tool-error paths), scout parallelism + per-beat
isolation, analyst score-clamping and the deterministic guards, the router's never-send safety guard
(proven to fire *before* the lazy import), every CRM/Slack/outreach adapter's request shaping and
error handling, the orchestrator (concurrent gather, semaphore, per-account isolation,
`http_client.aclose` in finally, `--concurrency` flag), and HTML report rendering.

## Where agency is bounded (deliberate human-in-the-loop)
- Scouts may not invent; the analyst independently verifies; undated signals are discarded.
- `apply_guards()` — not the model — caps low-confidence high scores and flags thin accounts; the
  score is clamped to 1–10 so a hallucinated number can never crash or skew a run.
- The router never sends: outreach leads land in a **review list** (Brevo) / **paused** campaign
  (lemlist) a rep approves; Slack/outreach tools refuse non-confident-Tier-1 accounts even if the
  agent asks — and the guard short-circuits before the send module loads.

## Where it breaks
- Exa noise/staleness on big brands (scout iteration + analyst verification mitigate; recall limited).
- Agent loops add latency/variance; bounded by turn caps. Demo runs a few accounts live, full list pre-run.
- No real person-level email — persona recommended; demo uses a test email as the lead.
- One-shot run; production = scheduled run + score diff + alert only on change. No CRM dedup yet
  (re-runs create new records; production would assert/upsert).

## Production notes
- **Bounded concurrency** — `asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)` prevents thundering-herd
  against Anthropic and Exa rate limits; tune via env var or `--concurrency`.
- **Pooled async HTTP client** — one `httpx.AsyncClient` with `HTTP_TIMEOUT_SECONDS` timeout shared
  across all write-back calls; gracefully closed in the orchestrator `finally` block.
- **Per-account isolation** — a failed account (network error, model timeout, bad JSON) is logged
  and excluded from the report; it never cancels other accounts or raises out of `run()`.
- **Next steps** — per-account retry with exponential back-off; Anthropic rate-limit handling;
  structured CRM dedup (upsert on domain); score-diff alerting on re-runs.

## What I'd build next (one week)
Scouts as MCP-tool agents (Apollo/LinkedIn/Gong) · a discovery agent for net-new accounts · a Gong
call-outcome agent writing back to the CRM · a reply-handling agent branching the outreach sequence
on intent · promote the ICP score to a structured CRM attribute. The `run_agent` async runtime
stays; only toolsets grow.

## Project layout
```
duvo-signal-loop/
├── config.py              # env + model + constants (incl. MAX_CONCURRENT_ACCOUNTS, HTTP/ANTHROPIC timeouts)
├── models.py              # Pydantic contract shared across agents
├── logging_setup.py       # central duvo.* logger
├── agent_core.py          # async run_agent() tool-use loop (AsyncAnthropic client)
├── http_client.py         # shared pooled httpx.AsyncClient; get_client() + async aclose()
├── tools/exa_tool.py      # exa_search tool (asyncio.to_thread for sync exa-py)
├── scouts.py              # 4 concurrent scout agents (asyncio.gather fan-out)
├── analyst.py             # async analyst agent + apply_guards()
├── router.py              # async router agent; write-backs as self-guarding async tools
├── writeback/
│   ├── crm.py             # async CRM dispatcher (attio|hubspot)
│   ├── attio.py / hubspot.py
│   ├── slack.py           # async Tier-1 #sales alert
│   ├── outreach.py        # async outreach dispatcher (brevo|lemlist)
│   └── brevo.py / lemlist.py
├── reporter.py            # sync HTML audit report (pure CPU/file — no async needed)
├── templates/report.html
├── main.py                # async orchestrator: semaphore-bounded gather → report
├── tests/                 # pytest suite (fully mocked, no keys/network, asyncio_mode=auto)
└── output/                # generated reports (gitignored)
```
