# duvo-signal-loop — Design Spec (multi-agent)

**Date:** 2026-06-01
**Author:** Jakub Kubala
**Context:** Take-home for Duvo.ai GTM Engineer role (CEO Tomas Cupr). Build something that
fixes a slow/manual B2B GTM loop, runs for real, and can be poked live. Stack to build against:
HubSpot, lemlist, Gong, Exa. Time budget: 3–4 hours. Deliverable: repo + Loom + one-page note.
Present EOD Tuesday 2026-06-02.

## One-liner

A small **team of AI agents** that turns a target list of retail/CPG accounts into rep-ready
pipeline. Per account: parallel **scout agents** (each with an Exa search tool) hunt sourced
intent signals, an **analyst agent** validates them — verifying doubtful claims with its own
searches before trusting them — scores ICP fit and drafts personalized outreach, and a **router
agent** decides how to action the account into the real stack (CRM, Slack, lemlist). Python
only orchestrates the hand-offs; each node is a genuine tool-using agent, not a fixed query.

The CRM write-back is a **pluggable interface** (`crm.upsert_account`): it ships working against
**Attio** (a real CRM with instant self-serve API access) and carries a **HubSpot** adapter behind
the same interface — Duvo's actual stack is a one-line config flip (`CRM_PROVIDER`) once a portal
is available. Built on Attio so the demo provisions in minutes, not on a CRM signup that may stall.

## Why agents, not a workflow (the design thesis)

Each scout is a real agent loop: it decides its own queries, follows the most promising thread,
and stops when it has enough. The analyst can dispatch its own verification searches to refute a
claim before trusting it. The router reasons about what actions an account warrants. This is the
shape Tomas already validated in the Rohlik briefing agent — parallel tool-using collectors into
a validated synthesizer with guards against making things up, fanning out per recipient — rebuilt
against Duvo's stack.

**But agency is bounded on purpose.** Agents own *reversible, low-stakes* decisions (which query
to run, which persona to target, which channel to use). *Irreversible* steps — capping a
hallucinated score, and never sending cold email — are hard deterministic guards and a human
approval gate, not things the agent is trusted to get right. Knowing where to *withhold* agency
is the same judgment as knowing where to leave a human in the loop.

## Architecture

Per-account, Python orchestrates three agent stages:

```
companies.csv
   │  per account — Python orchestrator (the conductor)
   ▼
┌── SCOUT AGENTS (4, parallel) ─────────────────────────┐
│  beats: erp_migration · hiring · ma_leadership · pain │  each = Claude agent
│  tool: exa_search                                      │  with a ReAct tool
│  loop: decide query → search → read → refine → repeat  │  loop (bounded)
│  finish: submit_signals(...)  ← only SOURCED signals   │
└────────────────────────────────────────────────────────┘
   ▼  all signals
ANALYST AGENT
   tools: exa_search (verification), record_assessment
   - may run up to 2 verification searches to refute a doubtful claim
   - outputs score, tier, confidence, persona, angle, outreach draft
   ▼  + deterministic apply_guards()  (caps hallucinated confidence)
ROUTER AGENT
   tools: crm_upsert, slack_alert, lemlist_queue, finish
   - decides which actions the account warrants
   - crm_upsert -> pluggable CRM (Attio default, HubSpot adapter behind same interface)
   - tools SELF-GUARD: slack/lemlist refuse unless confident Tier 1
   ▼
output/run-report.html   (audit log: every agent's tool calls — for the Loom, not the deliverable)
```

## The agent runtime

A single reusable `run_agent(system, user, tools, impls, max_turns, final_tools)` helper
implements the Anthropic tool-use loop: call the model → execute any tool_use blocks → feed
results back → repeat until the agent calls a designated *final* tool or stops. Every agent in
the system is one call to this helper with a different toolset. This keeps the "agent" concept
honest (model-directed tool use in a loop) and the codebase small and auditable.

## Components

| File | Responsibility | Agent? |
|------|----------------|--------|
| `config.py` | Env keys, model id | — |
| `models.py` | Pydantic: `Company`, `Signal`, `OutreachDraft`, `ICPScore`, `RunResult` | — |
| `agent_core.py` | `run_agent()` tool-use loop + shared Anthropic client | runtime |
| `tools/exa_tool.py` | `exa_search` tool schema + impl (used by scouts and analyst) | tool |
| `scouts.py` | `run_scout(company, beat)` — one scout agent per beat; `scout_all()` runs 4 in parallel | ✔ scout |
| `analyst.py` | `run_analyst(company, signals)` — analyst agent; `apply_guards()` deterministic post-guard | ✔ analyst |
| `writeback/crm.py` | CRM dispatcher: `upsert_account()` routes to Attio or HubSpot by `CRM_PROVIDER` | — |
| `writeback/{attio,hubspot,slack,lemlist}.py` | Deterministic, self-guarding API calls | — |
| `router.py` | `run_router(rr, dry_run)` — router agent; write-backs exposed as its tools | ✔ router |
| `reporter.py` + `templates/report.html` | HTML audit log of the run | — |
| `main.py` | Orchestrate per account: scouts → analyst → router → report; `--dry-run` | conductor |

