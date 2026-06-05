# Durable Run State & Write-back Idempotency — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SQLite-backed durable store (`runs` / `account_runs` / `writeback_events`) plus Attio CRM dedup so re-runs stop polluting the CRM, capture score-diff data, and support crash resumability — without changing any send behavior.

**Architecture:** New `duvo/store/` package using stdlib `sqlite3` in WAL mode, every op offloaded via `asyncio.to_thread`. The orchestrator writes run/account state and a per-channel write-back event ledger (with diff vs the prior run) — guarded so `--dry-run` never persists and store errors degrade to no-ops (never abort the pipeline). Attio gains a search-then-upsert-by-domain path mirroring the existing HubSpot adapter. Resume is an input-filter change driven by `--resume <run_id>` / `--skip-done-today`.

**Tech Stack:** Python 3.11, stdlib `sqlite3`, `asyncio`, pytest (`asyncio_mode=auto`), existing `duvo` package conventions.

---

## Design refinement vs spec (read first)

The approved spec said router tools record events and read the diff in router policy. This plan instead records `writeback_events` and computes the diff **in the orchestrator (`_process_account`) after `run_router` returns**. Behavior is identical (one event row per channel; diff computed deterministically in code; dry-run skips persistence) but it keeps the `store` dependency out of the `agents/` layer — consistent with the house rule that the orchestrator owns cross-cutting wiring (it already manages tracing spans and the HTTP client lifecycle there). No change to the schema or the spec's guarantees.

## File structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `duvo/config.py` | Modify | add `DUVO_DB_PATH` |
| `.gitignore` | Modify | ignore `state/` |
| `duvo/store/__init__.py` | Create | public store API re-exports |
| `duvo/store/schema.sql` | Create | table + index DDL (`IF NOT EXISTS`) + `user_version` |
| `duvo/store/db.py` | Create | connection, WAL/pragmas, schema-ensure, `to_thread` exec helpers |
| `duvo/store/runs.py` | Create | `start_run`, `finish_run` |
| `duvo/store/account_runs.py` | Create | `upsert_account_run`, `mark_status`, `last_done_for_domain`, `done_domains_for_run`, `done_domains_for_date` |
| `duvo/store/events.py` | Create | `record_event`, `derive_action` |
| `duvo/writeback/attio.py` | Modify | search-then-upsert by domain (idempotent company) |
| `duvo/orchestrator.py` | Modify | wire store into `run()` / `_process_account`; `--resume` / `--skip-done-today` |
| `tests/test_store_db.py` | Create | db init / WAL / safe-no-op |
| `tests/test_store_runs.py` | Create | runs row lifecycle |
| `tests/test_store_account_runs.py` | Create | account_runs upsert / status / queries |
| `tests/test_store_events.py` | Create | event recording + `derive_action` |
| `tests/test_attio.py` | Modify | assert search-then-upsert dedup |
| `tests/test_main.py` | Modify | orchestrator persistence + resume + dry-run-skips |

---

## Task 1: Config — `DUVO_DB_PATH` + gitignore

**Files:**
- Modify: `duvo/config.py`
- Modify: `.gitignore`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_duvo_db_path_default(monkeypatch):
    monkeypatch.delenv("DUVO_DB_PATH", raising=False)
    import importlib

    from duvo import config

    importlib.reload(config)
    assert config.DUVO_DB_PATH == "state/duvo.db"


def test_duvo_db_path_override(monkeypatch):
    monkeypatch.setenv("DUVO_DB_PATH", "/tmp/custom.db")
    import importlib

    from duvo import config

    importlib.reload(config)
    assert config.DUVO_DB_PATH == "/tmp/custom.db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::test_duvo_db_path_default tests/test_config.py::test_duvo_db_path_override -v`
Expected: FAIL with `AttributeError: module 'duvo.config' has no attribute 'DUVO_DB_PATH'`

- [ ] **Step 3: Add the config**

In `duvo/config.py`, after the `LOG_LEVEL` line (around line 32), add:

```python
# Durable run-state store (SQLite). Lives outside output/ (that's ephemeral reports).
DUVO_DB_PATH = os.environ.get("DUVO_DB_PATH", "state/duvo.db")
```

- [ ] **Step 4: Ignore the state dir**

In `.gitignore`, add a line under the existing `output/` entry:

```
state/
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add duvo/config.py .gitignore tests/test_config.py
git commit -m "feat(config): add DUVO_DB_PATH for durable run-state store"
```

---

## Task 2: Store schema + db helpers

**Files:**
- Create: `duvo/store/__init__.py`
- Create: `duvo/store/schema.sql`
- Create: `duvo/store/db.py`
- Test: `tests/test_store_db.py`

- [ ] **Step 1: Create the package init (empty for now)**

Create `duvo/store/__init__.py`:

```python
"""Durable run-state store (SQLite). One file = one concern, like infra/ and writeback/."""
```

- [ ] **Step 2: Create the schema**

Create `duvo/store/schema.sql`:

```sql
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_date TEXT,
    started_at TEXT,
    finished_at TEXT,
    dry_run INTEGER,
    concurrency INTEGER,
    model TEXT,
    app_env TEXT,
    accounts_total INTEGER,
    accounts_succeeded INTEGER,
    accounts_failed INTEGER,
    status TEXT
);

CREATE TABLE IF NOT EXISTS account_runs (
    run_id TEXT,
    domain TEXT,
    company_name TEXT,
    country TEXT,
    status TEXT,                 -- pending | running | done | failed
    score INTEGER,
    tier TEXT,
    confidence TEXT,
    needs_human_research INTEGER,
    signals_count INTEGER,
    signals_json TEXT,
    score_json TEXT,
    error TEXT,
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (run_id, domain)
);

CREATE INDEX IF NOT EXISTS ix_account_runs_domain_finished
    ON account_runs (domain, finished_at);

CREATE TABLE IF NOT EXISTS writeback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    domain TEXT,
    channel TEXT,               -- crm | slack | outreach
    provider TEXT,
    action TEXT,                -- upserted | alerted | queued | refused | failed | skipped
    changed INTEGER,            -- 1 if score/tier differs from prior run, else 0
    prev_score INTEGER,
    prev_tier TEXT,
    status_text TEXT,
    idempotency_key TEXT,
    created_at TEXT,
    UNIQUE (run_id, domain, channel)
);
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_store_db.py`:

