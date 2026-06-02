<div align="center">

# 🛰️ duvo-signal-loop

**A team of tool-using AI agents that turns a target list of retail/CPG accounts into rep-ready pipeline** — scouting intent signals, scoring ICP fit, and routing accounts into your sales stack.

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Async](https://img.shields.io/badge/async-asyncio-1F6FEB)](https://docs.python.org/3/library/asyncio.html)
[![httpx](https://img.shields.io/badge/HTTP-httpx-0A7E8C)](https://www.python-httpx.org/)
[![Claude](https://img.shields.io/badge/LLM-Claude-D97757?logo=anthropic&logoColor=white)](https://www.anthropic.com/)
[![Tests](https://img.shields.io/badge/tests-295%20passing-3FB950)](#-tests)
[![Style](https://img.shields.io/badge/built-test--first-8957E5)](#-tests)

</div>

---

## ✨ Highlights

- 🤖 **Multi-agent by design** — scouts → analyst → router, each a real tool-using agent on one shared loop.
- ⚡ **Async-first & production-ready** — `asyncio` end-to-end, pooled `httpx` client, bounded concurrency, timeouts at every layer.
- 🔌 **Pluggable stack** — swap CRM (Attio ⇄ HubSpot) and outreach (Brevo ⇄ lemlist) with a single env var.
- 🛡️ **Bounded agency** — deterministic guards, score clamping, and a router that **never sends** without a human in the loop.
- 🧪 **Fully tested offline** — 295 mocked tests, no API keys or network required.
- 📊 **Self-documenting runs** — every run renders an HTML audit report of decisions and agent tool calls.

---

## 🗺️ Architecture

```mermaid
flowchart LR
    CSV([📄 companies.csv]) --> ORCH[🎯 main.py →<br/>duvo.orchestrator]
    ORCH -- Semaphore-bounded<br/>asyncio.gather --> PIPE

    subgraph PIPE [per-account pipeline]
        direction TB
        S[🔎 4× Scout agents<br/>asyncio.gather] --> A[🧠 Analyst agent<br/>+ apply_guards]
        A --> R[🚦 Router agent]
    end

    R -- crm_upsert --> CRM[(🗂️ Attio / HubSpot)]
    R -- slack_alert --> SL[💬 Slack #sales]
    R -- outreach_queue --> OUT[✉️ Brevo / lemlist]
    PIPE --> REP[📊 HTML report]
```

Every agent runs on one shared async tool-use loop (`duvo.agent_core.run_agent`) backed by an **`AsyncAnthropic`** client. Write-backs (`crm`, `slack`, `outreach`) share a single **`httpx.AsyncClient`** via `duvo.infra.http_client.get_client()` — one connection pool for the whole process. `duvo.orchestrator` only orchestrates the hand-offs (reached through the thin `main.py` entry shim).

---

## 🤝 The agents

| Agent | Role |
|---|---|
| 🔎 **Scout** (×4, concurrent) | Each owns a beat (ERP · hiring · M&A · pain), uses an `exa_search` tool, decides its own queries, and returns **only sourced signals**. The four fan out via `asyncio.gather`; one failing beat is isolated so the others still produce signals. Since `exa-py` is sync-only, `exa_search` offloads to a thread via `asyncio.to_thread` to keep the event loop responsive. |
| 🧠 **Analyst** | Validates signals (can run its own verification searches before trusting a claim), scores ICP fit, and drafts personalized outreach. A deterministic `apply_guards()` caps any hallucinated confidence, and the score is clamped to the valid **1–10** range before it is trusted. |
| 🚦 **Router** | Decides how to action the account — its tools *are* the write-backs. They self-guard: Slack/outreach refuse anything but a confident Tier 1, and the guard fires **before** the write-back module is even imported. |

---

## 🔌 Pluggable CRM + outreach

One interface, two adapters each — point at Duvo's real stack by flipping a single env var, no other change. An unknown provider value logs a warning and falls back to the default.

| Step | Default 🟢 | Alternative | Switch |
|---|---|---|---|
| `crm.upsert_account()` | **Attio** — instant self-serve free API | **HubSpot** | `CRM_PROVIDER=hubspot` |
| `outreach.queue_lead()` | **Brevo** — free API, no card | **lemlist** | `OUTREACH_PROVIDER=lemlist` |

---

## 🚀 Setup

This project uses **[uv](https://docs.astral.sh/uv/)** — a fast, modern Python package manager.

```bash
# 1. install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. sync the environment (creates .venv + installs all dependencies)
uv sync

# 3. configure secrets
cp .env.example .env
```

Fill `.env` with **Exa**, **Anthropic**, `ATTIO_API_KEY` (keep `CRM_PROVIDER=attio`), the **Slack** webhook, and `BREVO_API_KEY` + `BREVO_LIST_ID` (keep `OUTREACH_PROVIDER=brevo`). See `.env.example` for exact, sourced steps.

### 🛠️ Adding or updating dependencies

```bash
# Add a production dependency
uv add 'package-name>=1.0'

# Add a development dependency
uv add --dev 'pytest-plugin'

# Update lockfile (commits to uv.lock)
uv lock

# Sync after dependency changes
uv sync
```

### ⚙️ Optional env knobs

All have sane defaults:

| Variable | Default | Purpose |
|---|---|---|
| `MAX_CONCURRENT_ACCOUNTS` | `5` | Account-level concurrency cap (semaphore bound) |
| `HTTP_TIMEOUT_SECONDS` | `30` | Per write-back HTTP request timeout |
| `ANTHROPIC_TIMEOUT_SECONDS` | `120` | Per Anthropic model-call timeout |
| `ACCOUNT_TIMEOUT_SECONDS` | `300` | Wall-clock timeout per account (stops one hung account stalling the batch) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## ▶️ Run

Run with `uv run` (dependencies are automatically in scope) or after activating `.venv/bin/activate`:

| Command | What it does |
|---|---|
| `uv run python main.py --dry-run` | Agents run and **really decide**; write-back tools simulate (safe demo fallback) |
| `uv run python main.py --limit 3` | First 3 accounts only (fast live demo) |
| `uv run python main.py` | Full concurrent loop with write-backs → `output/run-report.html` |
| `uv run python main.py --concurrency 3` | Override `MAX_CONCURRENT_ACCOUNTS` for this run |
| `uv run python main.py --test-email you@example.com` | Use your own inbox for the queued outreach lead (overrides `TEST_EMAIL`) |
| `uv run python main.py --log-level DEBUG` | Verbose logs (every turn, tool call, and guard decision) |

> ⚠️ **Note:** even `--dry-run` makes **real** Exa + Anthropic calls (only the write-backs are simulated), so it needs valid `EXA_API_KEY` and `ANTHROPIC_API_KEY`.

---

## ⚡ Async architecture

The entire pipeline is async end-to-end:

- 🎯 **Orchestrator** (`duvo.orchestrator`, reached via the `main.py` entry shim) — `async def run(...)` fans out all accounts concurrently via `asyncio.gather`, bounded by `asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)`. Each account runs in its own `_process_account` coroutine, so a single failure logs an error and yields `None` without aborting the batch. The shared `httpx.AsyncClient` is always closed via `await http_client.aclose()` in a `finally` block — even if every account fails.
- 🔎 **Scouts** — four beats fan out via `asyncio.gather` inside `scout_all`; `exa_search` uses `asyncio.to_thread` to keep the loop unblocked.
- 🧠🚦 **Analyst / Router** — `async def run_analyst` / `async def run_router` each drive the shared `async run_agent` loop; write-backs are awaited async `httpx` calls.
- 📊 **Reporter** — `generate_report(results)` stays **sync** (pure CPU + file I/O, no network); calling it from an async context is safe and correct.
- 🏁 **Entry point** — `asyncio.run(run(...))`.

---

## 🧾 Logging

Every module logs through a single `duvo.*` logger tree configured by `duvo.infra.logging_setup.configure_logging()` (called at startup; level set by `--log-level` / `LOG_LEVEL`). You get a timestamped, levelled trace of each agent turn, every tool call, every guard decision, and every write-back result — enough to debug a run from the console alone. **Secrets** (API keys, the Slack webhook URL) are never logged.

---

## 🧪 Tests

Built test-first and runs **fully offline** — every external dependency (Anthropic, Exa, and all HTTP write-backs) is mocked, so the suite needs no API keys and makes no network calls.

```bash
# Run tests with uv
uv run pytest -q          # 295 tests, ~1s

# Or activate the venv first, then run pytest directly
source .venv/bin/activate && pytest -q
```

Coverage includes:

- ✅ the model contract (enforced via Pydantic `Literal`/range constraints)
- ✅ the async tool-use loop (incl. unknown-tool and tool-error paths)
- ✅ scout parallelism + per-beat isolation
- ✅ analyst score-clamping and the deterministic guards
- ✅ the router's never-send safety guard — proven to fire *before* the lazy import
- ✅ every CRM/Slack/outreach adapter's request shaping and error handling
- ✅ the orchestrator (concurrent gather, semaphore, per-account isolation, `http_client.aclose` in `finally`, `--concurrency` flag)
- ✅ HTML report rendering

---

## 🛡️ Where agency is bounded (deliberate human-in-the-loop)

- 🚫 Scouts may not invent; the analyst independently verifies; undated signals are discarded.
- 🎚️ `apply_guards()` — **not the model** — caps low-confidence high scores and flags thin accounts; the score is clamped to 1–10 so a hallucinated number can never crash or skew a run.
- 📮 The router **never sends**: outreach leads land in a **review list** (Brevo) / **paused** campaign (lemlist) a rep approves; Slack/outreach tools refuse non-confident-Tier-1 accounts even if the agent asks — and the guard short-circuits before the send module loads.

---

## 🧩 Where it breaks (honest limits)

- 🌫️ Exa noise/staleness on big brands (scout iteration + analyst verification mitigate; recall limited).
- ⏱️ Agent loops add latency/variance; bounded by turn caps. Demo runs a few accounts live, full list pre-run.
- 📧 No real person-level email — persona recommended; demo uses a test email as the lead.
- 🔁 One-shot run; production = scheduled run + score diff + alert only on change. No CRM dedup yet (re-runs create new records; production would assert/upsert).

---

## 🏭 Production notes

- **Bounded concurrency** — `asyncio.Semaphore(MAX_CONCURRENT_ACCOUNTS)` prevents thundering-herd against Anthropic/Exa rate limits; tune via env var or `--concurrency`.
- **Pooled async HTTP client** — one `httpx.AsyncClient` with `HTTP_TIMEOUT_SECONDS` timeout shared across all write-backs; gracefully closed in the orchestrator `finally`.
- **Per-account isolation** — a failed account (network error, model/account timeout, bad JSON) is logged and excluded from the report; it never cancels other accounts or raises out of `run()`.
- **Next steps** — per-account retry with exponential back-off · Anthropic rate-limit handling · structured CRM dedup (upsert on domain) · score-diff alerting on re-runs.

---

## 🛣️ Roadmap (what I'd build next, ~1 week)

Scouts as MCP-tool agents (Apollo / LinkedIn / Gong) · a discovery agent for net-new accounts · a Gong call-outcome agent writing back to the CRM · a reply-handling agent branching the outreach sequence on intent · promoting the ICP score to a structured CRM attribute. **The `run_agent` async runtime stays; only toolsets grow.**

---

## 📂 Project layout

```text
duvo-signal-loop/
├── duvo/                       # application package (mirrors the duvo.* logger tree)
│   ├── config.py               # env + model + constants (concurrency + HTTP/Anthropic/account timeouts)
│   ├── models.py               # Pydantic contract shared across agents
│   ├── agent_core.py           # async run_agent() tool-use loop (AsyncAnthropic client)
│   ├── orchestrator.py         # async orchestrator: semaphore-bounded gather → report
│   ├── infra/                  # cross-cutting infrastructure
│   │   ├── logging_setup.py    # central duvo.* logger
│   │   └── http_client.py      # shared pooled httpx.AsyncClient — get_client() + async aclose()
│   ├── tools/
│   │   └── exa_tool.py         # exa_search tool (asyncio.to_thread for sync exa-py)
│   ├── agents/
│   │   ├── scouts.py           # 4 concurrent scout agents (asyncio.gather fan-out)
│   │   ├── analyst.py          # async analyst agent + apply_guards()
│   │   ├── router.py           # async router agent; write-backs as self-guarding async tools
│   │   └── agent_prompts/      # externalized system prompts as Markdown + load_prompt() loader
│   │       ├── scout.md / analyst.md / router.md
│   │       └── __init__.py     # load_prompt(name, **params) — reads <name>.md, fills {placeholders}
│   ├── writeback/
│   │   ├── crm.py              # async CRM dispatcher (attio | hubspot)
│   │   ├── attio.py / hubspot.py
│   │   ├── slack.py            # async Tier-1 #sales alert
│   │   ├── outreach.py         # async outreach dispatcher (brevo | lemlist)
│   │   └── brevo.py / lemlist.py
│   └── reporting/
│       ├── reporter.py         # sync HTML audit report (pure CPU/file — no async needed)
│       └── templates/report.html
├── main.py                     # thin entry shim → duvo.orchestrator:main (keeps `python main.py` working)
├── companies.csv               # input target list
├── pyproject.toml              # deps + pytest + ruff config (uv-managed; lockfile in uv.lock)
├── tests/                      # pytest suite (fully mocked, no keys/network, asyncio_mode=auto)
└── output/                     # generated reports (gitignored)
```

> Each folder groups one functional concern: the package root holds the shared kernel (`config`, `models`, `agent_core`) and the `orchestrator`; `infra/` cross-cutting infrastructure; `tools/` agent tools; `agents/` the six agents (with their system prompts externalized as editable Markdown under `agents/agent_prompts/`); `writeback/` the pluggable sales-stack adapters; `reporting/` the HTML audit report.

<div align="center">
<sub>Built with Claude · async-first · human-in-the-loop by design</sub>
</div>
