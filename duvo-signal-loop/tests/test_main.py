"""Tests for main.py — orchestrator: load_companies + async run."""

import asyncio
import csv
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duvo import orchestrator
from duvo.models import Company, RunResult, Signal
from duvo.store import db as _store_db
from tests.conftest import make_score


@pytest.fixture(autouse=True)
def _isolate_store_db(monkeypatch, tmp_path):
    """Every test in this module persists to a throwaway tmp DB, never state/duvo.db."""
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    _store_db._INITED.clear()
    yield
    _store_db._INITED.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal() -> Signal:
    return Signal(
        signal_type="hiring",
        title="Hiring for ops roles",
        summary="Company is hiring fast.",
        source_url="https://example.com/job",
        published_date="2024-01-10",
    )


def _make_run_result(company_name: str = "Acme Corp", score: int = 8) -> RunResult:
    icp = make_score(company_name=company_name, score=score)
    return RunResult(
        score=icp,
        signals=[_make_signal()],
        crm_status="created",
        slack_status="sent",
        outreach_status="enrolled",
        agent_log=["tool_a()", "tool_b()"],
    )


def _write_csv(path, rows: list[dict]) -> None:
    """Write a CSV to *path* with keys from the first row as headers."""
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


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


# ---------------------------------------------------------------------------
# load_companies tests (stay sync — load_companies is a sync function)
# ---------------------------------------------------------------------------


class TestLoadCompanies:
    def test_parses_csv_correctly(self, tmp_path):
        from duvo.orchestrator import load_companies

        csv_path = str(tmp_path / "test_companies.csv")
        _write_csv(
            csv_path,
            [
                {
                    "name": "Acme Corp",
                    "domain": "acme.com",
                    "country": "US",
                    "description": "Acme desc",
                },
                {
                    "name": "Beta Ltd",
                    "domain": "beta.com",
                    "country": "UK",
                    "description": "Beta desc",
                },
            ],
        )

        companies = load_companies(csv_path)

        assert len(companies) == 2
        assert isinstance(companies[0], Company)
        assert companies[0].name == "Acme Corp"
        assert companies[0].domain == "acme.com"
        assert companies[0].country == "US"
        assert companies[0].description == "Acme desc"
        assert companies[1].name == "Beta Ltd"

    def test_real_companies_csv_loads_10(self):
        from duvo.orchestrator import load_companies

        companies = load_companies("companies.csv")
        assert len(companies) == 10

    def test_returns_list_of_company_objects(self, tmp_path):
        from duvo.orchestrator import load_companies

        csv_path = str(tmp_path / "c.csv")
        _write_csv(
            csv_path,
            [
                {"name": "X Corp", "domain": "x.com", "country": "DE", "description": ""},
            ],
        )
        companies = load_companies(csv_path)
        assert all(isinstance(c, Company) for c in companies)


# ---------------------------------------------------------------------------
# run tests — all async, all external calls patched
# ---------------------------------------------------------------------------

PATCH_BASE = "duvo.orchestrator"


def _two_companies() -> list[Company]:
    return [
        Company(name="Acme Corp", domain="acme.com", country="US", description="Desc A"),
        Company(name="Beta Ltd", domain="beta.com", country="UK", description="Desc B"),
    ]


def _base_patches(companies: list[Company] | None = None):
    """Return a dict of patch targets → mock objects for the standard happy-path setup."""
    if companies is None:
        companies = _two_companies()

    mock_load = MagicMock(return_value=companies)
    mock_scout = AsyncMock(return_value=[_make_signal()])
    mock_analyst = AsyncMock(side_effect=lambda c, sigs, log=None: make_score(company_name=c.name))
    mock_router = AsyncMock(return_value=None)
    mock_report = MagicMock(return_value="output/run-report.html")
    mock_aclose = AsyncMock(return_value=None)

    return {
        "load": mock_load,
        "scout": mock_scout,
        "analyst": mock_analyst,
        "router": mock_router,
        "report": mock_report,
        "aclose": mock_aclose,
    }


