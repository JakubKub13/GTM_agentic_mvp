# 🧪 Manual End-to-End Testing — duvo-signal-loop

This guide describes how to **manually** verify the whole pipeline end-to-end: which
commands to run, which external services to open and check, what a correct result looks
like, and roughly how long each run takes.

> Automated tests (`uv run pytest -q`, 281 mocked tests) already cover the code in isolation.
> This document is about the **live, human-in-the-loop verification** against the real
> services (Exa, Anthropic, Attio, Slack, Brevo).
>
> This project uses **[uv](https://docs.astral.sh/uv/)**. Commands are shown with the
> `uv run` prefix (no manual venv activation needed). If you prefer, you can
> `source .venv/bin/activate` once and drop the `uv run` prefix.

---

## 0. Before you start (pre-flight)

| Check | How |
|---|---|
| uv installed | `uv --version` (install: `curl -LsSf https://astral.sh/uv/install.sh \| sh`) |
| Environment synced | `uv sync` (creates `.venv` + installs all deps from `uv.lock`) |
| `.env` exists and is filled | `cp .env.example .env` then edit |
| Required keys present | `EXA_API_KEY`, `ANTHROPIC_API_KEY` (needed even for `--dry-run`) |
| CRM ready | `CRM_PROVIDER=attio` + `ATTIO_API_KEY` |
| Slack ready | `SLACK_WEBHOOK_URL` (incoming webhook for `#sales`) |
| Outreach ready | `OUTREACH_PROVIDER=brevo` + `BREVO_API_KEY` + `BREVO_LIST_ID` |
| Test inbox known | `TEST_EMAIL` (default `jakubkubala3@gmail.com`) — the lead address used for outreach |

Quick sanity check that the package imports and tests pass:

```bash
uv run python -c "import main; print('import ok')"
uv run pytest -q          # expect: 281 passed
```

> ⚠️ Even `--dry-run` makes **real** Exa + Anthropic calls (only the write-backs are
> simulated). So `EXA_API_KEY` and `ANTHROPIC_API_KEY` must be valid for any run.

---

## 1. Which services to open

Keep these tabs/windows open side-by-side during the test:

| # | Service | URL | What you verify here |
|---|---|---|---|
| 1 | 🖥️ **Terminal** | — | Live progress logs, per-account score lines, final `Report:` path |
| 2 | 📊 **HTML report** | `output/run-report.html` | The full audit log: scores, tiers, signals, drafted outreach, agent tool calls, write-back statuses |
| 3 | 🗂️ **Attio** | https://app.attio.com | Company records created + an "AI-suggested" evidence note per account |
| 4 | 💬 **Slack** | your `#sales` channel | Tier-1 alert messages (only for confident Tier 1 accounts) |
| 5 | ✉️ **Brevo** | https://app.brevo.com | Your review list gets new **contacts** (Contacts → Lists). **Nothing is sent.** |
| 6 | 📧 **Test inbox** | mailbox of `TEST_EMAIL` | Confirm **no** outreach email actually arrives (never-send guarantee) |
| 7 | 🔎 **Exa usage** *(optional)* | https://dashboard.exa.ai | Search credits being consumed (sanity that scouts really searched) |
| 8 | 🤖 **Anthropic usage** *(optional)* | https://platform.claude.com | Token usage (sanity that agents really ran) |

---

## 2. Test scenarios (run in this order)

### ✅ Scenario A — Dry run, small (safest first)

Agents run and **really decide**, but every write-back is simulated. Nothing touches Attio/Slack/Brevo.

```bash
uv run python main.py --dry-run --limit 3
```

**Verify:**
- Terminal prints a `-> <Company>` line and a `N/10 <Tier> conf=<...> human=<...>` line per account.
- Terminal ends with `Report: output/run-report.html`.
- Open the report → each account shows a score badge, signals with source links, a drafted outreach opener, and an **Agent tool calls** list (e.g. `exa_search(...)`, `submit_signals(...)`, `record_assessment(...)`, `crm_upsert()`, `finish()`).
- Statuses read `[dry-run] ...` — **do not** check Attio/Slack/Brevo for this scenario (nothing was written).

⏱️ **Duration:** ~1–2 minutes.

---

### ✅ Scenario B — Dry run, full list

```bash
uv run python main.py --dry-run
```

**Verify:** same as A, but for all 10 accounts. Confirm the two deliberately weak accounts
(**Tiny Local Bakery**, **Garage Startup XYZ**) come out **Tier 3 / low confidence** and are
flagged **"human research needed"** in the report — this proves `apply_guards()` is working.

⏱️ **Duration:** ~2–4 minutes.

---

### ✅ Scenario C — Real run, small (writes to live services)

This actually writes to Attio (and, for any confident Tier 1, Slack + Brevo).

```bash
uv run python main.py --limit 2
```

**Verify in each service:**

| Service | Expected |
|---|---|
| 🗂️ Attio | A **Company** record per processed account + an evidence **Note** titled `ICP N/10 (...) — AI-suggested, review before outreach`. The note body contains "AI-SUGGESTED", the score, why-fit / why-not, reasoning, and the drafted opener. |
| 💬 Slack `#sales` | A **Tier-1 alert** *only if* one of the 2 accounts is a confident Tier 1 (header `🟢 Tier 1: <Company> (N/10)`, persona, confidence, angle, drafted opener). If neither is a confident Tier 1, **no** Slack message — that's correct. |
| ✉️ Brevo | A new **contact** in your review list (`BREVO_LIST_ID`) only for a confident Tier 1. Open Contacts → Lists → your list. |
| 📧 Test inbox | **No outreach email received.** The lead only sits in the review list awaiting a human. |
| 📊 Report | Statuses now show real values (e.g. `attio company <uuid> ...`, `alert posted to #sales`, `contact queued in Brevo review list ...`). |

⏱️ **Duration:** ~1–2 minutes.

---

### ✅ Scenario D — Full real run

```bash
uv run python main.py
```

**Verify:** all 10 accounts processed; confident Tier-1 accounts trigger `crm_upsert` **+**
`slack_alert` **+** `outreach_queue`; weak/non-Tier-1 accounts get **only** `crm_upsert`
(Slack/outreach self-refuse — visible in the report's agent tool-call log as a refusal).

⏱️ **Duration:** ~3–6 minutes (10 accounts run concurrently, 5 at a time by default).

---

### ✅ Scenario E — Verbose / debugging

```bash
uv run python main.py --dry-run --limit 1 --log-level DEBUG
```

**Verify:** the terminal shows every agent turn, each tool call with its arguments, and each
guard decision. Useful to watch one account flow through scouts → analyst → router in detail.

⏱️ **Duration:** ~30–60 seconds.

---

## 3. Safety / human-in-the-loop checklist

These are the guarantees to confirm explicitly during a **real** run (Scenario C/D):

- [ ] **Never-send:** no email lands in the `TEST_EMAIL` inbox — leads only appear in the Brevo review list.
- [ ] **Guarded routing:** non-confident-Tier-1 accounts get only a CRM record (no Slack, no Brevo contact).
- [ ] **Guard caps confidence:** the two weak demo accounts are Tier 3 + "human research needed".
- [ ] **Failure isolation:** if one account errors (e.g. a transient API hiccup), the run continues and the report is still generated for the rest. You can simulate pressure with a tiny timeout: `ACCOUNT_TIMEOUT_SECONDS=0.01 uv run python main.py --limit 3` → accounts time out, are logged as failed, and the report still renders (with 0 results) without the process crashing.
- [ ] **No secrets in logs:** scan the terminal output — API keys and the Slack webhook URL never appear.

---

## 4. Tuning knobs (optional during testing)

| Flag / env | Effect |
|---|---|
| `--limit N` | Process only the first N accounts (faster) |
| `--concurrency N` | Override `MAX_CONCURRENT_ACCOUNTS` (default 5) — lower it if you hit rate limits |
| `--log-level DEBUG` | Full trace of turns, tool calls, guard decisions |
| `--test-email you@example.com` | Override the outreach lead address |
| `MAX_CONCURRENT_ACCOUNTS` | Account-level concurrency cap |
| `ACCOUNT_TIMEOUT_SECONDS` | Wall-clock cap per account (default 300) |

---

## 5. Expected durations at a glance

| Command | Accounts | Approx. wall-clock |
|---|---|---|
| `--dry-run --limit 3` | 3 | ~1–2 min |
| `--dry-run` | 10 | ~2–4 min |
| `--limit 2` (real) | 2 | ~1–2 min |
| *(full)* `uv run python main.py` | 10 | ~3–6 min |
| `--limit 1 --log-level DEBUG` | 1 | ~30–60 s |

> Timings vary with Exa/Anthropic latency, how many searches each agent chooses, and your
> concurrency setting. Per account the work is bounded by `ACCOUNT_TIMEOUT_SECONDS` (5 min),
> per model call by `ANTHROPIC_TIMEOUT_SECONDS` (120 s), and per HTTP write-back by
> `HTTP_TIMEOUT_SECONDS` (30 s).

---

## 6. Cleanup (optional)

- Generated reports live in `output/` (gitignored) — delete freely.
- In **Attio**, delete the test company records/notes if you don't want them retained
  (there is no automatic dedup yet — re-runs create new records).
- In **Brevo**, remove the test contact(s) from the review list if desired.

---

## ✅ Definition of done

A run is considered successfully verified when:
1. The terminal completes without an unhandled exception and prints the report path.
2. `output/run-report.html` opens and shows correct scores, signals, drafts, and statuses.
3. (Real run) Attio shows companies + AI-suggested notes; confident Tier-1s appear in Slack and the Brevo review list.
4. No outreach email was actually delivered.
5. The two weak demo accounts are correctly down-ranked and flagged.
