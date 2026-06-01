"""Tests for scouts.py — fully mocked, no real Anthropic/Exa calls."""
from unittest.mock import patch
import pytest

from models import Company, Signal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_SIGNAL_DICT = {
    "title": "Acme ERP Migration",
    "summary": "Acme is migrating to SAP S/4HANA.",
    "source_url": "https://example.com/acme-erp",
    "published_date": "2024-01-15",
    "relevance": "Major ERP change is a direct trigger for Duvo.",
}


def _make_fake_run_agent(signals_list, *, call_submit=True):
    """Return a fake run_agent that optionally calls submit_signals with signals_list."""
    def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
        if call_submit:
            impls["submit_signals"](signals=signals_list)
            if log is not None:
                log.append("submit_signals(...)")
        return []
    return fake_run_agent


def _make_company(**kw):
    defaults = {"name": "Acme Corp", "domain": "acme.com", "country": "DE", "description": "A retail company."}
    defaults.update(kw)
    return Company(**defaults)


# ---------------------------------------------------------------------------
# TestRunScout
# ---------------------------------------------------------------------------

class TestRunScout:
    """run_scout: converts captured dicts to Signal objects correctly."""

    def test_returns_signal_objects(self):
        company = _make_company()
        fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "erp_migration", "ERP beat desc")

        assert len(signals) == 1
        assert isinstance(signals[0], Signal)

    def test_signal_type_set_to_beat_key(self):
        company = _make_company()
        for beat_key in ["erp_migration", "hiring", "ma_leadership", "pain"]:
            fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
            with patch("scouts.run_agent", side_effect=fake):
                from scouts import run_scout
                signals = run_scout(company, beat_key, "some beat desc")
            assert len(signals) == 1
            assert signals[0].signal_type == beat_key

    def test_title_from_dict(self):
        company = _make_company()
        fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "erp_migration", "ERP desc")
        assert signals[0].title == "Acme ERP Migration"

    def test_missing_title_defaults_to_no_title(self):
        sig_without_title = {k: v for k, v in SAMPLE_SIGNAL_DICT.items() if k != "title"}
        company = _make_company()
        fake = _make_fake_run_agent([sig_without_title])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "hiring", "hiring desc")
        assert signals[0].title == "(no title)"

    def test_missing_summary_defaults_to_empty_string(self):
        sig = {k: v for k, v in SAMPLE_SIGNAL_DICT.items() if k != "summary"}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "pain", "pain desc")
        assert signals[0].summary == ""

    def test_missing_relevance_defaults_to_empty_string(self):
        sig = {k: v for k, v in SAMPLE_SIGNAL_DICT.items() if k != "relevance"}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "ma_leadership", "M&A desc")
        assert signals[0].relevance == ""

    def test_empty_published_date_becomes_none(self):
        sig = {**SAMPLE_SIGNAL_DICT, "published_date": ""}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "pain", "pain desc")
        assert signals[0].published_date is None

    def test_none_published_date_stays_none(self):
        sig = {**SAMPLE_SIGNAL_DICT, "published_date": None}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "erp_migration", "ERP desc")
        assert signals[0].published_date is None

    def test_submit_signals_never_called_returns_empty_list(self):
        """If the agent never calls submit_signals, run_scout returns []."""
        fake = _make_fake_run_agent([], call_submit=False)
        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "erp_migration", "ERP desc")
        assert signals == []

    def test_multiple_signals_returned(self):
        sigs = [SAMPLE_SIGNAL_DICT, {**SAMPLE_SIGNAL_DICT, "title": "Another Signal"}]
        company = _make_company()
        fake = _make_fake_run_agent(sigs)
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            signals = run_scout(company, "hiring", "hiring desc")
        assert len(signals) == 2

    def test_log_receives_entry_when_provided(self):
        company = _make_company()
        log = []
        fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
        with patch("scouts.run_agent", side_effect=fake):
            from scouts import run_scout
            run_scout(company, "erp_migration", "ERP desc", log=log)
        # The fake appends an entry to log when called
        assert len(log) >= 1


# ---------------------------------------------------------------------------
# TestScoutAll
# ---------------------------------------------------------------------------

class TestScoutAll:
    """scout_all: runs all 4 beats in parallel, flattens results."""

    def test_runs_all_4_beats_and_flattens(self):
        """Each beat submits 1 signal; scout_all returns 4 signals total."""
        def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            impls["submit_signals"](signals=[SAMPLE_SIGNAL_DICT])
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            from scouts import scout_all
            signals = scout_all(company)

        assert len(signals) == 4

    def test_all_4_signal_types_covered(self):
        """The 4 beats produce signals with all 4 signal_type values."""
        def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            impls["submit_signals"](signals=[SAMPLE_SIGNAL_DICT])
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            from scouts import scout_all
            signals = scout_all(company)

        types_found = {s.signal_type for s in signals}
        assert types_found == {"erp_migration", "hiring", "ma_leadership", "pain"}

    def test_empty_beats_return_empty_list(self):
        """If no beat finds signals, scout_all returns []."""
        def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            impls["submit_signals"](signals=[])
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            from scouts import scout_all
            signals = scout_all(company)

        assert signals == []

    def test_log_shared_across_beats(self):
        """A shared log list accumulates entries from all 4 beats without corruption."""
        def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            if log is not None:
                log.append("scout_entry")
            impls["submit_signals"](signals=[])
            return []

        company = _make_company()
        log = []
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            from scouts import scout_all
            scout_all(company, log=log)

        # 4 beats each appended 1 entry
        assert len(log) == 4