class _SpyTracing:
    """Stand-in for duvo.orchestrator.tracing that records span/trace_context calls."""

    def __init__(self):
        self.spans = []
        self.trace_contexts = []

    def init_tracing(self):
        pass

    def flush(self):
        pass

    def obs_for(self, tool_name):
        return ("tool", "🔧")

    @contextmanager
    def span(self, **kwargs):
        self.spans.append(kwargs)

        class _Rec:
            def update(self, **kw):
                pass

        yield _Rec()

    @contextmanager
    def trace_context(self, **kwargs):
        self.trace_contexts.append(kwargs)
        yield None


class TestRun:
    """Test the async orchestrator run() with all external calls patched."""

    async def test_processes_all_companies(self):
        from duvo.orchestrator import run

        mocks = _base_patches()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        assert mocks["scout"].call_count == 2
        assert mocks["analyst"].call_count == 2
        assert mocks["router"].call_count == 2

    async def test_run_opens_single_batch_chain_span(self):
        from duvo.orchestrator import run

        mocks = _base_patches()
        spy = _SpyTracing()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
            patch(f"{PATCH_BASE}.tracing", spy),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        # Exactly one batch-level run span (chain) wrapping the gather.
        run_spans = [
            s
            for s in spy.spans
            if s.get("as_type") == "chain" and s.get("name", "").startswith("🚀 run ")
        ]
        assert len(run_spans) == 1
        # One account-run chain span per company, nested under it.
        account_spans = [s for s in spy.spans if s.get("name") == "🎯 account-run"]
        assert len(account_spans) == 2
        # Session/tags propagated once at batch level; session_id == the run id.
        assert len(spy.trace_contexts) == 1
        tc = spy.trace_contexts[0]
        assert "duvo-signal-loop" in tc["tags"]
        assert tc["session_id"] == tc["metadata"]["batch_run_id"]
        # The run trace is explicitly named (== the run span name) so every nested
        # account-run observation is attributed to this one trace in Langfuse's
        # observation list (otherwise the Trace Name column is blank and the
        # children look like standalone runs).
        assert tc["trace_name"] == run_spans[0]["name"]

    async def test_limit_is_respected(self):
        from duvo.orchestrator import run

        companies = [
            Company(name=f"Co{i}", domain=f"co{i}.com", country="US", description="")
            for i in range(5)
        ]
        mocks = _base_patches(companies=companies)
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=3)

        assert mocks["scout"].call_count == 3
        assert mocks["analyst"].call_count == 3
        assert mocks["router"].call_count == 3

    async def test_run_router_receives_dry_run_flag(self):
        from duvo.orchestrator import run

        mocks = _base_patches()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=True, test_email="rep@test.com", limit=None)

        for call in mocks["router"].call_args_list:
            args, kwargs = call
            # run_router(rr, dry_run, test_email, agent_log) — positional args
            assert args[1] is True  # dry_run positional

    async def test_generate_report_called_once_with_results_list(self):
        from duvo.orchestrator import run

        mocks = _base_patches()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        assert mocks["report"].call_count == 1
        results_arg = mocks["report"].call_args[0][0]
        assert isinstance(results_arg, list)
        assert len(results_arg) == 2
        assert all(isinstance(r, RunResult) for r in results_arg)

    async def test_run_results_have_agent_log_populated(self):
        """Each RunResult passed to generate_report must carry the log list from the pipeline."""
        from duvo.orchestrator import run

        mocks = _base_patches()

        async def fake_router(rr, dry_run, test_email, log=None):
            if log is not None:
                log.append("router:crm_write()")

        mocks["router"].side_effect = fake_router

        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        results_arg = mocks["report"].call_args[0][0]
        for rr in results_arg:
            assert "router:crm_write()" in rr.agent_log

    async def test_configure_logging_is_invoked(self):
        from duvo.orchestrator import run

        mocks = _base_patches()
        mock_configure = MagicMock()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch(f"{PATCH_BASE}.configure_logging", mock_configure),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        mock_configure.assert_called_once()

    async def test_run_router_receives_test_email(self):
        from duvo.orchestrator import run

        mocks = _base_patches()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=False, test_email="custom@example.com", limit=None)

        for call in mocks["router"].call_args_list:
            args, kwargs = call
            assert args[2] == "custom@example.com"  # test_email positional

    async def test_one_account_failure_does_not_abort_batch_and_report_still_generated(self):
        """If one account raises inside run_analyst, run() must not raise, must still call
        generate_report once, and the results passed to it must contain only the successful
        accounts (the failing one is skipped)."""
        from duvo.orchestrator import run

        companies = [
            Company(name="Good Corp", domain="good.com", country="US", description="Fine"),
            Company(name="Bad Corp", domain="bad.com", country="DE", description="Boom"),
            Company(name="Also Good", domain="alsogood.com", country="PL", description="Fine"),
        ]

        async def analyst_side_effect(c, sigs, log=None):
            if c.name == "Bad Corp":
                raise RuntimeError("Simulated transient API failure")
            return make_score(company_name=c.name)

        mock_load = MagicMock(return_value=companies)
        mock_scout = AsyncMock(return_value=[_make_signal()])
        mock_analyst = AsyncMock(side_effect=analyst_side_effect)
        mock_router = AsyncMock(return_value=None)
        mock_report = MagicMock(return_value="output/run-report.html")
        mock_aclose = AsyncMock(return_value=None)

        # Must not raise
        with (
            patch(f"{PATCH_BASE}.load_companies", mock_load),
            patch(f"{PATCH_BASE}.scout_all", mock_scout),
            patch(f"{PATCH_BASE}.run_analyst", mock_analyst),
            patch(f"{PATCH_BASE}.run_router", mock_router),
            patch(f"{PATCH_BASE}.generate_report", mock_report),
            patch("duvo.infra.http_client.aclose", mock_aclose),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        # generate_report must be called exactly once
        assert mock_report.call_count == 1

        # Results must contain only the 2 successful accounts, not Bad Corp
        results_arg = mock_report.call_args[0][0]
        assert isinstance(results_arg, list)
        assert len(results_arg) == 2
        successful_names = {rr.score.company_name for rr in results_arg}
        assert "Good Corp" in successful_names
        assert "Also Good" in successful_names
        assert "Bad Corp" not in successful_names

    async def test_http_client_aclose_awaited_on_success(self):
        """duvo.infra.http_client.aclose() must be awaited after a successful run."""
        from duvo.orchestrator import run

        mocks = _base_patches()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        mocks["aclose"].assert_awaited_once()

    async def test_http_client_aclose_awaited_when_account_fails(self):
        """duvo.infra.http_client.aclose() must be awaited in finally even when an account raises."""
        from duvo.orchestrator import run

        mock_aclose = AsyncMock(return_value=None)
        mock_load = MagicMock(return_value=_two_companies())
        mock_report = MagicMock(return_value="output/run-report.html")

        # Make all accounts fail
        mock_scout = AsyncMock(side_effect=RuntimeError("Network error"))

        with (
            patch(f"{PATCH_BASE}.load_companies", mock_load),
            patch(f"{PATCH_BASE}.scout_all", mock_scout),
            patch(f"{PATCH_BASE}.run_analyst", AsyncMock()),
            patch(f"{PATCH_BASE}.run_router", AsyncMock()),
            patch(f"{PATCH_BASE}.generate_report", mock_report),
            patch("duvo.infra.http_client.aclose", mock_aclose),
        ):
            # run() does not raise — errors are per-account isolated
            await run(dry_run=False, test_email="test@test.com", limit=None)

        # aclose must have been awaited despite all accounts failing
        mock_aclose.assert_awaited_once()
        # report still called with empty list
        assert mock_report.call_count == 1
        assert mock_report.call_args[0][0] == []

    async def test_concurrency_parameter_accepted_and_used(self):
        """run() accepts a concurrency kwarg and passes it to asyncio.Semaphore."""
        from duvo.orchestrator import run

        captured_values: list[int] = []
        real_semaphore = asyncio.Semaphore

        def fake_semaphore(value: int):
            captured_values.append(value)
            return real_semaphore(value)

        mocks = _base_patches()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
            patch(f"{PATCH_BASE}.asyncio.Semaphore", fake_semaphore),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None, concurrency=7)

        assert captured_values == [7], f"Expected semaphore(7), got {captured_values}"

    async def test_concurrency_defaults_to_max_concurrent_accounts(self):
        """When concurrency=None, the semaphore uses MAX_CONCURRENT_ACCOUNTS from config."""
        from duvo import config as cfg
        from duvo.orchestrator import run

        captured_values: list[int] = []
        real_semaphore = asyncio.Semaphore

        def fake_semaphore(value: int):
            captured_values.append(value)
            return real_semaphore(value)

        mocks = _base_patches()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
            patch(f"{PATCH_BASE}.asyncio.Semaphore", fake_semaphore),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None, concurrency=None)

        assert captured_values == [cfg.MAX_CONCURRENT_ACCOUNTS], (
            f"Expected semaphore({cfg.MAX_CONCURRENT_ACCOUNTS}), got {captured_values}"
        )

    async def test_tracing_init_and_flush_invoked(self):
        from unittest.mock import MagicMock

        from duvo.orchestrator import run

        mocks = _base_patches()
        mock_init = MagicMock()
        mock_flush = MagicMock()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
            patch(f"{PATCH_BASE}.tracing.init_tracing", mock_init),
            patch(f"{PATCH_BASE}.tracing.flush", mock_flush),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        mock_init.assert_called_once()
        mock_flush.assert_called_once()

    async def test_account_timeout_excludes_slow_account_and_calls_generate_report(self):
        """When an account's pipeline hangs beyond ACCOUNT_TIMEOUT_SECONDS, that account
        is excluded from the results but run() does not raise and generate_report is still
        called with the successful results."""
        from duvo.orchestrator import run

        companies = [
            Company(name="Fast Corp", domain="fast.com", country="US", description="Fine"),
            Company(name="Slow Corp", domain="slow.com", country="DE", description="Hangs"),
        ]

        async def scout_side_effect(company, agent_log):
            if company.name == "Slow Corp":
                # Sleep far longer than the tiny patched account timeout
                await asyncio.sleep(10)
            return [_make_signal()]

        mock_load = MagicMock(return_value=companies)
        mock_scout = AsyncMock(side_effect=scout_side_effect)
        mock_analyst = AsyncMock(
            side_effect=lambda c, sigs, log=None: make_score(company_name=c.name)
        )
        mock_router = AsyncMock(return_value=None)
        mock_report = MagicMock(return_value="output/run-report.html")
        mock_aclose = AsyncMock(return_value=None)

        with (
            patch(f"{PATCH_BASE}.load_companies", mock_load),
            patch(f"{PATCH_BASE}.scout_all", mock_scout),
            patch(f"{PATCH_BASE}.run_analyst", mock_analyst),
            patch(f"{PATCH_BASE}.run_router", mock_router),
            patch(f"{PATCH_BASE}.generate_report", mock_report),
            patch("duvo.infra.http_client.aclose", mock_aclose),
            # Patch main.ACCOUNT_TIMEOUT_SECONDS — the module-level name _process_account reads
            patch(f"{PATCH_BASE}.ACCOUNT_TIMEOUT_SECONDS", 0.01),
        ):
            # Must not raise
            await run(dry_run=False, test_email="test@test.com", limit=None)

        # generate_report must still be called
        assert mock_report.call_count == 1

        # Only the fast account should appear; the slow one timed out and returns None
        results_arg = mock_report.call_args[0][0]
        assert isinstance(results_arg, list)
        successful_names = {rr.score.company_name for rr in results_arg}
        assert "Fast Corp" in successful_names
        assert "Slow Corp" not in successful_names


