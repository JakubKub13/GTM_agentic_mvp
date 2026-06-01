# duvo-signal-loop — Design Spec

**Date:** 2026-06-01
**Author:** Jakub Kubala
**Context:** Take-home for Duvo.ai GTM Engineer role (CEO Tomas Cupr). Build something
that fixes a slow/manual B2B GTM loop, runs for real, and can be poked live. Stack to
build against: HubSpot, lemlist, Gong, Exa. Time budget: 3–4 hours. Deliverable: repo +
Loom + one-page note. Present EOD Tuesday 2026-06-02.

## One-liner

Take a realistic target list of retail/CPG accounts. For each account, collect real intent
signals via Exa in parallel, run them through a validating synthesizer with an
anti-hallucination guard, score ICP fit (1–10 + tier) and draft personalized outreach per
persona, then **write back into the real stack**: HubSpot (custom property + note with
evidence) → Slack alert for Tier 1 → lemlist as a **paused** draft campaign awaiting rep
approval.

## Why this problem

A 5-person GTM team's slowest, most manual loop is: notice a trigger event → figure out if
the account fits → research the right persona and angle → draft something worth sending →
log it in the CRM so it isn't lost. Today that's tabs, copy-paste, and tribal memory. This
system collapses a 30-minute-per-account manual task into a 30-second review. It mirrors the
exact shape Tomas already validated in the Rohlik briefing agent: parallel collectors → a
validated synthesizer with guards against making things up → fan-out per recipient.

## Architecture

Per-account pipeline, accounts processed with light concurrency:

```
companies.csv
   │  (per account)
   ▼
[Exa] 4 parallel signal queries  → raw hits
   │   - ERP / tech migration
   │   - hiring (supply chain / procurement / ops)
   │   - M&A / leadership change
   │   - operational pain points
   ▼
[Claude #1] VALIDATING SYNTH + GUARD
   - drop hits with no published date / off-topic
   - thin signal → confidence=low, do NOT score high
   ▼
[Claude #2] SCORE (1–10, tier) + DRAFT outreach per persona
   ▼
┌──────────────── WRITE-BACK ────────────────┐
│ [HubSpot]  upsert company + contact,         │  always
│            set icp_score property,           │
│            attach note: evidence + angle      │
│            (labeled "AI-suggested, review")   │
│ [Slack]    alert #sales — Tier 1 only         │  tier 1
│ [lemlist]  add lead → PAUSED campaign (draft) │  tier 1, awaits rep
└───────────────────────────────────────────────┘
   │
   ▼
output/run-report.html   (audit log for the Loom — NOT the deliverable)
```

## Components

| File | Purpose | Depends on |
|------|---------|-----------|
| `config.py` | Load keys from `.env` | python-dotenv |
| `models.py` | Pydantic: `Company`, `Signal`, `ICPScore`, `OutreachDraft`, `RunResult` | pydantic |
| `signal_collector.py` | `collect_signals(company) -> list[Signal]`; 4 parallel Exa queries; tolerant of empty results | exa-py |
| `scorer.py` | `synthesize_and_score(company, signals) -> ICPScore`; Claude call #1 (validate+guard) and #2 (score+draft) via forced tool_use | anthropic |
| `writeback/hubspot.py` | `upsert_account(score)`; create/update company+contact, set `icp_score`, attach evidence note | requests / hubspot SDK |
| `writeback/slack.py` | `alert_tier1(score)`; Block Kit message via incoming webhook | requests |
| `writeback/lemlist.py` | `queue_draft(score)`; add lead to a pre-created PAUSED campaign | requests |
| `reporter.py` | `generate_report(results)`; local HTML audit log | jinja2 |
| `main.py` | Orchestrate: load → per-account pipeline → write-back → report | — |

## Data model (key fields)

- `Signal`: `signal_type`, `title`, `summary`, `source_url`, `published_date`, `relevance`
- `ICPScore`: `company`, `score:int`, `tier`, `confidence`, `signals:list[Signal]`,
  `why_fit:list[str]`, `why_not:list[str]`, `recommended_persona`, `recommended_angle`,
  `reasoning`, `outreach:OutreachDraft`, `needs_human_research:bool`
- `OutreachDraft`: `persona`, `subject`, `first_line`, `body`

## Layered build order (time insurance)

- **T0 (~2h, must run):** Exa → Claude synth+guard+score+draft → HubSpot write-back. This
  alone is a closed loop into the CRM; the demo stands even if nothing else lands.
- **T1 (+30m):** Slack alert for Tier 1. Trivial webhook; highest visual payoff for the Loom.
- **T2 (+1h, riskiest, last):** lemlist push as paused campaign. If the API misbehaves,
  fall back to creating the lead without launch + screenshot, and say so honestly.

## Human-in-the-loop (deliberate)

1. **The system never sends.** lemlist campaign stays *paused*; the rep approves and sends.
   We automate research + drafting; the human owns the decision to make contact.
2. **Thin signal → guard fires.** Accounts with weak signals are not scored high, are flagged
   `needs_human_research`, and are not pushed to lemlist. Demonstrated live in the Loom.
3. HubSpot note is labeled "AI-suggested, review before outreach."

## Target list

8–12 real EU retail/CPG accounts with genuine recent public triggers (ERP news, M&A,
leadership), plus 1–2 deliberately weak/small accounts so the guard can be shown firing live.

## Error handling

- Exa returns nothing for a query → continue with that query's signals empty; never crash.
- Account ends with zero usable signals → `confidence=low`, `needs_human_research=true`,
  HubSpot note created, no Slack/lemlist.
- Claude tool_use enforced (`tool_choice`) → guaranteed structured output, no JSON parsing.
- Each write-back call wrapped in try/except; a failed channel is logged in `RunResult`,
  pipeline continues. Per-account isolation: one bad account never aborts the run.
- Light concurrency + small sleep between accounts to respect Exa rate limits.

## Testing

- `models.py` validated by construction (pydantic).
- A `--dry-run` flag that runs Exa + scoring but stubs the three write-back calls (prints
  what *would* be written) — lets us iterate without polluting HubSpot/lemlist and is the
  safe mode to run live if a real write misbehaves during the call.
- One smoke test per write-back module against the real sandbox (create + read back).

## Where it breaks (for Tomas)

- Exa noise/staleness on large brands; the guard mitigates but recall is limited.
- No real person-level email — we recommend a persona but use the candidate's own email as
  the test contact in the demo. Honest limitation.
- One-shot script; production = weekly cron + score diff + alert only on change.
- No dedup against existing HubSpot pipeline (production would check before creating).

## What I'd build next (one week)

Net-new account discovery via Exa as the front of the loop · Gong call-outcome → HubSpot
write-back · reply handling in lemlist branching on intent · person-level enrichment (Apollo)
for real emails.

## Tech

Python 3.11+ · exa-py · anthropic (Claude, forced tool_use) · requests · pydantic · jinja2 ·
python-dotenv. Keys: `EXA_API_KEY`, `ANTHROPIC_API_KEY`, `HUBSPOT_TOKEN`,
`SLACK_WEBHOOK_URL`, `LEMLIST_API_KEY`, `LEMLIST_CAMPAIGN_ID`.
