"""Read-side store query helpers (plan #12).

The three endpoints' data layer: `list_runs()` (history), `get_run(run_id)`
(run + account_runs snapshot), `get_account_detail(run_id, domain)` (detail +
the score/tier diff already computed for `writeback_events`).

Seeded via the existing store writers so the tests exercise the real schema.
"""

import json

import pytest

from duvo.store import account_runs, db, events, queries, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


async def _seed_run(
    run_id: str,
    *,
    started_at: str,
    dry_run: bool = False,
    triggered_by: str | None = "cli",
    accounts_total: int = 1,
) -> None:
    await runs.start_run(
        run_id=run_id,
        run_date="2026-06-05",
        started_at=started_at,
        dry_run=dry_run,
        concurrency=2,
        model="anthropic/claude-sonnet-4-6",
        app_env="dev",
        accounts_total=accounts_total,
        triggered_by=triggered_by,
        companies_json='[{"domain": "acme.com"}]',
    )


# --- list_runs: history, newest first ---


async def test_list_runs_returns_empty_when_no_runs():
    assert await queries.list_runs() == []


async def test_list_runs_orders_newest_first():
    await _seed_run("r1", started_at="2026-06-05T10:00:00Z")
    await _seed_run("r2", started_at="2026-06-05T12:00:00Z")
    await _seed_run("r3", started_at="2026-06-05T11:00:00Z")

    rows = await queries.list_runs()
    assert [r["run_id"] for r in rows] == ["r2", "r3", "r1"]
    # Carries the columns the history view needs.
    assert rows[0]["status"] == "running"
    assert rows[0]["triggered_by"] == "cli"
    assert rows[0]["accounts_total"] == 1


async def test_list_runs_honours_limit():
    await _seed_run("r1", started_at="2026-06-05T10:00:00Z")
    await _seed_run("r2", started_at="2026-06-05T12:00:00Z")
    rows = await queries.list_runs(limit=1)
    assert [r["run_id"] for r in rows] == ["r2"]


# --- get_run: run row + account_runs snapshot ---


async def test_get_run_returns_none_for_unknown_run():
    assert await queries.get_run("nope") is None


async def test_get_run_returns_run_and_account_snapshot():
    await _seed_run("r1", started_at="t", accounts_total=2)
    await account_runs.upsert_account_run(
        run_id="r1",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="t",
    )
    await account_runs.upsert_account_run(
        run_id="r1",
        domain="globex.com",
        company_name="Globex",
        country="DE",
        status="pending",
        started_at="t",
    )
    await account_runs.mark_status(
        run_id="r1",
        domain="acme.com",
        status="done",
        finished_at="t2",
        score=8,
        tier="Tier 1",
        confidence="high",
        signals_count=3,
    )

    result = await queries.get_run("r1")
    assert result is not None
    assert result["run"]["run_id"] == "r1"
    assert result["run"]["accounts_total"] == 2

    accts = result["accounts"]
    # Snapshot is ordered deterministically by domain.
    assert [a["domain"] for a in accts] == ["acme.com", "globex.com"]
    acme = accts[0]
    assert acme["status"] == "done"
    assert acme["score"] == 8
    assert acme["tier"] == "Tier 1"
    assert acme["signals_count"] == 3
    assert accts[1]["status"] == "pending"


async def test_get_run_with_no_accounts_returns_empty_snapshot():
    await _seed_run("r1", started_at="t", accounts_total=0)
    result = await queries.get_run("r1")
    assert result is not None
    assert result["accounts"] == []


# --- get_account_detail: detail + score/tier diff ---


async def test_get_account_detail_returns_none_for_unknown_account():
    await _seed_run("r1", started_at="t")
    assert await queries.get_account_detail("r1", "missing.com") is None


async def test_get_account_detail_returns_persisted_json_and_no_prior_diff():
    await _seed_run("r1", started_at="t")
    await account_runs.upsert_account_run(
        run_id="r1",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="t",
    )
    await account_runs.mark_status(
        run_id="r1",
        domain="acme.com",
        status="done",
        finished_at="t2",
        score=8,
        tier="Tier 1",
        confidence="high",
        needs_human_research=False,
        signals_count=2,
        signals_json='[{"type": "hiring"}]',
        score_json='{"score": 8, "why_fit": ["fast"]}',
    )

    detail = await queries.get_account_detail("r1", "acme.com")
    assert detail is not None
    acct = detail["account"]
    assert acct["domain"] == "acme.com"
    assert acct["score"] == 8
    assert acct["score_json"] == '{"score": 8, "why_fit": ["fast"]}'
    assert acct["signals_json"] == '[{"type": "hiring"}]'
    # No prior real run for this domain → no diff baseline.
    assert detail["diff"] == {"changed": False, "prev_score": None, "prev_tier": None}