```python
import sqlite3

import pytest

from duvo.store import db


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    path = str(tmp_path / "test.db")
    monkeypatch.setattr(config, "DUVO_DB_PATH", path)
    db._INITED.clear()
    yield path
    db._INITED.clear()


async def test_run_creates_schema_and_executes():
    rows = await db.query_all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    names = {r["name"] for r in rows}
    assert {"runs", "account_runs", "writeback_events"} <= names


async def test_wal_mode_enabled():
    rows = await db.query_all("PRAGMA journal_mode")
    assert rows[0][0].lower() == "wal"


async def test_execute_and_query_one_roundtrip():
    await db.execute(
        "INSERT INTO runs (run_id, status) VALUES (?, ?)", ("r1", "running")
    )
    row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", ("r1",))
    assert row["status"] == "running"


async def test_store_errors_degrade_to_safe_default(monkeypatch):
    # A broken query must not raise out of the helper — returns None / [].
    assert await db.query_one("SELECT * FROM does_not_exist") is None
    assert await db.query_all("SELECT * FROM does_not_exist") == []
    # execute swallows + logs, returns None
    assert await db.execute("INSERT INTO nope VALUES (1)") is None
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run pytest tests/test_store_db.py -v`
Expected: FAIL with `ImportError` / `AttributeError` (no `db` module yet)

- [ ] **Step 5: Implement `db.py`**

Create `duvo/store/db.py`:

```python
"""SQLite connection + schema init for the durable run-state store.

Stdlib ``sqlite3`` is synchronous, so every operation is offloaded via
``asyncio.to_thread`` (the house-rule pattern for sync-only libs) — the event
loop never blocks. WAL mode + a short ``busy_timeout`` let the concurrently
``gather``-ed account coroutines write without "database is locked". Every
public helper degrades to a safe default on error (like ``infra.tracing``) so a
persistence failure can never abort the pipeline.
"""

import asyncio
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from duvo import config
from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
_INITED: set[str] = set()  # db paths whose schema has been ensured this process


def _connect() -> sqlite3.Connection:
    path = config.DUVO_DB_PATH
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if path not in _INITED:
        with open(_SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        conn.commit()
        _INITED.add(path)
    return conn


def _execute_sync(query: str, params: tuple) -> None:
    with _connect() as conn:
        conn.execute(query, params)
        conn.commit()


def _query_one_sync(query: str, params: tuple) -> sqlite3.Row | None:
    with _connect() as conn:
        cur = conn.execute(query, params)
        return cur.fetchone()


def _query_all_sync(query: str, params: tuple) -> list[sqlite3.Row]:
    with _connect() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


async def _safe(fn: Callable[..., Any], default: Any, *args: Any) -> Any:
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as exc:
        _log.warning("store op failed (%s) — continuing without persistence", exc)
        return default


async def execute(query: str, params: tuple = ()) -> None:
    """Run a write statement; commit. Returns None (also on error)."""
    return await _safe(_execute_sync, None, query, params)


async def query_one(query: str, params: tuple = ()) -> sqlite3.Row | None:
    """Return the first row, or None (also on error)."""
    return await _safe(_query_one_sync, None, query, params)


async def query_all(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Return all rows, or [] (also on error)."""
    return await _safe(_query_all_sync, [], query, params)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_store_db.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add duvo/store/__init__.py duvo/store/schema.sql duvo/store/db.py tests/test_store_db.py
git commit -m "feat(store): SQLite connection + schema with safe-no-op helpers"
```

---

## Task 3: `runs` repository

**Files:**
- Create: `duvo/store/runs.py`
- Test: `tests/test_store_runs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_store_runs.py`:

```python
import pytest

from duvo.store import db, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


async def test_start_run_inserts_running_row():
    await runs.start_run(
        run_id="r1",
        run_date="2026-06-05",
        started_at="2026-06-05T10:00:00Z",
        dry_run=False,
        concurrency=5,
        model="anthropic/claude-sonnet-4-6",
        app_env="dev",
        accounts_total=3,
    )
    row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("r1",))
    assert row["status"] == "running"
    assert row["accounts_total"] == 3
    assert row["dry_run"] == 0


async def test_start_run_is_idempotent_for_resume():
    await runs.start_run(run_id="r1", run_date="d", started_at="t1", dry_run=False,
                         concurrency=1, model="m", app_env="dev", accounts_total=2)
    # Resuming the same run must not overwrite the original started_at.
    await runs.start_run(run_id="r1", run_date="d", started_at="t2", dry_run=False,
                         concurrency=1, model="m", app_env="dev", accounts_total=2)
    row = await db.query_one("SELECT started_at FROM runs WHERE run_id = ?", ("r1",))
    assert row["started_at"] == "t1"


async def test_finish_run_sets_counts_and_status():
    await runs.start_run(run_id="r1", run_date="d", started_at="t", dry_run=False,
                         concurrency=1, model="m", app_env="dev", accounts_total=2)
    await runs.finish_run(run_id="r1", finished_at="t2", succeeded=1, failed=1)
    row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("r1",))
    assert row["status"] == "done"
    assert row["accounts_succeeded"] == 1
    assert row["accounts_failed"] == 1
    assert row["finished_at"] == "t2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'duvo.store.runs'`

- [ ] **Step 3: Implement `runs.py`**

Create `duvo/store/runs.py`:

