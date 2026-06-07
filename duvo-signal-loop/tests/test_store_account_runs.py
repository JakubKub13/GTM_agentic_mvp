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


async def _seed_run(run_id: str, run_date: str = "2026-06-05", dry_run: bool = False):
    await runs.start_run(
        run_id=run_id,
        run_date=run_date,
        started_at="t",
        dry_run=dry_run,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
    )


async def test_upsert_then_mark_status():
    await _seed_run("r1")
    await ar.upsert_account_run(
        run_id="r1",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="running",
        started_at="t1",
    )
    await ar.mark_status(
        run_id="r1",
        domain="acme.com",
        status="done",
        finished_at="t2",
        score=8,
        tier="Tier 1",
        confidence="high",
        needs_human_research=False,
        signals_count=3,
        signals_json="[]",
        score_json="{}",
        error=None,
    )
    row = await db.query_one(
        "SELECT * FROM account_runs WHERE run_id=? AND domain=?", ("r1", "acme.com")
    )
    assert row["status"] == "done"
    assert row["score"] == 8
    assert row["tier"] == "Tier 1"


async def test_mark_status_failed_records_error():
    await _seed_run("r1")
    await ar.upsert_account_run(
        run_id="r1", domain="x.com", company_name="X", country="", status="running", started_at="t1"
    )
    await ar.mark_status(
        run_id="r1", domain="x.com", status="failed", finished_at="t2", error="boom"
    )
    row = await db.query_one("SELECT status, error FROM account_runs WHERE domain=?", ("x.com",))
    assert row["status"] == "failed"
    assert row["error"] == "boom"


async def test_last_done_for_domain_excludes_current_run():
    await _seed_run("r1", run_date="2026-06-01")
    await _seed_run("r2", run_date="2026-06-05")
    await ar.upsert_account_run(
        run_id="r1",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="running",
        started_at="t",
    )
    await ar.mark_status(
        run_id="r1",
        domain="acme.com",
        status="done",
        finished_at="2026-06-01T00:00:00Z",
        score=5,
        tier="Tier 2",
        confidence="medium",
        needs_human_research=False,
        signals_count=1,
        signals_json="[]",
        score_json="{}",
        error=None,
    )
    # Current run r2 should not see its own row as "previous".
    prev = await ar.last_done_for_domain("acme.com", exclude_run_id="r2")
    assert prev["score"] == 5
    assert prev["tier"] == "Tier 2"
    # When excluding r1 too, there is no prior done.
    assert await ar.last_done_for_domain("acme.com", exclude_run_id="r1") is None


async def test_done_domains_for_run_and_date():
    await _seed_run("r1", run_date="2026-06-05")
    for dom, status in (("a.com", "done"), ("b.com", "failed"), ("c.com", "done")):
        await ar.upsert_account_run(
            run_id="r1", domain=dom, company_name=dom, country="", status="running", started_at="t"
        )
        await ar.mark_status(run_id="r1", domain=dom, status=status, finished_at="t2")
    assert await ar.done_domains_for_run("r1") == {"a.com", "c.com"}
    assert await ar.done_domains_for_date("2026-06-05") == {"a.com", "c.com"}
    assert await ar.done_domains_for_date("2026-01-01") == set()


async def test_upsert_reset_clears_stale_terminal_fields():
    """Plan #5: resetting an account to pending/running nulls every terminal field."""
    await _seed_run("r1")
    # First attempt completes with a full set of terminal data.
    await ar.upsert_account_run(
        run_id="r1",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="running",
        started_at="t1",
    )
    await ar.mark_status(
        run_id="r1",
        domain="acme.com",
        status="done",
        finished_at="t2",
        score=9,
        tier="Tier 1",
        confidence="high",
        needs_human_research=True,
        signals_count=4,
        signals_json="[1,2,3,4]",
        score_json="{\"x\": 1}",
        error="prev error",
    )
    # Persist a stale agent log too (column added by migration, populated elsewhere).
    await db.execute(
        "UPDATE account_runs SET agent_log_json = ? WHERE run_id=? AND domain=?",
        ("[\"old log\"]", "r1", "acme.com"),
    )

    # Resume: re-seed the same account back to running.
    await ar.upsert_account_run(
        run_id="r1",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="running",
        started_at="t3",
    )

    row = await db.query_one(
        "SELECT * FROM account_runs WHERE run_id=? AND domain=?", ("r1", "acme.com")
    )
    assert row["status"] == "running"
    assert row["started_at"] == "t3"
    # Every terminal field from the previous attempt must be cleared.
    assert row["score"] is None
    assert row["tier"] is None
    assert row["confidence"] is None
    assert row["error"] is None
    assert row["finished_at"] is None
    assert row["signals_count"] is None
    assert row["signals_json"] is None
    assert row["score_json"] is None
    assert row["needs_human_research"] is None
    assert row["agent_log_json"] is None


async def test_last_done_for_domain_ignores_dry_runs():
    """Plan #3: a dry-run row can never become a diff baseline."""
    await _seed_run("dry", run_date="2026-06-01", dry_run=True)
    await _seed_run("real", run_date="2026-06-05", dry_run=False)
    await ar.upsert_account_run(
        run_id="dry",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="running",
        started_at="t",
    )
    await ar.mark_status(
        run_id="dry",
        domain="acme.com",
        status="done",
        finished_at="2026-06-01T00:00:00Z",
        score=7,
        tier="Tier 1",
        confidence="high",
        needs_human_research=False,
        signals_count=2,
        signals_json="[]",
        score_json="{}",
        error=None,
    )
    # The only prior 'done' row for acme.com is a dry-run -> must NOT be a baseline.
    assert await ar.last_done_for_domain("acme.com", exclude_run_id="real") is None


async def test_done_domains_for_date_ignores_dry_runs():
    """Plan #3: a dry-run can never be skipped-as-done."""
    await _seed_run("dry", run_date="2026-06-05", dry_run=True)
    await _seed_run("real", run_date="2026-06-05", dry_run=False)
    await ar.upsert_account_run(
        run_id="dry", domain="d.com", company_name="D", country="", status="running", started_at="t"
    )
    await ar.mark_status(run_id="dry", domain="d.com", status="done", finished_at="t2")
    await ar.upsert_account_run(
        run_id="real", domain="r.com", company_name="R", country="", status="running", started_at="t"
    )
    await ar.mark_status(run_id="real", domain="r.com", status="done", finished_at="t2")
    # Only the real run's domain counts as done-today.
    assert await ar.done_domains_for_date("2026-06-05") == {"r.com"}