## Data model (key fields)

- `Signal`: `signal_type`, `title`, `summary`, `source_url`, `published_date`, `relevance`
- `OutreachDraft`: `persona`, `subject`, `first_line`, `body`
- `ICPScore`: `company_name`, `domain`, `score:int`, `tier`, `confidence`, `why_fit`, `why_not`,
  `recommended_persona`, `recommended_angle`, `reasoning`, `needs_human_research:bool`, `outreach`
- `RunResult`: `score`, `signals`, `crm_status`, `slack_status`, `lemlist_status`,
  `agent_log: list[str]` (tool calls each agent made — shown in the report)

## Where agency is bounded (deliberate human-in-the-loop)

1. **Scouts cannot invent.** System prompt forbids unsourced claims; the analyst independently
   verifies; undated/unsourced signals are discarded.
2. **The analyst's score is capped by a deterministic guard.** `apply_guards()` — not the model —
   forces `confidence=low` + `needs_human_research=true` when evidence is thin, and caps a
   low-confidence high score. The model cannot talk its way past this.
3. **The router never sends.** `lemlist_queue` only adds a lead to a **paused** campaign; a rep
   approves and sends. `slack_alert` and `lemlist_queue` self-refuse unless the account is a
   confident Tier 1 — true even if the router agent decides otherwise. CRM notes are labeled
   "AI-suggested — review before outreach."

## Layered build order (time insurance)

- **T0 (~2.5h, must run):** `agent_core` + `exa_tool` + scouts + analyst + `apply_guards` +
  CRM write-back (Attio via the `crm` dispatcher) + router + report. A complete agentic loop
  into the CRM. The demo stands even if nothing else lands.
- **T1 (+30m):** add `slack_alert` to the router's toolset.
- **T2 (+45m, riskiest, last):** add `lemlist_queue` (paused campaign) to the router's toolset.

## Target list

8–10 real EU retail/CPG accounts with genuine recent public triggers, plus 1–2 deliberately
weak/small accounts so the guard fires on camera (scouts find little → analyst flags →
router declines Slack/lemlist).

## Error handling

- Each scout degrades to empty on any Exa/model error; one bad scout never aborts the account.
- `run_agent` is bounded by `max_turns`; an agent that never finishes returns what it has.
- Analyst with no usable signals → conservative `needs_human_research` default.
- Router tools wrapped so a failed channel is logged in `RunResult` and the run continues.
- `--dry-run`: router agent still runs and *really decides*, but the write-back tools simulate
  (print what they would do). Safe iteration and the live-demo fallback.

## Where it breaks (for Tomas)

- Exa noise/staleness on large brands; scout iteration + analyst verification mitigate, recall
  still limited.
- Agent loops add latency and variance; bounded by turn caps, so worst case is degraded recall,
  not a hang. Demo runs 2–3 accounts live, the full list is pre-run.
- No real person-level email — persona is recommended; the demo uses a test email as the lead.
- One-shot run; production = scheduled run + score diff + alert only on change.
- No dedup vs. existing CRM pipeline (re-runs create new records; production would assert/upsert).
- CRM is pluggable: built on Attio for instant provisioning; HubSpot adapter is the same
  interface and becomes the target via `CRM_PROVIDER=hubspot` once a portal exists.

## What I'd build next (one week)

Promote scouts to MCP-tool agents (Apollo/LinkedIn/Gong as tools) · a discovery agent that finds
net-new accounts from a trigger before scoring · Gong call-outcome agent writing back to HubSpot ·
a reply-handling agent that branches the lemlist sequence on intent · person-level enrichment for
real emails. The `run_agent` runtime stays; only toolsets grow.

## Tech

Python 3.11+ · anthropic (tool-use loop) · exa-py · requests · pydantic · jinja2 · python-dotenv.
Keys: `EXA_API_KEY`, `ANTHROPIC_API_KEY`, `CRM_PROVIDER` (attio|hubspot), `ATTIO_API_KEY`,
`HUBSPOT_TOKEN` (optional), `SLACK_WEBHOOK_URL`, `LEMLIST_API_KEY`, `LEMLIST_CAMPAIGN_ID`,
`TEST_EMAIL`.