```python
"""The ``runs`` table: one row per batch run."""

from duvo.store import db


async def start_run(
    *,
    run_id: str,
    run_date: str,
    started_at: str,
    dry_run: bool,
    concurrency: int,
    model: str,
    app_env: str,
    accounts_total: int,
) -> None:
    """Insert a run row in status ``running``. Idempotent (resume keeps the original)."""
    await db.execute(
        """
        INSERT INTO runs (run_id, run_date, started_at, dry_run, concurrency,
                          model, app_env, accounts_total, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
        ON CONFLICT(run_id) DO NOTHING
        """,
        (run_id, run_date, started_at, int(dry_run), concurrency, model, app_env, accounts_total),
    )


async def finish_run(*, run_id: str, finished_at: str, succeeded: int, failed: int) -> None:
    """Mark the run done and record success/failure counts."""
    await db.execute(
        """
        UPDATE runs
        SET finished_at = ?, accounts_succeeded = ?, accounts_failed = ?, status = 'done'
        WHERE run_id = ?
        """,
        (finished_at, succeeded, failed, run_id),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_runs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add duvo/store/runs.py tests/test_store_runs.py
git commit -m "feat(store): runs repository (start_run/finish_run, resume-idempotent)"
```

---

## Task 4: `account_runs` repository

**Files:**
- Create: `duvo/store/account_runs.py`
- Test: `tests/test_store_account_runs.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_store_account_runs.py`:

```python
import pytest

from duvo.store import account_runs as ar
from duvo.store import db, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


async def _seed_run(run_id: str, run_date: str = "2026-06-05"):
    await runs.start_run(run_id=run_id, run_date=run_date, started_at="t", dry_run=False,
                         concurrency=1, model="m", app_env="dev", accounts_total=1)


async def test_upsert_then_mark_status():
    await _seed_run("r1")
    await ar.upsert_account_run(
        run_id="r1", domain="acme.com", company_name="Acme", country="US",
        status="running", started_at="t1",
    )
    await ar.mark_status(
        run_id="r1", domain="acme.com", status="done", finished_at="t2",
        score=8, tier="Tier 1", confidence="high", needs_human_research=False,
        signals_count=3, signals_json="[]", score_json="{}", error=None,
    )
    row = await db.query_one(
        "SELECT * FROM account_runs WHERE run_id=? AND domain=?", ("r1", "acme.com")
    )
    assert row["status"] == "done"
    assert row["score"] == 8
    assert row["tier"] == "Tier 1"


async def test_mark_status_failed_records_error():
    await _seed_run("r1")
    await ar.upsert_account_run(run_id="r1", domain="x.com", company_name="X",
                                country="", status="running", started_at="t1")
    await ar.mark_status(run_id="r1", domain="x.com", status="failed",
                         finished_at="t2", error="boom")
    row = await db.query_one("SELECT status, error FROM account_runs WHERE domain=?", ("x.com",))
    assert row["status"] == "failed"
    assert row["error"] == "boom"


async def test_last_done_for_domain_excludes_current_run():
    await _seed_run("r1", run_date="2026-06-01")
    await _seed_run("r2", run_date="2026-06-05")
    await ar.upsert_account_run(run_id="r1", domain="acme.com", company_name="Acme",
                               country="US", status="running", started_at="t")
    await ar.mark_status(run_id="r1", domain="acme.com", status="done",
                         finished_at="2026-06-01T00:00:00Z", score=5, tier="Tier 2",
                         confidence="medium", needs_human_research=False,
                         signals_count=1, signals_json="[]", score_json="{}", error=None)
    # Current run r2 should not see its own row as "previous".
    prev = await ar.last_done_for_domain("acme.com", exclude_run_id="r2")
    assert prev["score"] == 5
    assert prev["tier"] == "Tier 2"
    # When excluding r1 too, there is no prior done.
    assert await ar.last_done_for_domain("acme.com", exclude_run_id="r1") is None


async def test_done_domains_for_run_and_date():
    await _seed_run("r1", run_date="2026-06-05")
    for dom, status in (("a.com", "done"), ("b.com", "failed"), ("c.com", "done")):
        await ar.upsert_account_run(run_id="r1", domain=dom, company_name=dom,
                                    country="", status="running", started_at="t")
        await ar.mark_status(run_id="r1", domain=dom, status=status, finished_at="t2")
    assert await ar.done_domains_for_run("r1") == {"a.com", "c.com"}
    assert await ar.done_domains_for_date("2026-06-05") == {"a.com", "c.com"}
    assert await ar.done_domains_for_date("2026-01-01") == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_account_runs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'duvo.store.account_runs'`

- [ ] **Step 3: Implement `account_runs.py`**

Create `duvo/store/account_runs.py`:

```python
"""The ``account_runs`` table: one row per (run, account)."""

from duvo.store import db


async def upsert_account_run(
    *,
    run_id: str,
    domain: str,
    company_name: str,
    country: str,
    status: str,
    started_at: str,
) -> None:
    """Insert (or reset on re-run) the account row in the given status."""
    await db.execute(
        """
        INSERT INTO account_runs (run_id, domain, company_name, country, status, started_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, domain) DO UPDATE SET
            company_name = excluded.company_name,
            country = excluded.country,
            status = excluded.status,
            started_at = excluded.started_at
        """,
        (run_id, domain, company_name, country, status, started_at),
    )


async def mark_status(
    *,
    run_id: str,
    domain: str,
    status: str,
    finished_at: str,
    score: int | None = None,
    tier: str | None = None,
    confidence: str | None = None,
    needs_human_research: bool | None = None,
    signals_count: int | None = None,
    signals_json: str | None = None,
    score_json: str | None = None,
    error: str | None = None,
) -> None:
    """Update an account row's terminal status + result fields."""
    nhr = None if needs_human_research is None else int(needs_human_research)
    await db.execute(
        """
        UPDATE account_runs SET
            status = ?, finished_at = ?, score = ?, tier = ?, confidence = ?,
            needs_human_research = ?, signals_count = ?, signals_json = ?,
            score_json = ?, error = ?
        WHERE run_id = ? AND domain = ?
        """,
        (status, finished_at, score, tier, confidence, nhr, signals_count,
         signals_json, score_json, error, run_id, domain),
    )


async def last_done_for_domain(domain: str, *, exclude_run_id: str):
    """Return the most recent ``done`` row for *domain* from any OTHER run, else None."""
    return await db.query_one(
        """
        SELECT score, tier, confidence FROM account_runs
        WHERE domain = ? AND status = 'done' AND run_id != ?
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        (domain, exclude_run_id),
    )


async def done_domains_for_run(run_id: str) -> set[str]:
    """Return the set of domains already ``done`` within *run_id* (for --resume)."""
    rows = await db.query_all(
        "SELECT domain FROM account_runs WHERE run_id = ? AND status = 'done'", (run_id,)
    )
    return {r["domain"] for r in rows}


async def done_domains_for_date(run_date: str) -> set[str]:
    """Return domains ``done`` on *run_date* across all runs (for --skip-done-today)."""
    rows = await db.query_all(
        """
        SELECT ar.domain FROM account_runs ar
        JOIN runs r ON ar.run_id = r.run_id
        WHERE r.run_date = ? AND ar.status = 'done'
        """,
        (run_date,),
    )
    return {r["domain"] for r in rows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_account_runs.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add duvo/store/account_runs.py tests/test_store_account_runs.py
git commit -m "feat(store): account_runs repository (upsert/status/diff/resume queries)"
```