# ---------------------------------------------------------------------------
# Durable run-state persistence (store wiring)
# ---------------------------------------------------------------------------


class TestPersistence:
    async def test_real_run_persists_run_and_account_and_events(self, monkeypatch):
        from duvo.store import db

        monkeypatch.setattr(
            orchestrator,
            "load_companies",
            lambda *a, **k: [Company(name="Acme", domain="acme.com", country="US", description="")],
        )
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
        events = await db.query_all(
            "SELECT channel, action FROM writeback_events WHERE domain=?", ("acme.com",)
        )
        by_channel = {e["channel"]: e["action"] for e in events}
        assert by_channel == {"crm": "upserted", "slack": "alerted", "outreach": "queued"}

    async def test_dry_run_does_not_persist(self, monkeypatch):
        from duvo.store import db

        monkeypatch.setattr(
            orchestrator,
            "load_companies",
            lambda *a, **k: [Company(name="Acme", domain="acme.com", country="US", description="")],
        )
        score = make_score(domain="acme.com")
        p1, p2, p3 = _patch_pipeline(score)
        with p1, p2, p3:
            await orchestrator.run(dry_run=True, test_email="t@e.com", limit=None)
        assert await db.query_one("SELECT * FROM runs LIMIT 1") is None
        assert await db.query_one("SELECT * FROM account_runs LIMIT 1") is None
        assert await db.query_one("SELECT * FROM writeback_events LIMIT 1") is None

    async def test_event_records_diff_vs_prior_run(self, monkeypatch):
        from duvo.store import account_runs, db, runs

        # Seed a prior done run with score 5.
        await runs.start_run(
            run_id="old",
            run_date="2026-06-01",
            started_at="t",
            dry_run=False,
            concurrency=1,
            model="m",
            app_env="dev",
            accounts_total=1,
        )
        await account_runs.upsert_account_run(
            run_id="old",
            domain="acme.com",
            company_name="Acme",
            country="US",
            status="running",
            started_at="t",
        )
        await account_runs.mark_status(
            run_id="old",
            domain="acme.com",
            status="done",
            finished_at="2026-06-01T00:00:00Z",
            score=5,
            tier="Tier 2",
            confidence="medium",
            needs_human_research=False,
            signals_count=0,
            signals_json="[]",
            score_json="{}",
            error=None,
        )

        monkeypatch.setattr(
            orchestrator,
            "load_companies",
            lambda *a, **k: [Company(name="Acme", domain="acme.com", country="US", description="")],
        )
        score = make_score(domain="acme.com", score=8, tier="Tier 1")  # changed
        p1, p2, p3 = _patch_pipeline(score)
        with p1, p2, p3:
            await orchestrator.run(dry_run=False, test_email="t@e.com", limit=None)

        ev = await db.query_one(
            "SELECT changed, prev_score, prev_tier FROM writeback_events WHERE channel='crm' "
            "AND domain='acme.com' AND prev_score IS NOT NULL",
            (),
        )
        assert ev["changed"] == 1
        assert ev["prev_score"] == 5
        assert ev["prev_tier"] == "Tier 2"


