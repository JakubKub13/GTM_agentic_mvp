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


async def test_start_run_persists_triggered_by_and_companies_json():
    await runs.start_run(
        run_id="r1",
        run_date="d",
        started_at="t",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
        triggered_by="user@example.com",
        companies_json='[{"domain": "acme.com"}]',
    )
    row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("r1",))
    assert row["triggered_by"] == "user@example.com"
    assert row["companies_json"] == '[{"domain": "acme.com"}]'


async def test_start_run_triggered_by_defaults_to_none():
    await runs.start_run(
        run_id="r1",
        run_date="d",
        started_at="t",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
    )
    row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("r1",))
    assert row["triggered_by"] is None
    assert row["companies_json"] is None


# --- db.execute_tx: non-swallowing single-transaction executor (#4) ---


async def test_execute_tx_commits_all_statements_atomically():
    def _work(conn):
        conn.execute(
            "INSERT INTO runs (run_id, status) VALUES (?, 'running')",
            ("rtx",),
        )
        conn.execute(
            "INSERT INTO account_runs (run_id, domain, status) VALUES (?, ?, 'pending')",
            ("rtx", "a.com"),
        )

    await db.execute_tx(_work)
    run_row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("rtx",))
    acct = await db.query_all("SELECT * FROM account_runs WHERE run_id = ?", ("rtx",))
    assert run_row["status"] == "running"
    assert len(acct) == 1


async def test_execute_tx_reraises_and_rolls_back():
    def _work(conn):
        conn.execute(
            "INSERT INTO runs (run_id, status) VALUES (?, 'running')",
            ("rtx",),
        )
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await db.execute_tx(_work)
    # The first INSERT must have been rolled back — no phantom row.
    row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("rtx",))
    assert row is None


# --- start_run_strict: runs row + pending account_runs in one tx, raises (#4, #5) ---


async def test_start_run_strict_writes_run_and_pending_accounts():
    await runs.start_run_strict(
        run_id="r1",
        run_date="d",
        started_at="t",
        dry_run=True,
        concurrency=2,
        model="m",
        app_env="dev",
        triggered_by="user@example.com",
        companies_json='[{"domain": "acme.com"}, {"domain": "globex.com"}]',
        accounts=[
            {"domain": "acme.com", "company_name": "Acme", "country": "US"},
            {"domain": "globex.com", "company_name": "Globex", "country": "DE"},
        ],
    )
    run_row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", ("r1",))
    assert run_row["status"] == "running"
    assert run_row["accounts_total"] == 2
    assert run_row["dry_run"] == 1
    assert run_row["triggered_by"] == "user@example.com"
    assert run_row["companies_json"] == '[{"domain": "acme.com"}, {"domain": "globex.com"}]'

    accts = await db.query_all(
        "SELECT * FROM account_runs WHERE run_id = ? ORDER BY domain", ("r1",)
    )
    assert [a["domain"] for a in accts] == ["acme.com", "globex.com"]
    assert all(a["status"] == "pending" for a in accts)
    assert {a["company_name"] for a in accts} == {"Acme", "Globex"}
    assert all(a["started_at"] == "t" for a in accts)


async def test_start_run_strict_raises_and_leaves_nothing_on_conflict():
    # Pre-existing run with the same id forces a PRIMARY KEY conflict (no DO NOTHING here).
    await runs.start_run(
        run_id="r1",
        run_date="d",
        started_at="orig",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
    )
    with pytest.raises(Exception):
        await runs.start_run_strict(
            run_id="r1",
            run_date="d",
            started_at="t",
            dry_run=False,
            concurrency=1,
            model="m",
            app_env="dev",
            triggered_by="u",
            companies_json="[]",
            accounts=[{"domain": "acme.com", "company_name": "Acme", "country": "US"}],
        )
    # No account rows leaked from the failed strict preflight (rolled back atomically).
    accts = await db.query_all("SELECT * FROM account_runs WHERE run_id = ?", ("r1",))
    assert accts == []
    # Original row untouched.
    row = await db.query_one("SELECT started_at FROM runs WHERE run_id = ?", ("r1",))
    assert row["started_at"] == "orig"
