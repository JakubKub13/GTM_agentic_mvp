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
    await db.execute("INSERT INTO runs (run_id, status) VALUES (?, ?)", ("r1", "running"))
    row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", ("r1",))
    assert row["status"] == "running"


async def test_store_errors_degrade_to_safe_default(monkeypatch):
    # A broken query must not raise out of the helper — returns None / [].
    assert await db.query_one("SELECT * FROM does_not_exist") is None
    assert await db.query_all("SELECT * FROM does_not_exist") == []
    # execute swallows + logs, returns None
    assert await db.execute("INSERT INTO nope VALUES (1)") is None