# ---------------------------------------------------------------------------
# Crash-recovery flags: --resume and --skip-done-today
# ---------------------------------------------------------------------------


async def test_resume_skips_done_domains_for_run(monkeypatch):
    from duvo.store import account_runs, runs

    # Seed run "r-resume" with a.com already done, b.com not.
    await runs.start_run(
        run_id="r-resume",
        run_date="2026-06-05",
        started_at="t",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=2,
    )
    await account_runs.upsert_account_run(
        run_id="r-resume",
        domain="a.com",
        company_name="A",
        country="",
        status="running",
        started_at="t",
    )
    await account_runs.mark_status(
        run_id="r-resume",
        domain="a.com",
        status="done",
        finished_at="t2",
        score=7,
        tier="Tier 2",
        confidence="high",
        needs_human_research=False,
        signals_count=0,
        signals_json="[]",
        score_json="{}",
        error=None,
    )

    processed = []

    async def fake_scout(company, log=None):
        processed.append(company.domain)
        return []

    monkeypatch.setattr(
        orchestrator,
        "load_companies",
        lambda *a, **k: [
            Company(name="A", domain="a.com", country="", description=""),
            Company(name="B", domain="b.com", country="", description=""),
        ],
    )
    score = make_score(domain="b.com")
    with (
        patch.object(orchestrator, "scout_all", fake_scout),
        patch.object(orchestrator, "run_analyst", AsyncMock(return_value=score)),
        patch.object(orchestrator, "run_router", AsyncMock()),
    ):
        await orchestrator.run(
            dry_run=False, test_email="t@e.com", limit=None, resume_run_id="r-resume"
        )

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