---

## Task 5: `writeback_events` repository + `derive_action`

**Files:**
- Create: `duvo/store/events.py`
- Test: `tests/test_store_events.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_store_events.py`:

```python
import pytest

from duvo.store import db, events, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


@pytest.mark.parametrize(
    "channel,status_text,expected",
    [
        ("crm", "attio company rec_1 (ICP 8) + evidence note", "upserted"),
        ("slack", "posted to #sales", "alerted"),
        ("outreach", "contact queued in Brevo review list 7", "queued"),
        ("slack", "refused: not a confident Tier 1 (safety guard)", "refused"),
        ("outreach", "failed: boom", "failed"),
        ("crm", "skipped", "skipped"),
    ],
)
def test_derive_action(channel, status_text, expected):
    assert events.derive_action(channel, status_text) == expected


async def test_record_event_persists_row():
    await runs.start_run(run_id="r1", run_date="d", started_at="t", dry_run=False,
                         concurrency=1, model="m", app_env="dev", accounts_total=1)
    await events.record_event(
        run_id="r1", domain="acme.com", channel="crm", provider="attio",
        status_text="attio company rec_1 (ICP 8)", changed=True,
        prev_score=5, prev_tier="Tier 2", created_at="t1",
    )
    row = await db.query_one(
        "SELECT * FROM writeback_events WHERE run_id=? AND domain=? AND channel=?",
        ("r1", "acme.com", "crm"),
    )
    assert row["action"] == "upserted"
    assert row["provider"] == "attio"
    assert row["changed"] == 1
    assert row["prev_score"] == 5


async def test_record_event_upserts_on_rerun_same_channel():
    await events.record_event(run_id="r1", domain="a.com", channel="slack", provider="slack",
                              status_text="refused: x", changed=False, prev_score=None,
                              prev_tier=None, created_at="t1")
    await events.record_event(run_id="r1", domain="a.com", channel="slack", provider="slack",
                              status_text="posted to #sales", changed=True, prev_score=None,
                              prev_tier=None, created_at="t2")
    rows = await db.query_all(
        "SELECT action, status_text FROM writeback_events WHERE run_id=? AND domain=? AND channel=?",
        ("r1", "a.com", "slack"),
    )
    assert len(rows) == 1  # UNIQUE(run_id, domain, channel) → updated, not duplicated
    assert rows[0]["action"] == "alerted"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_store_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'duvo.store.events'`

- [ ] **Step 3: Implement `events.py`**

Create `duvo/store/events.py`:

```python
"""The ``writeback_events`` table: a per-channel audit ledger of write-back actions."""

from duvo.store import db

_CHANNEL_OK = {"crm": "upserted", "slack": "alerted", "outreach": "queued"}


def derive_action(channel: str, status_text: str) -> str:
    """Map a write-back status string to a coarse ledger action.

    ``refused`` / ``failed`` / ``skipped`` are detected by prefix; otherwise the
    channel's success verb is used (crm→upserted, slack→alerted, outreach→queued).
    """
    t = (status_text or "").strip().lower()
    if t.startswith("refused"):
        return "refused"
    if t.startswith("failed") or t.startswith("error"):
        return "failed"
    if t.startswith("skipped") or t == "":
        return "skipped"
    return _CHANNEL_OK.get(channel, "ok")


async def record_event(
    *,
    run_id: str,
    domain: str,
    channel: str,
    provider: str,
    status_text: str,
    changed: bool,
    prev_score: int | None,
    prev_tier: str | None,
    created_at: str,
) -> None:
    """Insert (or update on re-run) one write-back event row.

    The ``action`` is derived from *status_text*; ``idempotency_key`` is
    ``domain:channel`` (the natural per-channel key for future suppression).
    """
    await db.execute(
        """
        INSERT INTO writeback_events
            (run_id, domain, channel, provider, action, changed, prev_score,
             prev_tier, status_text, idempotency_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, domain, channel) DO UPDATE SET
            provider = excluded.provider,
            action = excluded.action,
            changed = excluded.changed,
            prev_score = excluded.prev_score,
            prev_tier = excluded.prev_tier,
            status_text = excluded.status_text,
            created_at = excluded.created_at
        """,
        (run_id, domain, channel, provider, derive_action(channel, status_text),
         int(changed), prev_score, prev_tier, status_text, f"{domain}:{channel}", created_at),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_store_events.py -v`
Expected: PASS (6 parametrized + 2 = 8 cases)

- [ ] **Step 5: Commit**

```bash
git add duvo/store/events.py tests/test_store_events.py
git commit -m "feat(store): writeback_events ledger + derive_action"
```

---

## Task 6: Store public API re-exports

**Files:**
- Modify: `duvo/store/__init__.py`

- [ ] **Step 1: Re-export the public functions**

