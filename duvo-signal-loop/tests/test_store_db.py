import sqlite3

import pytest

from duvo.store import db

# The v1 schema as it shipped before the Phase-1 columns (#15): no `triggered_by` /
# `companies_json` on runs, no `agent_log_json` on account_runs, no `delivery_status`
# on writeback_events, and `PRAGMA user_version = 1`. Used to build an "old" DB on
# disk so the migration test exercises the real v1 -> v2 upgrade path.
_V1_SCHEMA = """
PRAGMA user_version = 1;

CREATE TABLE runs (
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

CREATE TABLE account_runs (
    run_id TEXT,
    domain TEXT,
    company_name TEXT,
    country TEXT,
    status TEXT,
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

CREATE TABLE writeback_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    domain TEXT,
    channel TEXT,
    provider TEXT,
    action TEXT,
    changed INTEGER,
    prev_score INTEGER,
    prev_tier TEXT,
    status_text TEXT,
    idempotency_key TEXT,
    created_at TEXT,
    UNIQUE (run_id, domain, channel)
);
"""


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
    await db.execute("INSERT INTO runs (run_id, status) VALUES (?, ?)", ("r1", "running"))
    row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", ("r1",))
    assert row["status"] == "running"


async def test_store_errors_degrade_to_safe_default(monkeypatch):
    # A broken query must not raise out of the helper — returns None / [].
    assert await db.query_one("SELECT * FROM does_not_exist") is None
    assert await db.query_all("SELECT * FROM does_not_exist") == []
    # execute swallows + logs, returns None
    assert await db.execute("INSERT INTO nope VALUES (1)") is None


# --- schema.sql is the v2 source of truth; migrate() upgrades existing DBs (#15, #23) ---


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


async def test_schema_sql_alone_is_v2_with_all_columns():
    # schema.sql must be the v2 source of truth ON ITS OWN (not patched by db._migrate
    # afterwards): a connection that runs ONLY schema.sql must already carry every
    # Phase-1 column and stamp the current user_version (plan #15/#23).
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    try:
        with open(db._SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db._SCHEMA_VERSION
        assert {"triggered_by", "companies_json"} <= _columns(conn, "runs")
        assert "agent_log_json" in _columns(conn, "account_runs")
        assert "delivery_status" in _columns(conn, "writeback_events")
    finally:
        conn.close()


async def test_fresh_db_from_connect_is_v2_with_all_columns(_tmp_db):
    # A brand-new DB created through the real connect path (schema.sql + migrate) must
    # carry every Phase-1 column and the current user_version (plan #15/#23).
    await db.query_one("SELECT 1")  # forces a connect → schema.sql + migrate
    conn = sqlite3.connect(_tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db._SCHEMA_VERSION
        assert {"triggered_by", "companies_json"} <= _columns(conn, "runs")
        assert "agent_log_json" in _columns(conn, "account_runs")
        assert "delivery_status" in _columns(conn, "writeback_events")
    finally:
        conn.close()


async def test_migrate_upgrades_v1_db_adding_all_new_columns(_tmp_db):
    # Build an OLD (v1) DB on disk: legacy schema, user_version = 1, no new columns,
    # plus a legacy completed write-back row. The next connect must migrate it to v2.
    seed = sqlite3.connect(_tmp_db)
    try:
        seed.executescript(_V1_SCHEMA)
        assert seed.execute("PRAGMA user_version").fetchone()[0] == 1
        seed.execute(
            "INSERT INTO writeback_events (run_id, domain, channel, action) "
            "VALUES ('legacy', 'acme.com', 'slack', 'alerted')"
        )
        seed.commit()
    finally:
        seed.close()

    # Fresh process view of the same path: the first connect runs schema.sql + _migrate.
    db._INITED.clear()
    await db.query_one("SELECT 1")

    conn = sqlite3.connect(_tmp_db)
    conn.row_factory = sqlite3.Row
    try:
        # user_version bumped to the current schema version.
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db._SCHEMA_VERSION
        # All four new columns added on the existing tables.
        assert {"triggered_by", "companies_json"} <= _columns(conn, "runs")
        assert "agent_log_json" in _columns(conn, "account_runs")
        assert "delivery_status" in _columns(conn, "writeback_events")
        # Legacy write-back rows default to 'succeeded' so old completed sends are not
        # mistaken for retryable ('attempting'/'failed') on a later resume (#15).
        legacy = conn.execute(
            "SELECT delivery_status FROM writeback_events WHERE run_id = 'legacy'"
        ).fetchone()
        assert legacy["delivery_status"] == "succeeded"
    finally:
        conn.close()