async def test_get_account_detail_computes_diff_against_prior_real_run():
    # Prior real run scored acme at 6 / Tier 2.
    await _seed_run("r_old", started_at="2026-06-01T00:00:00Z")
    await account_runs.upsert_account_run(
        run_id="r_old",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="2026-06-01T00:00:00Z",
    )
    await account_runs.mark_status(
        run_id="r_old",
        domain="acme.com",
        status="done",
        finished_at="2026-06-01T01:00:00Z",
        score=6,
        tier="Tier 2",
        confidence="medium",
    )

    # New run scores acme at 8 / Tier 1.
    await _seed_run("r_new", started_at="2026-06-05T00:00:00Z")
    await account_runs.upsert_account_run(
        run_id="r_new",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="2026-06-05T00:00:00Z",
    )
    await account_runs.mark_status(
        run_id="r_new",
        domain="acme.com",
        status="done",
        finished_at="2026-06-05T01:00:00Z",
        score=8,
        tier="Tier 1",
        confidence="high",
    )

    detail = await queries.get_account_detail("r_new", "acme.com")
    assert detail is not None
    assert detail["diff"] == {"changed": True, "prev_score": 6, "prev_tier": "Tier 2"}


async def test_get_account_detail_diff_ignores_dry_run_baseline():
    # A persisted dry-run must never become the diff baseline (plan #3).
    await _seed_run("r_dry", started_at="2026-06-01T00:00:00Z", dry_run=True)
    await account_runs.upsert_account_run(
        run_id="r_dry",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="2026-06-01T00:00:00Z",
    )
    await account_runs.mark_status(
        run_id="r_dry",
        domain="acme.com",
        status="done",
        finished_at="2026-06-01T01:00:00Z",
        score=3,
        tier="Tier 3",
        confidence="low",
    )

    await _seed_run("r_new", started_at="2026-06-05T00:00:00Z")
    await account_runs.upsert_account_run(
        run_id="r_new",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="2026-06-05T00:00:00Z",
    )
    await account_runs.mark_status(
        run_id="r_new",
        domain="acme.com",
        status="done",
        finished_at="2026-06-05T01:00:00Z",
        score=8,
        tier="Tier 1",
        confidence="high",
    )

    detail = await queries.get_account_detail("r_new", "acme.com")
    assert detail is not None
    # The dry-run row is invisible to the baseline → no prior, no diff.
    assert detail["diff"] == {"changed": False, "prev_score": None, "prev_tier": None}


async def test_get_account_detail_diff_unchanged_when_score_and_tier_match():
    await _seed_run("r_old", started_at="2026-06-01T00:00:00Z")
    await account_runs.upsert_account_run(
        run_id="r_old",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="2026-06-01T00:00:00Z",
    )
    await account_runs.mark_status(
        run_id="r_old",
        domain="acme.com",
        status="done",
        finished_at="2026-06-01T01:00:00Z",
        score=8,
        tier="Tier 1",
        confidence="high",
    )

    await _seed_run("r_new", started_at="2026-06-05T00:00:00Z")
    await account_runs.upsert_account_run(
        run_id="r_new",
        domain="acme.com",
        company_name="Acme",
        country="US",
        status="pending",
        started_at="2026-06-05T00:00:00Z",
    )
    await account_runs.mark_status(
        run_id="r_new",
        domain="acme.com",
        status="done",
        finished_at="2026-06-05T01:00:00Z",
        score=8,
        tier="Tier 1",
        confidence="high",
    )

    detail = await queries.get_account_detail("r_new", "acme.com")
    assert detail is not None
    assert detail["diff"] == {"changed": False, "prev_score": 8, "prev_tier": "Tier 1"}


def test_events_module_imported():
    # Keep the writeback-events writer referenced so the diff semantics here stay
    # aligned with `events.record_event`'s `changed`/`prev_*` derivation.
    assert hasattr(events, "record_event")


# --- companies_for_run: resume reconstructs input from runs.companies_json (plan #1) ---


async def test_companies_for_run_reconstructs_from_companies_json():
    # The run was launched with a specific input set (incl. `description`); resume must
    # reconstruct EXACTLY that from runs.companies_json — never from repo-local CSV (#1).
    from duvo.models import Company

    launched = [
        Company(name="Acme", domain="acme.com", country="US", description="ERP migration"),
        Company(name="Globex", domain="globex.com", country="DE", description=""),
    ]
    companies_json = json.dumps([c.model_dump() for c in launched], ensure_ascii=False)
    await runs.start_run(
        run_id="r_resume",
        run_date="2026-06-05",
        started_at="2026-06-05T10:00:00Z",
        dry_run=False,
        concurrency=2,
        model="m",
        app_env="dev",
        accounts_total=2,
        triggered_by="user@example.com",
        companies_json=companies_json,
    )

    reconstructed = await queries.companies_for_run("r_resume")
    assert reconstructed == launched
    # `description` round-trips so an uploaded CSV run is faithfully recoverable.
    assert reconstructed[0].description == "ERP migration"


async def test_companies_for_run_returns_empty_for_missing_run():
    assert await queries.companies_for_run("nope") == []


async def test_companies_for_run_returns_empty_when_companies_json_null():
    # A legacy run (or one persisted before companies_json existed) yields [] — the
    # caller falls back; reconstruction never raises on missing input.
    await runs.start_run(
        run_id="r_legacy",
        run_date="2026-06-05",
        started_at="2026-06-05T10:00:00Z",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=0,
    )
    assert await queries.companies_for_run("r_legacy") == []