Replace the contents of `duvo/store/__init__.py` with:

```python
"""Durable run-state store (SQLite). One file = one concern, like infra/ and writeback/."""

from duvo.store.account_runs import (
    done_domains_for_date,
    done_domains_for_run,
    last_done_for_domain,
    mark_status,
    upsert_account_run,
)
from duvo.store.events import derive_action, record_event
from duvo.store.runs import finish_run, start_run

__all__ = [
    "done_domains_for_date",
    "done_domains_for_run",
    "last_done_for_domain",
    "mark_status",
    "upsert_account_run",
    "derive_action",
    "record_event",
    "finish_run",
    "start_run",
]
```

- [ ] **Step 2: Verify the suite still passes**

Run: `uv run pytest tests/test_store_db.py tests/test_store_runs.py tests/test_store_account_runs.py tests/test_store_events.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add duvo/store/__init__.py
git commit -m "feat(store): expose store package public API"
```

---

## Task 7: Attio search-then-upsert (CRM dedup)

**Files:**
- Modify: `duvo/writeback/attio.py:43-87` (replace `_create_company`)
- Test: `tests/test_attio.py`

**Context:** Today `_create_company` blindly POSTs, creating a duplicate on every re-run. Mirror the HubSpot adapter (`hubspot.py:64` `_upsert_company`): query by domain, PATCH if found, POST if not. Keep the existing name+domains → name-only fallback for the create path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_attio.py` (imports `_fake_response`, `make_fake_async_client`, `make_score` from conftest; patch `duvo.infra.http_client.get_client`):

```python
from unittest.mock import AsyncMock, patch

from duvo.writeback import attio
from tests.conftest import _fake_response, make_score


async def test_attio_upserts_existing_company_by_domain():
    score = make_score(domain="acme.com")
    # query returns an existing record → PATCH, no create POST
    query_resp = _fake_response(200, {"data": [{"id": {"record_id": "rec_existing"}}]})
    patch_resp = _fake_response(200, {"data": {"id": {"record_id": "rec_existing"}}})
    note_resp = _fake_response(201, {"data": {"id": {"note_id": "n1"}}})
    client = type("C", (), {})()
    client.post = AsyncMock(side_effect=[query_resp, note_resp])  # query + note
    client.patch = AsyncMock(return_value=patch_resp)
    with patch("duvo.infra.http_client.get_client", return_value=client):
        result = await attio.upsert_account(score)
    client.patch.assert_awaited_once()  # updated, not duplicated
    assert "rec_existing" in result


async def test_attio_creates_when_not_found():
    score = make_score(domain="newco.com")
    query_resp = _fake_response(200, {"data": []})  # not found
    create_resp = _fake_response(201, {"data": {"id": {"record_id": "rec_new"}}})
    note_resp = _fake_response(201, {"data": {"id": {"note_id": "n1"}}})
    client = type("C", (), {})()
    client.post = AsyncMock(side_effect=[query_resp, create_resp, note_resp])
    client.patch = AsyncMock()
    with patch("duvo.infra.http_client.get_client", return_value=client):
        result = await attio.upsert_account(score)
    client.patch.assert_not_awaited()
    assert "rec_new" in result
```

Note: review the existing `tests/test_attio.py` and update/remove any prior test that asserted a single blind-POST create flow, since `upsert_account` now queries first.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_attio.py -v`
Expected: FAIL (current `_create_company` POSTs directly; no query/patch path)

- [ ] **Step 3: Replace `_create_company` with a search-then-upsert**

In `duvo/writeback/attio.py`, replace the whole `_create_company` function (lines 43-86) with:

```python
async def _find_company_id(score: ICPScore) -> str | None:
    """Return the Attio record_id for a company matching ``domains``, or None."""
    url = f"{_BASE}/objects/companies/records/query"
    resp = await retry.with_retries(
        lambda: http_client.get_client().post(
            url, headers=_headers(), json={"filter": {"domains": score.domain}, "limit": 1}
        ),
        max_attempts=config.HTTP_MAX_RETRIES,
    )
    if resp.status_code >= 300:
        log.debug("Attio: domain query non-2xx (%s) — treating as not found", resp.status_code)
        return None
    data = resp.json().get("data") or []
    if not data:
        return None
    return data[0]["id"]["record_id"]


async def _patch_company(score: ICPScore, record_id: str) -> str:
    """PATCH an existing company record's values; return its record_id."""
    url = f"{_BASE}/objects/companies/records/{record_id}"
    log.info("Attio: updating existing company '%s' (record_id=%s)", score.company_name, record_id)
    resp = await retry.with_retries(
        lambda: http_client.get_client().patch(
            url, headers=_headers(), json={"data": {"values": {"name": score.company_name}}}
        ),
        max_attempts=config.HTTP_MAX_RETRIES,
    )
    resp.raise_for_status()
    return record_id


async def _create_company(score: ICPScore) -> str:
    """Create a new Attio company; try name+domains, fall back to name-only."""
    url = f"{_BASE}/objects/companies/records"
    log.info("Attio: creating company record for '%s' (%s)", score.company_name, score.domain)
    last = None
    for attempt, values in enumerate(
        ({"name": score.company_name, "domains": [score.domain]}, {"name": score.company_name})
    ):
        if attempt > 0:
            log.debug("Attio: payload shape rejected — retrying name-only for '%s'",
                      score.company_name)
        last = await retry.with_retries(
            lambda values=values: http_client.get_client().post(
                url, headers=_headers(), json={"data": {"values": values}}
            ),
            max_attempts=config.HTTP_MAX_RETRIES,
        )
        if last.status_code < 300:
            record_id: str = last.json()["data"]["id"]["record_id"]
            log.info("Attio: company record created — record_id=%s", record_id)
            return record_id
    log.error("Attio: failed to create company '%s' — status=%s", score.company_name,
              last.status_code if last else "unknown")
    last.raise_for_status()  # type: ignore[union-attr]
    return ""  # unreachable


async def _upsert_company(score: ICPScore) -> str:
    """Find the company by domain and PATCH it, else create it. Idempotent on re-runs."""
    existing = await _find_company_id(score)
    if existing:
        return await _patch_company(score, existing)
    return await _create_company(score)
```

