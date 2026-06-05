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
    await runs.start_run(
        run_id="r1",
        run_date="d",
        started_at="t1",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=2,
    )
    # Resuming the same run must not overwrite the original started_at.
    await runs.start_run(
        run_id="r1",
        run_date="d",
        started_at="t2",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=2,
    )
    row = await db.query_one("SELECT started_at FROM runs WHERE run_id = ?", ("r1",))
    assert row["started_at"] == "t1"


async def test_finish_run_sets_counts_and_status():
    await runs.start_run(
        run_id="r1",
        run_date="d",
        started_at="t",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=2,
    )
    await runs.finish_run(run_id="r1", finished_at="t2", succeeded=1, failed=1)
    row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("r1",))
    assert row["status"] == "done"
    assert row["accounts_succeeded"] == 1
    assert row["accounts_failed"] == 1
    assert row["finished_at"] == "t2"
