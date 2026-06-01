"""Tests for main.py — orchestrator: load_companies + run."""
import csv
import os
from unittest.mock import MagicMock, patch, call

import pytest

from tests.conftest import make_score
from models import Company, RunResult, Signal


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


# ---------------------------------------------------------------------------
# load_companies tests
# ---------------------------------------------------------------------------

class TestLoadCompanies:
    def test_parses_csv_correctly(self, tmp_path):
        from main import load_companies

        csv_path = str(tmp_path / "test_companies.csv")
        _write_csv(csv_path, [
            {"name": "Acme Corp", "domain": "acme.com", "country": "US", "description": "Acme desc"},
            {"name": "Beta Ltd", "domain": "beta.com", "country": "UK", "description": "Beta desc"},
        ])

        companies = load_companies(csv_path)

        assert len(companies) == 2
        assert isinstance(companies[0], Company)
        assert companies[0].name == "Acme Corp"
        assert companies[0].domain == "acme.com"
        assert companies[0].country == "US"
        assert companies[0].description == "Acme desc"
        assert companies[1].name == "Beta Ltd"

    def test_real_companies_csv_loads_10(self):
        from main import load_companies

        companies = load_companies("companies.csv")
        assert len(companies) == 10

    def test_returns_list_of_company_objects(self, tmp_path):
        from main import load_companies

        csv_path = str(tmp_path / "c.csv")
        _write_csv(csv_path, [
            {"name": "X Corp", "domain": "x.com", "country": "DE", "description": ""},
        ])
        companies = load_companies(csv_path)
        assert all(isinstance(c, Company) for c in companies)


# ---------------------------------------------------------------------------
# run tests (all external calls patched)
# ---------------------------------------------------------------------------

PATCH_BASE = "main"


class TestRun:
    """Test the orchestrator run() function with all external calls mocked."""

    def _setup_mocks(
        self,
        mock_load,
        mock_scout,
        mock_analyst,
        mock_router,
        mock_report,
        companies: list[Company] | None = None,
    ):
        if companies is None:
            companies = [
                Company(name="Acme Corp", domain="acme.com", country="US", description="Desc A"),
                Company(name="Beta Ltd", domain="beta.com", country="UK", description="Desc B"),
            ]
        mock_load.return_value = companies
        mock_scout.return_value = [_make_signal()]
        mock_analyst.side_effect = lambda c, sigs, log=None: make_score(company_name=c.name)
        mock_router.return_value = None
        mock_report.return_value = "output/run-report.html"

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_processes_all_companies(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        from main import run

        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report)
        run(dry_run=False, test_email="test@test.com", limit=None)

        assert mock_scout.call_count == 2
        assert mock_analyst.call_count == 2
        assert mock_router.call_count == 2

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_limit_is_respected(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        from main import run

        companies = [
            Company(name=f"Co{i}", domain=f"co{i}.com", country="US", description="")
            for i in range(5)
        ]
        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report, companies=companies)
        run(dry_run=False, test_email="test@test.com", limit=3)

        assert mock_scout.call_count == 3
        assert mock_analyst.call_count == 3
        assert mock_router.call_count == 3

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_run_router_receives_dry_run_flag(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        from main import run

        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report)
        run(dry_run=True, test_email="rep@test.com", limit=None)

        # Every router call should receive dry_run=True
        for c in mock_router.call_args_list:
            # run_router(rr, dry_run, test_email, log) — positional args
            args, kwargs = c
            assert args[1] is True  # dry_run positional

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_generate_report_called_once_with_results_list(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        from main import run

        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report)
        run(dry_run=False, test_email="test@test.com", limit=None)

        assert mock_report.call_count == 1
        results_arg = mock_report.call_args[0][0]
        assert isinstance(results_arg, list)
        assert len(results_arg) == 2
        assert all(isinstance(r, RunResult) for r in results_arg)

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_run_results_have_agent_log_populated(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        """Each RunResult appended to results must carry the log list from the pipeline."""
        from main import run

        # Simulate run_router mutating log (it appends to the log list in the real impl).
        # Set up base mocks first, then override router.side_effect once.
        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report)

        def fake_router(rr, dry_run, test_email, log=None):
            if log is not None:
                log.append("router:crm_write()")

        mock_router.side_effect = fake_router

        run(dry_run=False, test_email="test@test.com", limit=None)
        results_arg = mock_report.call_args[0][0]
        for rr in results_arg:
            assert "router:crm_write()" in rr.agent_log

    @patch(f"{PATCH_BASE}.configure_logging")
    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_configure_logging_is_invoked(
        self,
        mock_load,
        mock_scout,
        mock_analyst,
        mock_router,
        mock_report,
        mock_sleep,
        mock_configure,
    ):
        from main import run

        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report)
        run(dry_run=False, test_email="test@test.com", limit=None)

        mock_configure.assert_called_once()

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_time_sleep_called_per_account(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        """time.sleep(0.3) is called once per company processed."""
        from main import run

        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report)
        run(dry_run=False, test_email="test@test.com", limit=None)

        assert mock_sleep.call_count == 2  # 2 companies
        for c in mock_sleep.call_args_list:
            assert c[0][0] == 0.3

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_run_router_receives_test_email(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        from main import run

        self._setup_mocks(mock_load, mock_scout, mock_analyst, mock_router, mock_report)
        run(dry_run=False, test_email="custom@example.com", limit=None)

        for c in mock_router.call_args_list:
            args, kwargs = c
            assert args[2] == "custom@example.com"  # test_email positional

    @patch(f"{PATCH_BASE}.time.sleep")
    @patch(f"{PATCH_BASE}.generate_report")
    @patch(f"{PATCH_BASE}.run_router")
    @patch(f"{PATCH_BASE}.run_analyst")
    @patch(f"{PATCH_BASE}.scout_all")
    @patch(f"{PATCH_BASE}.load_companies")
    def test_one_account_failure_does_not_abort_batch_and_report_still_generated(
        self, mock_load, mock_scout, mock_analyst, mock_router, mock_report, mock_sleep
    ):
        """If one account raises inside run_analyst, run() must not raise, must still call
        generate_report once, and the results passed to it must contain only the successful
        accounts (the failing one is skipped via continue)."""
        from main import run

        companies = [
            Company(name="Good Corp", domain="good.com", country="US", description="Fine"),
            Company(name="Bad Corp", domain="bad.com", country="DE", description="Boom"),
            Company(name="Also Good", domain="alsogood.com", country="PL", description="Fine"),
        ]
        mock_load.return_value = companies
        mock_scout.return_value = [_make_signal()]
        mock_report.return_value = "output/run-report.html"

        def analyst_side_effect(c, sigs, log=None):
            if c.name == "Bad Corp":
                raise RuntimeError("Simulated transient API failure")
            return make_score(company_name=c.name)

        mock_analyst.side_effect = analyst_side_effect
        mock_router.return_value = None

        # Must not raise
        run(dry_run=False, test_email="test@test.com", limit=None)

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