- [ ] **Step 4: Point `upsert_account` at the new path**

In `duvo/writeback/attio.py`, in `upsert_account` (around line 119), change:

```python
    record_id = await _create_company(score)
```

to:

```python
    record_id = await _upsert_company(score)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_attio.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add duvo/writeback/attio.py tests/test_attio.py
git commit -m "feat(writeback): Attio search-then-upsert by domain (CRM dedup)"
```

---

## Task 8: Orchestrator persistence + event ledger

**Files:**
- Modify: `duvo/orchestrator.py` (imports; `_process_account` signature + body; `run()` body)
- Test: `tests/test_main.py`

**Context:** `_process_account` (`orchestrator.py:34`) must persist account state and, when not dry-run, record the three write-back events with a diff vs the prior run. `run()` (`orchestrator.py:107`) must `start_run` / `finish_run`. All store calls are guarded by `if persist:` (= `not dry_run`). Per-account isolation and `http_client.aclose()` in `finally` are unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main.py` (uses the existing patterns there; patch the agent stages and point the DB at tmp):

```python
from unittest.mock import AsyncMock, patch

import pytest

from duvo import orchestrator
from duvo.models import RunResult
from duvo.store import db
from tests.conftest import make_score


@pytest.fixture
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


def _patch_pipeline(score):
    """Patch the three agent stages so _process_account runs without LLM/network."""
    async def fake_scout(company, log=None):
        return []

    async def fake_analyst(company, signals, log=None):
        return score

    async def fake_router(rr, dry_run, test_email, log=None):
        rr.crm_status = "attio company rec_1 (ICP 8)"
        rr.slack_status = "posted to #sales"
        rr.outreach_status = "contact queued in Brevo review list 7"

    return (
        patch.object(orchestrator, "scout_all", fake_scout),
        patch.object(orchestrator, "run_analyst", fake_analyst),
        patch.object(orchestrator, "run_router", fake_router),
    )


async def test_real_run_persists_run_and_account_and_events(_tmp_db, monkeypatch):
    monkeypatch.setattr(orchestrator, "load_companies",
                        lambda *a, **k: [__import__("duvo.models", fromlist=["Company"]).Company(
                            name="Acme", domain="acme.com", country="US", description="")])
    score = make_score(domain="acme.com", score=8, tier="Tier 1")
    p1, p2, p3 = _patch_pipeline(score)
    with p1, p2, p3:
        await orchestrator.run(dry_run=False, test_email="t@e.com", limit=None)

    run_row = await db.query_one("SELECT * FROM runs LIMIT 1")
    assert run_row["status"] == "done"
    assert run_row["accounts_succeeded"] == 1
    acc = await db.query_one("SELECT * FROM account_runs WHERE domain=?", ("acme.com",))
    assert acc["status"] == "done"
    assert acc["score"] == 8
    events = await db.query_all("SELECT channel, action FROM writeback_events WHERE domain=?",
                                ("acme.com",))
    by_channel = {e["channel"]: e["action"] for e in events}
    assert by_channel == {"crm": "upserted", "slack": "alerted", "outreach": "queued"}


async def test_dry_run_does_not_persist(_tmp_db, monkeypatch):
    monkeypatch.setattr(orchestrator, "load_companies",
                        lambda *a, **k: [__import__("duvo.models", fromlist=["Company"]).Company(
                            name="Acme", domain="acme.com", country="US", description="")])
    score = make_score(domain="acme.com")
    p1, p2, p3 = _patch_pipeline(score)
    with p1, p2, p3:
        await orchestrator.run(dry_run=True, test_email="t@e.com", limit=None)
    # No rows at all.
    assert await db.query_one("SELECT * FROM runs LIMIT 1") is None
    assert await db.query_one("SELECT * FROM account_runs LIMIT 1") is None


async def test_event_records_diff_vs_prior_run(_tmp_db, monkeypatch):
    from duvo.models import Company
    from duvo.store import account_runs, runs

    # Seed a prior done run with score 5.
    await runs.start_run(run_id="old", run_date="2026-06-01", started_at="t", dry_run=False,
                         concurrency=1, model="m", app_env="dev", accounts_total=1)
    await account_runs.upsert_account_run(run_id="old", domain="acme.com", company_name="Acme",
                                          country="US", status="running", started_at="t")
    await account_runs.mark_status(run_id="old", domain="acme.com", status="done",
                                   finished_at="2026-06-01T00:00:00Z", score=5, tier="Tier 2",
                                   confidence="medium", needs_human_research=False,
                                   signals_count=0, signals_json="[]", score_json="{}", error=None)

    monkeypatch.setattr(orchestrator, "load_companies",
                        lambda *a, **k: [Company(name="Acme", domain="acme.com", country="US",
                                                 description="")])
    score = make_score(domain="acme.com", score=8, tier="Tier 1")  # changed
    p1, p2, p3 = _patch_pipeline(score)
    with p1, p2, p3:
        await orchestrator.run(dry_run=False, test_email="t@e.com", limit=None)

    ev = await db.query_one(
        "SELECT changed, prev_score, prev_tier FROM writeback_events WHERE channel='crm' "
        "AND domain='acme.com' AND prev_score IS NOT NULL", ()
    )
    assert ev["changed"] == 1
    assert ev["prev_score"] == 5
    assert ev["prev_tier"] == "Tier 2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_main.py -v -k "persist or dry_run_does_not or diff"`
Expected: FAIL (orchestrator does not import/use `store` yet)

- [ ] **Step 3: Add imports + a UTC timestamp helper**

In `duvo/orchestrator.py`, add to the imports block (after the existing `from duvo.reporting...` import):

```python
from duvo import store
```

The module already imports `from datetime import UTC, datetime`. Add a small helper above `_process_account`:

