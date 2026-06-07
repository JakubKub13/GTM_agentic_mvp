import pytest

from duvo.store import db, events, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


async def _start_run(run_id: str, *, dry_run: bool = False) -> None:
    await runs.start_run(
        run_id=run_id,
        run_date="d",
        started_at="t",
        dry_run=dry_run,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
    )


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


@pytest.mark.parametrize(
    "channel,status_text",
    [
        ("crm", "[dry-run] would upsert attio company"),
        ("slack", "[dry-run] would post to #sales"),
        ("outreach", "[DRY-RUN] would queue contact"),
    ],
)
def test_derive_action_dry_run_prefix_is_simulated(channel, status_text):
    # Plan #3: the `[dry-run]` prefix → action `simulated`, regardless of the verb.
    assert events.derive_action(channel, status_text) == "simulated"


def test_derive_action_dry_run_flag_is_simulated():
    # Plan #3: the run's dry_run flag threaded in also forces `simulated`.
    assert events.derive_action("slack", "posted to #sales", dry_run=True) == "simulated"
    assert events.derive_action("crm", "attio company rec_1", dry_run=True) == "simulated"


async def test_record_event_persists_row():
    await _start_run("r1")
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
    # A plain post-hoc record is final: it lands as `succeeded`.
    assert row["delivery_status"] == "succeeded"


async def test_record_event_upserts_on_rerun_same_channel():
    await _start_run("r1")
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


async def test_record_event_dry_run_flag_marks_simulated():
    # Plan #3: a dry-run row records action `simulated` (never alerted/queued/upserted).
    await _start_run("r1", dry_run=True)
    await events.record_event(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        status_text="posted to #sales",
        changed=True,
        prev_score=None,
        prev_tier=None,
        created_at="t1",
        dry_run=True,
    )
    row = await db.query_one(
        "SELECT action FROM writeback_events WHERE run_id=? AND domain=? AND channel=?",
        ("r1", "acme.com", "slack"),
    )
    assert row["action"] == "simulated"


# --- Delivery lifecycle (#6a) -------------------------------------------------


async def test_set_delivery_status_attempting_then_succeeded():
    await _start_run("r1")
    await events.set_delivery_status(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="attempting",
        created_at="t1",
    )
    row = await db.query_one(
        "SELECT delivery_status FROM writeback_events WHERE run_id=? AND domain=? AND channel=?",
        ("r1", "acme.com", "slack"),
    )
    assert row["delivery_status"] == "attempting"

    await events.set_delivery_status(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="succeeded",
        created_at="t2",
    )
    rows = await db.query_all(
        "SELECT delivery_status FROM writeback_events WHERE run_id=? AND domain=? AND channel=?",
        ("r1", "acme.com", "slack"),
    )
    assert len(rows) == 1  # same (run_id, domain, channel) → updated in place
    assert rows[0]["delivery_status"] == "succeeded"


# --- Run-scoped idempotency / resume decision (#6a) ---------------------------


async def test_idempotency_decision_no_prior_row_fires():
    await _start_run("r1")
    decision = await events.idempotency_decision(
        run_id="r1", domain="acme.com", channel="slack"
    )
    assert decision == "fire"


async def test_idempotency_decision_succeeded_is_suppressed():
    await _start_run("r1")
    await events.set_delivery_status(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="succeeded",
        created_at="t1",
    )
    decision = await events.idempotency_decision(
        run_id="r1", domain="acme.com", channel="slack"
    )
    assert decision == "suppress"


async def test_idempotency_decision_failed_is_retried():
    await _start_run("r1")
    await events.set_delivery_status(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="failed",
        created_at="t1",
    )
    decision = await events.idempotency_decision(
        run_id="r1", domain="acme.com", channel="slack"
    )
    assert decision == "fire"


async def test_idempotency_decision_dangling_attempting_is_surfaced():
    # Plan #6a: a crash mid-call leaves `attempting`; never silently re-fired.
    await _start_run("r1")
    await events.set_delivery_status(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="attempting",
        created_at="t1",
    )
    decision = await events.idempotency_decision(
        run_id="r1", domain="acme.com", channel="slack"
    )
    assert decision == "surface"


async def test_idempotency_decision_is_run_scoped_fresh_run_refires():
    # Plan #6a: scope is (run_id, domain, channel). A succeeded send in r1 must NOT
    # suppress the SAME domain+channel in a fresh run r2 (new run_id).
    await _start_run("r1")
    await events.set_delivery_status(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="succeeded",
        created_at="t1",
    )
    assert (
        await events.idempotency_decision(run_id="r1", domain="acme.com", channel="slack")
        == "suppress"
    )

    await _start_run("r2")
    assert (
        await events.idempotency_decision(run_id="r2", domain="acme.com", channel="slack")
        == "fire"
    )


async def test_idempotency_decision_is_channel_scoped():
    await _start_run("r1")
    await events.set_delivery_status(
        run_id="r1",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="succeeded",
        created_at="t1",
    )
    # A different channel for the same (run_id, domain) is independent.
    assert (
        await events.idempotency_decision(run_id="r1", domain="acme.com", channel="outreach")
        == "fire"
    )


async def test_idempotency_decision_ignores_simulated_dry_run_rows():
    # Plan #3: the idempotency guard ignores `simulated` rows AND the query JOINs runs
    # and filters dry_run=0 — a persisted dry-run can never suppress a real send.
    await _start_run("r_dry", dry_run=True)
    await events.record_event(
        run_id="r_dry",
        domain="acme.com",
        channel="slack",
        provider="slack",
        status_text="posted to #sales",
        changed=True,
        prev_score=None,
        prev_tier=None,
        created_at="t1",
        dry_run=True,
    )
    # Even within the same dry-run, the simulated row never suppresses.
    assert (
        await events.idempotency_decision(run_id="r_dry", domain="acme.com", channel="slack")
        == "fire"
    )


async def test_idempotency_decision_dry_run_does_not_suppress_real_run():
    # A persisted server dry-run for a domain must not suppress a later real run for it.
    await _start_run("r_dry", dry_run=True)
    await events.set_delivery_status(
        run_id="r_dry",
        domain="acme.com",
        channel="slack",
        provider="slack",
        delivery_status="succeeded",
        created_at="t1",
        dry_run=True,
    )
    await _start_run("r_real", dry_run=False)
    # The decision is run-scoped anyway, but assert the dry-run row is invisible to
    # the real run even if scopes were widened: it fires.
    assert (
        await events.idempotency_decision(run_id="r_real", domain="acme.com", channel="slack")
        == "fire"
    )
