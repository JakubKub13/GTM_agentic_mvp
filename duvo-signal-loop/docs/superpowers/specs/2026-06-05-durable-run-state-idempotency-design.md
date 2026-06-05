# Durable run state & write-back idempotency — design

**Date:** 2026-06-05
**Status:** Approved design (pre-implementation)
**Branch:** `feat/durable-run-state`

## Problem

The pipeline is one-shot. The only persisted output is ephemeral HTML in the
gitignored `output/`. This blocks the roadmap's "scheduled run + score-diff +
resumability" and leaves real correctness gaps on re-runs:

- **Attio** blindly `POST`s a new company record + note on every run
  (`writeback/attio.py:43`), so re-runs pollute the CRM with duplicates. (HubSpot
  already does a true search-by-domain upsert — `writeback/hubspot.py:64` — and
  Brevo upserts the contact via `updateEnabled` + per-domain plus-addressing, so
  those two are already idempotent. Attio is the real hole.)
- **No run history** anywhere, so there is no "what score did account X get last
  week" → no score-diff alerting and no replay/eval corpus.
- **No durable per-account status**, so a batch that crashes mid-run cannot be
  resumed; it re-does everything.

## Goals (all four, confirmed)

1. Stop polluting the CRM on re-runs (Attio dedup).
2. Score-diff data captured per account across runs (basis for future alerting).
3. Resumability after a crash.
4. A durable replay / eval corpus.

## Non-goals

- No Postgres, no multi-instance / shared store. Deployment is **one machine,
  cron/scheduled** → SQLite single file is sufficient.
- No behavioral gating of write-backs on score-diff (see Decisions). The diff is
  **recorded**, not acted on, in this iteration.
- No migration framework (Alembic). `CREATE TABLE IF NOT EXISTS` + `PRAGMA
  user_version` only.
- No new third-party dependency — `sqlite3` is stdlib.

## Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Storage tech | SQLite single file via stdlib `sqlite3` | single-machine, queryable history + mutable status, zero new deps |
| Schema | 3 tables: `runs`, `account_runs`, `writeback_events` | full relational store incl. per-event audit ledger (chosen over lean single-table) |
| Resume trigger | **both**: explicit `--resume <run_id>` + optional `--skip-done-today` | safe default (new run never skips); cron convenience flag available |
| Score-diff policy | **action always, just log the diff** | no change to send behavior now; ledger captures `changed`/`prev_score`/`prev_tier` so alerting can be built later |
| Dry-run persistence | **dry-run does NOT persist** | keeps production history clean and the offline/dry-run test path store-free |
| Attio note | append a note every run (audit trail) | consistent with "action always"; the company itself is upserted so no duplicate companies |

## Architecture

### New package: `duvo/store/`

One folder, one concern — mirrors `infra/` and `writeback/`.

- `db.py` — connection + init. Opens SQLite in **WAL** mode; every operation runs
  inside `asyncio.to_thread` so the sync driver never blocks the event loop (the
  sanctioned house-rule pattern for sync-only libs). Connection-per-operation;
  WAL handles concurrent writes from the `asyncio.gather`-ed account coroutines.
  Runs `schema.sql` on first use (idempotent).
- `schema.sql` — `CREATE TABLE IF NOT EXISTS …` for the three tables + indexes;
  sets `PRAGMA user_version` for future hand-rolled migrations.
- `runs.py` — `start_run(...)`, `finish_run(run_id, counts...)`.
- `account_runs.py` — `upsert_account_run(...)`, `mark_status(run_id, domain,
  status, ...)`, `last_done_for_domain(domain) -> row | None`,
  `done_domains_for_run(run_id) -> set[str]`, `done_domains_for_date(run_date)`.
- `events.py` — `record_event(...)`, `last_event(domain, channel) -> row | None`.

DB path: `DUVO_DB_PATH` env, default `state/duvo.db` (NOT `output/`, which is
"ephemeral reports"). Add `state/` to `.gitignore`.

### Schema

```sql
runs(
  run_id TEXT PRIMARY KEY, run_date TEXT, started_at TEXT, finished_at TEXT,
  dry_run INT, concurrency INT, model TEXT, app_env TEXT,
  accounts_total INT, accounts_succeeded INT, accounts_failed INT, status TEXT
);

account_runs(
  run_id TEXT, domain TEXT, company_name TEXT, country TEXT,
  status TEXT,                          -- pending | running | done | failed
  score INT, tier TEXT, confidence TEXT, needs_human_research INT,
  signals_count INT, signals_json TEXT, score_json TEXT, error TEXT,
  started_at TEXT, finished_at TEXT,
  PRIMARY KEY (run_id, domain)          -- in-run idempotency
);
CREATE INDEX IF NOT EXISTS ix_account_runs_domain_finished
  ON account_runs(domain, finished_at);  -- score-diff "last done for domain"

writeback_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT, domain TEXT,
  channel TEXT,                         -- crm | slack | outreach
  provider TEXT,                        -- attio|hubspot|brevo|lemlist|slack
  action TEXT,                          -- created|updated|queued|refused|failed
                                        -- (no "skipped": diff is logged, never gates an action)
  changed INT,                          -- 1 if score/tier differs from prior run, else 0
  prev_score INT, prev_tier TEXT,
  status_text TEXT, idempotency_key TEXT, created_at TEXT,
  UNIQUE (run_id, domain, channel)      -- never write the same channel twice in a run (resume-safe)
);
```