```python
def _now_iso() -> str:
    """UTC timestamp string for store rows."""
    return datetime.now(UTC).isoformat()
```

- [ ] **Step 4: Thread `persist` + `run_date`-aware store calls into `_process_account`**

Change the `_process_account` signature to accept `persist: bool` (add it after `semaphore`):

```python
async def _process_account(
    company: Company,
    dry_run: bool,
    test_email: str,
    semaphore: asyncio.Semaphore,
    run_id: str,
    run_date: str,
    persist: bool,
) -> RunResult | None:
```

Then inside the `try:` block, wrap the body so it persists state. Replace the existing `try:`/`except` body (lines ~74-104) with:

```python
            try:
                if persist:
                    await store.upsert_account_run(
                        run_id=run_id, domain=company.domain, company_name=company.name,
                        country=company.country, status="running", started_at=_now_iso(),
                    )
                async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
                    agent_log: list[str] = []
                    signals = await scout_all(company, agent_log)
                    score = await run_analyst(company, signals, agent_log)
                    rr = RunResult(score=score, signals=signals)
                    await run_router(rr, dry_run, test_email, agent_log)
                    rr.agent_log = agent_log
                    if persist:
                        await _persist_success(run_id, company, rr, signals)
                    if root is not None:
                        root.update(output={"score": score.score, "tier": score.tier,
                                            "confidence": score.confidence})
                    log.info(
                        "%d/10 %s conf=%s human=%s (%d signals, %d tool calls)",
                        score.score, score.tier, score.confidence,
                        score.needs_human_research, len(signals), len(agent_log),
                    )
                    return rr
            except Exception as exc:
                if persist:
                    await store.mark_status(run_id=run_id, domain=company.domain,
                                            status="failed", finished_at=_now_iso(),
                                            error=str(exc))
                if root is not None:
                    root.update(level="ERROR", status_message=str(exc))
                log.error("account %s failed: %s", company.name, exc)
                return None
```

- [ ] **Step 5: Add the `_persist_success` helper (diff + status + events)**

First add `import json` to the top import block of `duvo/orchestrator.py` (it is not imported there today). Then add this function above `_process_account`:

```python
_EVENT_CHANNELS = ("crm", "slack", "outreach")


async def _persist_success(run_id, company, rr, signals) -> None:
    """Mark the account done and record one write-back event per channel with the diff."""
    s = rr.score
    prev = await store.last_done_for_domain(company.domain, exclude_run_id=run_id)
    changed = prev is None or prev["score"] != s.score or prev["tier"] != s.tier
    prev_score = prev["score"] if prev else None
    prev_tier = prev["tier"] if prev else None

    await store.mark_status(
        run_id=run_id, domain=company.domain, status="done", finished_at=_now_iso(),
        score=s.score, tier=s.tier, confidence=s.confidence,
        needs_human_research=s.needs_human_research, signals_count=len(signals),
        signals_json=json.dumps([sig.model_dump() for sig in signals], ensure_ascii=False),
        score_json=s.model_dump_json(), error=None,
    )

    status_by_channel = {
        "crm": (rr.crm_status, config.CRM_PROVIDER),
        "slack": (rr.slack_status, "slack"),
        "outreach": (rr.outreach_status, config.OUTREACH_PROVIDER),
    }
    now = _now_iso()
    for channel in _EVENT_CHANNELS:
        status_text, provider = status_by_channel[channel]
        await store.record_event(
            run_id=run_id, domain=company.domain, channel=channel, provider=provider,
            status_text=status_text, changed=changed, prev_score=prev_score,
            prev_tier=prev_tier, created_at=now,
        )
```

Note: `import json` already exists in some agent modules but NOT in `orchestrator.py` — add `import json as _json` to the top import block (or reuse a plain `import json` and call `json.dumps`). Keep one consistent name.

- [ ] **Step 6: Wire `start_run` / `finish_run` + `persist` into `run()`**

In `run()`, after `run_id` / `run_date` are computed and `companies` is loaded (after the `limit` slice, around line 147), add:

```python
    persist = not dry_run
    if persist:
        await store.start_run(
            run_id=run_id, run_date=run_date, started_at=datetime.now(UTC).isoformat(),
            dry_run=dry_run, concurrency=concurrency or MAX_CONCURRENT_ACCOUNTS,
            model=config.LLM_MODEL, app_env=config.APP_ENV, accounts_total=len(companies),
        )
```

Update the `gather` call to pass `persist`:

```python
                raw_results = await asyncio.gather(
                    *[
                        _process_account(c, dry_run, test_email, sem, run_id, run_date, persist)
                        for c in companies
                    ]
                )
```

To make the `finally` safe even if `gather` raises, initialize the results list just before the `try:`:

```python
    raw_results: list = []
```

Then in the `finally:` block (after `tracing.flush()`), add the run finalization — `raw_results` is always defined:

```python
    finally:
        await http_client.aclose()
        await exa_tool.aclose()
        tracing.flush()
        if not dry_run:
            ok = sum(1 for r in raw_results if r is not None)
            await store.finish_run(run_id=run_id, finished_at=datetime.now(UTC).isoformat(),
                                   succeeded=ok, failed=len(companies) - ok)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS (including the three new tests)

- [ ] **Step 8: Commit**

```bash
git add duvo/orchestrator.py tests/test_main.py
git commit -m "feat(orchestrator): persist run/account state + write-back event ledger with diff"
```

---

## Task 9: Resume — `--resume` / `--skip-done-today`

**Files:**
- Modify: `duvo/orchestrator.py` (`run()` signature + filter; `main()` argparse)
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_main.py`:

