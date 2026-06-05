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
    await events.record_event(
        run_id="r1",
        domain="acme.com",
        channel="crm",
        provider="attio",
        status_text="attio company rec_1 (ICP 8)",
        changed=True,
        prev_score=5,
        prev_tier="Tier 2",
        created_at="t1",
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
    await events.record_event(
        run_id="r1",
        domain="a.com",
        channel="slack",
        provider="slack",
        status_text="refused: x",
        changed=False,
        prev_score=None,
        prev_tier=None,
        created_at="t1",
    )
    await events.record_event(
        run_id="r1",
        domain="a.com",
        channel="slack",
        provider="slack",
        status_text="posted to #sales",
        changed=True,
        prev_score=None,
        prev_tier=None,
        created_at="t2",
    )
    rows = await db.query_all(
        "SELECT action, status_text FROM writeback_events WHERE run_id=? AND domain=? AND channel=?",
        ("r1", "a.com", "slack"),
    )
    assert len(rows) == 1  # UNIQUE(run_id, domain, channel) → updated, not duplicated
    assert rows[0]["action"] == "alerted"
