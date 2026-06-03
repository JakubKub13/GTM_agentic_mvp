"""Tests for reporter.py — HTML generation from RunResult objects."""

import os

from duvo.models import RunResult, Signal
from tests.conftest import make_score

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signal(title: str = "Test Signal", signal_type: str = "hiring") -> Signal:
    return Signal(
        signal_type=signal_type,
        title=title,
        summary="A test signal summary.",
        source_url="https://example.com/article",
        published_date="2024-01-15",
        relevance="high",
    )


def _make_result(
    company_name: str = "Acme Corp",
    domain: str = "acme.com",
    score: int = 8,
    tier: str = "Tier 1",
    crm_status: str = "created",
    slack_status: str = "sent",
    outreach_status: str = "enrolled",
    agent_log: list[str] | None = None,
    signals: list[Signal] | None = None,
) -> RunResult:
    icp = make_score(
        company_name=company_name,
        domain=domain,
        score=score,
        tier=tier,
    )
    return RunResult(
        score=icp,
        signals=signals if signals is not None else [_make_signal()],
        crm_status=crm_status,
        slack_status=slack_status,
        outreach_status=outreach_status,
        agent_log=agent_log
        if agent_log is not None
        else ["scout_search(acme.com)", "analyst_score()"],
    )


# ---------------------------------------------------------------------------
# Basic generation tests
# ---------------------------------------------------------------------------


class TestGenerateReport:
    def test_creates_file_and_returns_path(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        results = [_make_result()]
        out_path = str(tmp_path / "sub" / "report.html")
        returned = generate_report(results, path=out_path)

        assert returned == out_path
        assert os.path.isfile(out_path)

    def test_html_contains_company_names(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        r1 = _make_result(company_name="Acme Corp")
        r2 = _make_result(company_name="Beta Ltd", domain="beta.com", score=5, tier="Tier 2")
        out_path = str(tmp_path / "r.html")
        generate_report([r1, r2], path=out_path)

        html = open(out_path).read()
        assert "Acme Corp" in html
        assert "Beta Ltd" in html

    def test_html_contains_score_badges(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        r = _make_result(score=8)
        out_path = str(tmp_path / "r.html")
        generate_report([r], path=out_path)

        html = open(out_path).read()
        assert "8/10" in html

    def test_tier_css_classes(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        r1 = _make_result(score=9, tier="Tier 1")
        r2 = _make_result(company_name="Beta Ltd", domain="beta.com", score=5, tier="Tier 2")
        r3 = _make_result(company_name="Gamma Inc", domain="gamma.com", score=2, tier="Tier 3")
        out_path = str(tmp_path / "r.html")
        generate_report([r1, r2, r3], path=out_path)

        html = open(out_path).read()
        # Match actual rendered badge elements, not just the <style> block.
        assert 'class="badge t1"' in html
        assert 'class="badge t2"' in html
        assert 'class="badge t3"' in html

    def test_html_contains_signal_titles(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        sig = _make_signal(title="Big ERP Migration Announcement")
        r = _make_result(signals=[sig])
        out_path = str(tmp_path / "r.html")
        generate_report([r], path=out_path)

        html = open(out_path).read()
        assert "Big ERP Migration Announcement" in html

    def test_html_contains_outreach_subject_and_first_line(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        icp = make_score(
            outreach_subject="Custom Subject Line",
            outreach_first_line="Hi, this is the first line.",
        )
        r = RunResult(
            score=icp,
            signals=[_make_signal()],
            crm_status="created",
            slack_status="sent",
            outreach_status="enrolled",
            agent_log=[],
        )
        out_path = str(tmp_path / "r.html")
        generate_report([r], path=out_path)

        html = open(out_path).read()
        assert "Custom Subject Line" in html
        assert "Hi, this is the first line." in html

    def test_html_contains_agent_log_entries(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        r = _make_result(agent_log=["tool_call_alpha()", "tool_call_beta()"])
        out_path = str(tmp_path / "r.html")
        generate_report([r], path=out_path)

        html = open(out_path).read()
        assert "tool_call_alpha()" in html
        assert "tool_call_beta()" in html

    def test_html_contains_crm_slack_outreach_statuses(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        r = _make_result(crm_status="created", slack_status="sent", outreach_status="enrolled")
        out_path = str(tmp_path / "r.html")
        generate_report([r], path=out_path)

        html = open(out_path).read()
        assert "created" in html
        assert "sent" in html
        assert "enrolled" in html

    def test_results_sorted_by_score_descending(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        low = _make_result(company_name="LowCo", domain="low.com", score=3, tier="Tier 3")
        high = _make_result(company_name="HighCo", domain="high.com", score=9, tier="Tier 1")
        mid = _make_result(company_name="MidCo", domain="mid.com", score=6, tier="Tier 2")

        out_path = str(tmp_path / "r.html")
        # Pass in low-first order; output should be high → mid → low
        generate_report([low, mid, high], path=out_path)

        html = open(out_path).read()
        idx_high = html.index("HighCo")
        idx_mid = html.index("MidCo")
        idx_low = html.index("LowCo")
        assert idx_high < idx_mid < idx_low


# ---------------------------------------------------------------------------
# dirname-empty guard
# ---------------------------------------------------------------------------


class TestDirnameGuard:
    def test_no_raise_when_path_has_no_directory(self, tmp_path, monkeypatch):
        """generate_report('r.html') in a flat cwd must not raise via makedirs('')."""
        from duvo.reporting.reporter import generate_report

        monkeypatch.chdir(tmp_path)
        results = [_make_result()]
        # Must not raise; file should be created in cwd
        returned = generate_report(results, path="r.html")
        assert os.path.isfile(tmp_path / "r.html")
        assert returned == "r.html"


# ---------------------------------------------------------------------------
# Autoescape test
# ---------------------------------------------------------------------------


class TestAutoescape:
    def test_special_chars_escaped(self, tmp_path):
        from duvo.reporting.reporter import generate_report

        icp = make_score(company_name="A & B <X>", domain="ab.com")
        r = RunResult(
            score=icp,
            signals=[_make_signal()],
            crm_status="skipped",
            slack_status="skipped",
            outreach_status="skipped",
            agent_log=[],
        )
        out_path = str(tmp_path / "r.html")
        generate_report([r], path=out_path)

        html = open(out_path).read()
        # Raw unescaped string must NOT appear literally
        assert "A & B <X>" not in html
        # Escaped versions must appear
        assert "A &amp; B" in html
        assert "&lt;X&gt;" in html