```python
async def test_resume_skips_done_domains_for_run(_tmp_db, monkeypatch):
    from duvo.models import Company
    from duvo.store import account_runs, runs

    # Seed run "r-resume" with a.com already done, b.com not.
    await runs.start_run(run_id="r-resume", run_date="2026-06-05", started_at="t", dry_run=False,
                         concurrency=1, model="m", app_env="dev", accounts_total=2)
    await account_runs.upsert_account_run(run_id="r-resume", domain="a.com", company_name="A",
                                          country="", status="running", started_at="t")
    await account_runs.mark_status(run_id="r-resume", domain="a.com", status="done",
                                   finished_at="t2", score=7, tier="Tier 2", confidence="high",
                                   needs_human_research=False, signals_count=0,
                                   signals_json="[]", score_json="{}", error=None)

    processed = []

    async def fake_scout(company, log=None):
        processed.append(company.domain)
        return []

    monkeypatch.setattr(orchestrator, "load_companies",
                        lambda *a, **k: [Company(name="A", domain="a.com", country="", description=""),
                                         Company(name="B", domain="b.com", country="", description="")])
    score = make_score(domain="b.com")
    with patch.object(orchestrator, "scout_all", fake_scout), \
         patch.object(orchestrator, "run_analyst", AsyncMock(return_value=score)), \
         patch.object(orchestrator, "run_router", AsyncMock()):
        await orchestrator.run(dry_run=False, test_email="t@e.com", limit=None,
                               resume_run_id="r-resume")

    assert processed == ["b.com"]  # a.com skipped


def test_main_parses_resume_flags(monkeypatch):
    captured = {}

    async def fake_run(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(orchestrator, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["prog", "--resume", "abc123", "--skip-done-today"])
    orchestrator.main()
    assert captured["resume_run_id"] == "abc123"
    assert captured["skip_done_today"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_main.py -v -k "resume or skip_done"`
Expected: FAIL (`run()` has no `resume_run_id` / `skip_done_today` params)

- [ ] **Step 3: Extend `run()` signature + resume filter**

Change the `run()` signature to add the two params (after `concurrency`):

```python
async def run(
    dry_run: bool,
    test_email: str,
    limit: int | None,
    log_level: str = LOG_LEVEL,
    concurrency: int | None = None,
    resume_run_id: str | None = None,
    skip_done_today: bool = False,
) -> None:
```

Replace the `run_id = uuid4().hex` line with:

```python
    run_id = resume_run_id or uuid4().hex
```

After the `companies = companies[:limit]` slice (and before `sem = ...`), add the resume filter:

```python
    if not dry_run and (resume_run_id or skip_done_today):
        skip: set[str] = set()
        if resume_run_id:
            skip |= await store.done_domains_for_run(resume_run_id)
        if skip_done_today:
            skip |= await store.done_domains_for_date(run_date)
        before = len(companies)
        companies = [c for c in companies if c.domain not in skip]
        log.info("resume: skipping %d already-done account(s)", before - len(companies))
```

Note: `log` is defined a few lines above (`log = get_logger(__name__)`); ensure this filter block is placed AFTER that line. Move it below `log = get_logger(__name__)` if needed.

- [ ] **Step 4: Add the CLI flags in `main()`**

In `main()`, after the `--concurrency` argument, add:

```python
    ap.add_argument(
        "--resume",
        dest="resume_run_id",
        default=None,
        help="resume an existing run_id: skip its already-done accounts (real runs only)",
    )
    ap.add_argument(
        "--skip-done-today",
        action="store_true",
        help="skip accounts already marked done for today's run_date (cron convenience)",
    )
```

And pass them into `run(...)`:

```python
    asyncio.run(
        run(
            dry_run=args.dry_run,
            test_email=args.test_email,
            limit=args.limit,
            log_level=args.log_level,
            concurrency=args.concurrency,
            resume_run_id=args.resume_run_id,
            skip_done_today=args.skip_done_today,
        )
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add duvo/orchestrator.py tests/test_main.py
git commit -m "feat(orchestrator): --resume and --skip-done-today for crash recovery"
```

---

## Task 10: Full suite + docs

**Files:**
- Modify: `README.md` (run table + "Where it breaks" note)
- Modify: `.env.example` (document `DUVO_DB_PATH`)

- [ ] **Step 1: Run the full suite + linters**

Run: `uv run pytest -q`
Expected: PASS (existing tests + all new store/orchestrator/attio tests)

Run: `uv run ruff check && uv run ruff format --check`
Expected: clean (fix any reported issues, then re-run)

- [ ] **Step 2: Document the new env var**

In `.env.example`, add under an appropriate section:

```
# Durable run-state store (SQLite). Default state/duvo.db. Dry-runs do not persist.
DUVO_DB_PATH=state/duvo.db
```

- [ ] **Step 3: Document the new flags + persistence in README**

In `README.md`, add two rows to the Run table (around line 150):

```
| `uv run python main.py --resume <run_id>` | Re-run only the accounts that didn't finish in `<run_id>` |
| `uv run python main.py --skip-done-today` | Skip accounts already completed today (cron-friendly) |
```

And update the "Where it breaks" bullet about one-shot/no-dedup (line ~219) to note that CRM dedup (Attio upsert-by-domain) and durable run state now exist; score-diff is captured in `writeback_events` for future alerting.

- [ ] **Step 4: Commit**

```bash
git add README.md .env.example
git commit -m "docs: document DUVO_DB_PATH, --resume/--skip-done-today, and CRM dedup"
```

---

## Self-review notes (for the implementer)

- **Spec coverage:** runs/account_runs/writeback_events (Tasks 2-6) ✓; Attio dedup (Task 7) ✓; resumability (Task 9) ✓; diff captured-not-gated (Task 8 `_persist_success`, no send-behavior change) ✓; dry-run does NOT persist (Task 8 `persist` guard, tested) ✓; store never aborts pipeline (Task 2 `_safe`) ✓; WAL + busy_timeout + synchronous=NORMAL (Task 2 `_connect`, the three hardening items requested during brainstorming) ✓.
- **Type consistency:** store functions are keyword-only and re-exported in Task 6 exactly as called in Task 8/9. `last_done_for_domain(domain, *, exclude_run_id=...)` signature matches every call site.
- **Out of scope (do NOT build):** Slack/outreach suppression on unchanged score (data captured only); Postgres backend; HTML report surfacing the diff. These are documented follow-ups in the spec.
```