`account_runs` serves diff + replay + resume; `writeback_events` is the per-event
audit ledger and the in-run idempotency layer.

### Pipeline integration (`duvo/orchestrator.py`)

Guarded by `if not dry_run:` everywhere — dry-run skips the store entirely.

1. Start of `run()`: `await store.start_run(run_id, run_date, dry_run=False,
   concurrency, model, app_env, accounts_total)` (status=`running`).
2. Resume filter applied to the company list before `gather`:
   - `--resume <run_id>`: reuse that `run_id`; skip domains in
     `done_domains_for_run(run_id)`.
   - `--skip-done-today`: skip domains in `done_domains_for_date(run_date)`.
   - neither: fresh `run_id`, process all.
3. Inside `_process_account` (still fully isolated):
   - `upsert_account_run(status="running")` at entry (skip if dry_run).
   - on success: `mark_status("done", score/tier/confidence/signals/score_json)`.
   - on exception: `mark_status("failed", error=str(exc))` (inside the existing
     `except`, before returning `None`).
4. End of `run()`: `finish_run(run_id, succeeded, failed, status="done")` in the
   `finally`/after-gather block (next to the existing report generation).

Resume is the only change to the input filter; per-account isolation,
semaphore, timeout, and `http_client.aclose()` in `finally` are unchanged.

### Write-back idempotency + diff recording

- **Attio (`writeback/attio.py`)** — replace blind `POST` with **assert-by-
  matching-attribute** on `domains` (Attio's single-call upsert), so a re-run
  updates the existing company instead of creating a duplicate. Note is still
  appended each run as an audit trail. Keep the existing payload-fallback
  robustness.
- **HubSpot** — already upserts the company; no change required beyond emitting
  events.
- **Event recording** — each router tool (`crm_upsert`, `slack_alert`,
  `outreach_queue`), and `finish`, records a `writeback_events` row after acting,
  including `refused` and the dry-run path is simply skipped (no persistence).
  Before recording, the router reads `last_done_for_domain(domain)` to compute
  `changed`/`prev_score`/`prev_tier`. This is a deterministic read in the router
  layer (policy), not a prompt instruction. **No send behavior changes** — Slack
  and outreach still fire for every confident Tier 1; the diff is only logged.

Idempotency keys (recorded on events for future use): CRM = `domain`; outreach =
`domain + campaign/list id`; in-run safety via the `UNIQUE(run_id, domain,
channel)` constraint.

### Config (`duvo/config.py`)

- `DUVO_DB_PATH` (default `state/duvo.db`).
- New CLI flags in `main()`: `--resume <run_id>`, `--skip-done-today`.
- No new required env var; the store is created lazily on first real (non-dry)
  run.

## Error handling

- Store failures must **never** abort the pipeline. Each store call is wrapped so
  a persistence error logs a warning and degrades to a no-op (same philosophy as
  `infra/tracing.py`). A bad DB write cannot sink an account or the batch.
- WAL mode + connection-per-op avoids "database is locked" under the concurrent
  gather; if a write still contends, the `to_thread` call retries briefly.

## Testing (mirrors existing offline philosophy)

- All store tests point `DUVO_DB_PATH` at a `tmp_path` SQLite file — still no
  network, no keys.
- `start_run` → `mark_status` → `finish_run` writes the expected rows.
- Resume: an account marked `done` for a `run_id` is skipped on `--resume`;
  `--skip-done-today` skips by `run_date`.
- Attio assert-by-domain does not create a duplicate company on a second run
  (mocked HTTP asserting a single upsert call shape).
- `writeback_events` row is recorded per channel with correct `action`, and
  `changed`/`prev_score` reflect a prior `account_runs` row.
- Dry-run leaves the DB untouched (no file / no rows).

## Out of scope / follow-ups

- Acting on the diff (score-diff Slack alerting that suppresses unchanged
  accounts) — data is captured now; gating is a later, small change.
- Promoting the diff into the HTML report header.
- Postgres backend (only if multi-instance becomes real).
