"""Tests for the API-drivable orchestrator.run() refactor (plan #1, #3, #6, #6a, #10).

These cover the NEW behavior added on top of the CLI guarantees exercised in
tests/test_main.py: the generalized run() signature, persist decoupled from
dry_run, manage_clients gating of shared-client teardown, cancellation/interrupt
statuses, the run-scoped write-back idempotency lifecycle, and agent_log_json
persistence. Everything is offline — the pipeline stages and clients are mocked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duvo import orchestrator
from duvo.models import Company
from duvo.store import db as _store_db
from tests.conftest import make_score

PATCH_BASE = "duvo.orchestrator"


@pytest.fixture(autouse=True)
def _isolate_store_db(monkeypatch, tmp_path):
    """Every test persists to a throwaway tmp DB, never state/duvo.db."""
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    _store_db._INITED.clear()
    yield
    _store_db._INITED.clear()


def _company(domain: str = "acme.com", name: str = "Acme") -> Company:
    return Company(name=name, domain=domain, country="US", description="d")


def _patch_pipeline(score, router=None):
    """Patch the three agent stages so _process_account runs without LLM/network."""

    async def fake_scout(company, log=None):
        return []

    async def fake_analyst(company, signals, log=None):
        return score

    async def default_router(rr, dry_run, test_email, log=None):
        rr.crm_status = "attio company rec_1 (ICP 8)"
        rr.slack_status = "posted to #sales"
        rr.outreach_status = "contact queued in Brevo review list 7"

    return (
        patch.object(orchestrator, "scout_all", fake_scout),
        patch.object(orchestrator, "run_analyst", fake_analyst),
        patch.object(orchestrator, "run_router", router or default_router),
    )


# ---------------------------------------------------------------------------
# #1 generalized signature + companies passed in + companies_json persisted
# ---------------------------------------------------------------------------


class TestGeneralizedSignature:
    async def test_companies_passed_explicitly_skips_load_companies(self):
        """When companies are passed in, run() must NOT touch load_companies()."""
        mock_load = MagicMock(side_effect=AssertionError("load_companies must not be called"))
        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "load_companies", mock_load),
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="api-1",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        mock_load.assert_not_called()

    async def test_run_id_explicit_is_used_as_runs_row_id(self):
        from duvo.store import db

        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="explicit-run-id",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        row = await db.query_one("SELECT run_id, triggered_by FROM runs LIMIT 1")
        assert row["run_id"] == "explicit-run-id"
        assert row["triggered_by"] == "user@duvo.com"

    async def test_companies_json_persisted_on_runs_row(self):
        from duvo.store import db

        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="api-cjson",
                companies=[_company(domain="acme.com", name="Acme")],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        row = await db.query_one("SELECT companies_json FROM runs WHERE run_id=?", ("api-cjson",))
        assert row["companies_json"] is not None
        import json

        parsed = json.loads(row["companies_json"])
        assert parsed[0]["domain"] == "acme.com"
        assert parsed[0]["name"] == "Acme"


# ---------------------------------------------------------------------------
# #20 live SSE feed: run() publishes account + status transitions to the bus
# ---------------------------------------------------------------------------


class TestLiveEventPublishing:
    async def test_run_publishes_account_running_done_and_status_done(self):
        from duvo.infra import events

        score = make_score(domain="acme.com", score=8, tier="Tier 1", confidence="high")
        p1, p2, p3 = _patch_pipeline(score)
        queue = events.subscribe("evt-run")
        try:
            with (
                patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
                p1,
                p2,
                p3,
            ):
                await orchestrator.run(
                    run_id="evt-run",
                    companies=[_company(domain="acme.com")],
                    triggered_by="user@duvo.com",
                    dry_run=True,
                    test_email="t@e.com",
                    persist=True,
                    manage_clients=False,
                )
        finally:
            events.unsubscribe("evt-run", queue)

        drained = []
        while not queue.empty():
            drained.append(queue.get_nowait())

        account_evs = [e for e in drained if e["type"] == "account"]
        status_evs = [e for e in drained if e["type"] == "status"]
        statuses = [e["status"] for e in account_evs]
        assert "running" in statuses
        assert "done" in statuses
        done_ev = next(e for e in account_evs if e["status"] == "done")
        assert done_ev["account"] == "acme.com"
        assert done_ev["score"] == 8
        assert done_ev["tier"] == "Tier 1"
        # a terminal run status closes the SSE stream (#11)
        assert any(e["status"] == "done" for e in status_evs)

    async def test_failed_account_publishes_failed_event(self):
        from duvo.infra import events

        async def boom_analyst(company, signals, log=None):
            raise RuntimeError("analyst exploded")

        score = make_score(domain="acme.com")
        p1, _p2, p3 = _patch_pipeline(score)
        queue = events.subscribe("evt-fail")
        try:
            with (
                patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
                p1,
                patch.object(orchestrator, "run_analyst", boom_analyst),
                p3,
            ):
                await orchestrator.run(
                    run_id="evt-fail",
                    companies=[_company(domain="acme.com")],
                    triggered_by="user@duvo.com",
                    dry_run=True,
                    test_email="t@e.com",
                    persist=True,
                    manage_clients=False,
                )
        finally:
            events.unsubscribe("evt-fail", queue)

        drained = []
        while not queue.empty():
            drained.append(queue.get_nowait())
        statuses = [e["status"] for e in drained if e["type"] == "account"]
        assert "running" in statuses
        assert "failed" in statuses


# ---------------------------------------------------------------------------
# #3 persist decoupled from dry_run (server path persists a dry-run)
# ---------------------------------------------------------------------------


class TestPersistDecoupledFromDryRun:
    async def test_server_dry_run_persists_when_persist_true(self):
        from duvo.store import db

        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="srv-dry",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=True,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        run_row = await db.query_one("SELECT * FROM runs WHERE run_id=?", ("srv-dry",))
        assert run_row is not None
        assert run_row["dry_run"] == 1
        acc = await db.query_one("SELECT * FROM account_runs WHERE run_id=?", ("srv-dry",))
        assert acc is not None
        assert acc["status"] == "done"

    async def test_persist_false_persists_nothing_even_for_real_run(self):
        from duvo.store import db

        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="nopersist",
                companies=[_company()],
                triggered_by="cli",
                dry_run=False,
                test_email="t@e.com",
                persist=False,
                manage_clients=True,
            )
        assert await db.query_one("SELECT * FROM runs LIMIT 1") is None


# ---------------------------------------------------------------------------
# Concurrent-runs client lifecycle (#decision Option 2): manage_clients gating
# ---------------------------------------------------------------------------


class TestManageClients:
    async def test_manage_clients_false_does_not_close_shared_clients(self):
        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        mock_aclose = AsyncMock()
        mock_exa_close = AsyncMock()
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            patch("duvo.infra.http_client.aclose", mock_aclose),
            patch.object(orchestrator.exa_tool, "aclose", mock_exa_close),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="keep-clients",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        mock_aclose.assert_not_called()
        mock_exa_close.assert_not_called()

    async def test_manage_clients_true_closes_shared_clients(self):
        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        mock_aclose = AsyncMock()
        mock_exa_close = AsyncMock()
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            patch("duvo.infra.http_client.aclose", mock_aclose),
            patch.object(orchestrator.exa_tool, "aclose", mock_exa_close),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="close-clients",
                companies=[_company()],
                triggered_by="cli",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=True,
            )
        mock_aclose.assert_awaited_once()
        mock_exa_close.assert_awaited_once()


# ---------------------------------------------------------------------------
# #6 cancellation / interrupt semantics
# ---------------------------------------------------------------------------


class TestCancellation:
    async def test_account_cancelled_marks_cancelled_not_done(self):
        """A CancelledError mid-account marks the account 'cancelled', never 'done'."""
        from duvo.store import db

        async def cancelling_analyst(company, signals, log=None):
            raise asyncio.CancelledError()

        async def fake_scout(company, log=None):
            return []

        with (
            patch.object(orchestrator, "scout_all", fake_scout),
            patch.object(orchestrator, "run_analyst", cancelling_analyst),
            patch.object(orchestrator, "run_router", AsyncMock()),
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
        ):
            with pytest.raises(asyncio.CancelledError):
                await orchestrator.run(
                    run_id="cancel-run",
                    companies=[_company()],
                    triggered_by="user@duvo.com",
                    dry_run=False,
                    test_email="t@e.com",
                    persist=True,
                    manage_clients=False,
                )
        acc = await db.query_one("SELECT status FROM account_runs WHERE run_id=?", ("cancel-run",))
        assert acc["status"] == "cancelled"

    async def test_run_cancelled_marks_run_interrupted(self):
        from duvo.store import db

        async def cancelling_analyst(company, signals, log=None):
            raise asyncio.CancelledError()

        async def fake_scout(company, log=None):
            return []

        with (
            patch.object(orchestrator, "scout_all", fake_scout),
            patch.object(orchestrator, "run_analyst", cancelling_analyst),
            patch.object(orchestrator, "run_router", AsyncMock()),
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
        ):
            with pytest.raises(asyncio.CancelledError):
                await orchestrator.run(
                    run_id="interrupt-run",
                    companies=[_company()],
                    triggered_by="user@duvo.com",
                    dry_run=False,
                    test_email="t@e.com",
                    persist=True,
                    manage_clients=False,
                )
        run_row = await db.query_one("SELECT status FROM runs WHERE run_id=?", ("interrupt-run",))
        assert run_row["status"] == "interrupted"


# ---------------------------------------------------------------------------
# #6a run-scoped write-back idempotency lifecycle
# ---------------------------------------------------------------------------


class TestIdempotencyLifecycle:
    async def test_real_run_records_delivery_succeeded_then_event(self):
        """A real run drives set_delivery_status (attempting → succeeded) per channel."""
        from duvo.store import db

        score = make_score(domain="acme.com", score=8, tier="Tier 1")
        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="idem-1",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        rows = await db.query_all(
            "SELECT channel, delivery_status FROM writeback_events WHERE run_id=?", ("idem-1",)
        )
        by_channel = {r["channel"]: r["delivery_status"] for r in rows}
        assert by_channel.get("slack") == "succeeded"
        assert by_channel.get("outreach") == "succeeded"

    async def test_same_run_id_retry_suppresses_router(self):
        """Re-running the SAME run_id after a succeeded delivery suppresses the router."""
        from duvo.store import db

        score = make_score(domain="acme.com", score=8, tier="Tier 1")

        # First pass: succeeds.
        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="idem-retry",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )

        # Second pass: same run_id; router must be suppressed (not called).
        router_calls = []

        async def spy_router(rr, dry_run, test_email, log=None):
            router_calls.append(rr.score.domain)
            rr.slack_status = "posted again"

        p1b, p2b, p3b = _patch_pipeline(score, router=spy_router)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1b,
            p2b,
            p3b,
        ):
            await orchestrator.run(
                run_id="idem-retry",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        assert router_calls == [], "router must be suppressed on same-run_id retry of succeeded"
        # The succeeded delivery rows are still present.
        rows = await db.query_all(
            "SELECT channel, delivery_status FROM writeback_events WHERE run_id=?", ("idem-retry",)
        )
        assert any(r["delivery_status"] == "succeeded" for r in rows)

    async def test_fresh_run_id_refires_router(self):
        """A fresh run_id (different from the prior succeeded one) re-fires the router."""
        score = make_score(domain="acme.com", score=8, tier="Tier 1")

        p1, p2, p3 = _patch_pipeline(score)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1,
            p2,
            p3,
        ):
            await orchestrator.run(
                run_id="run-A",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )

        router_calls = []

        async def spy_router(rr, dry_run, test_email, log=None):
            router_calls.append(rr.score.domain)
            rr.slack_status = "posted"

        p1b, p2b, p3b = _patch_pipeline(score, router=spy_router)
        with (
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
            p1b,
            p2b,
            p3b,
        ):
            await orchestrator.run(
                run_id="run-B",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        assert router_calls == ["acme.com"], "a fresh run_id must re-fire the router"


# ---------------------------------------------------------------------------
# #10 persist agent_log_json at account completion
# ---------------------------------------------------------------------------


class TestAgentLogPersistence:
    async def test_agent_log_json_persisted_on_done(self):
        from duvo.store import db

        score = make_score(domain="acme.com")

        async def fake_scout(company, log=None):
            if log is not None:
                log.append("scout:exa_search()")
            return []

        async def fake_analyst(company, signals, log=None):
            if log is not None:
                log.append("analyst:record_assessment()")
            return score

        async def fake_router(rr, dry_run, test_email, log=None):
            if log is not None:
                log.append("router:slack_alert()")
            rr.slack_status = "posted"

        with (
            patch.object(orchestrator, "scout_all", fake_scout),
            patch.object(orchestrator, "run_analyst", fake_analyst),
            patch.object(orchestrator, "run_router", fake_router),
            patch.object(orchestrator, "generate_report", MagicMock(return_value="r.html")),
        ):
            await orchestrator.run(
                run_id="log-run",
                companies=[_company()],
                triggered_by="user@duvo.com",
                dry_run=False,
                test_email="t@e.com",
                persist=True,
                manage_clients=False,
            )
        row = await db.query_one(
            "SELECT agent_log_json FROM account_runs WHERE run_id=?", ("log-run",)
        )
        assert row["agent_log_json"] is not None
        import json

        log_lines = json.loads(row["agent_log_json"])
        assert "scout:exa_search()" in log_lines
        assert "analyst:record_assessment()" in log_lines
        assert "router:slack_alert()" in log_lines
