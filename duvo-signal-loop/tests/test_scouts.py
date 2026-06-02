"""Tests for scouts.py — fully mocked, no real Anthropic/Exa calls."""
from unittest.mock import patch
import pytest

from models import Company, Signal
from scouts import run_scout, scout_all, BEATS


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
    """Return an async fake run_agent that optionally calls submit_signals with signals_list.

    The fake must be an async function because run_scout/run_analyst do:
        await run_agent(...)
    """
    async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
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

    async def test_returns_signal_objects(self):
        company = _make_company()
        fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "erp_migration", "ERP beat desc")

        assert len(signals) == 1
        assert isinstance(signals[0], Signal)

    async def test_signal_type_set_to_beat_key(self):
        company = _make_company()
        for beat_key in ["erp_migration", "hiring", "ma_leadership", "pain"]:
            fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
            with patch("scouts.run_agent", side_effect=fake):
                signals = await run_scout(company, beat_key, "some beat desc")
            assert len(signals) == 1
            assert signals[0].signal_type == beat_key

    async def test_title_from_dict(self):
        company = _make_company()
        fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "erp_migration", "ERP desc")
        assert signals[0].title == "Acme ERP Migration"

    async def test_missing_title_defaults_to_no_title(self):
        sig_without_title = {k: v for k, v in SAMPLE_SIGNAL_DICT.items() if k != "title"}
        company = _make_company()
        fake = _make_fake_run_agent([sig_without_title])
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "hiring", "hiring desc")
        assert signals[0].title == "(no title)"

    async def test_missing_summary_defaults_to_empty_string(self):
        sig = {k: v for k, v in SAMPLE_SIGNAL_DICT.items() if k != "summary"}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "pain", "pain desc")
        assert signals[0].summary == ""

    async def test_missing_relevance_defaults_to_empty_string(self):
        sig = {k: v for k, v in SAMPLE_SIGNAL_DICT.items() if k != "relevance"}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "ma_leadership", "M&A desc")
        assert signals[0].relevance == ""

    async def test_empty_published_date_becomes_none(self):
        sig = {**SAMPLE_SIGNAL_DICT, "published_date": ""}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "pain", "pain desc")
        assert signals[0].published_date is None

    async def test_none_published_date_stays_none(self):
        sig = {**SAMPLE_SIGNAL_DICT, "published_date": None}
        company = _make_company()
        fake = _make_fake_run_agent([sig])
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "erp_migration", "ERP desc")
        assert signals[0].published_date is None

    async def test_submit_signals_never_called_returns_empty_list(self):
        """If the agent never calls submit_signals, run_scout returns []."""
        fake = _make_fake_run_agent([], call_submit=False)
        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "erp_migration", "ERP desc")
        assert signals == []

    async def test_submit_signals_called_with_no_args_returns_empty_list(self):
        """The model may call submit_signals() with no args to mean 'found nothing'.

        This must NOT raise (it previously crashed with a missing-arg TypeError and
        lost the beat). run_scout should treat it as zero signals.
        """
        async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            # Call with no signals argument at all — must be tolerated.
            result = impls["submit_signals"]()
            assert "0 signals" in result
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            signals = await run_scout(company, "hiring", "hiring desc")
        assert signals == []

    async def test_submit_signals_called_with_none_returns_empty_list(self):
        """submit_signals(signals=None) is tolerated as zero signals."""
        async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            impls["submit_signals"](signals=None)
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            signals = await run_scout(company, "pain", "pain desc")
        assert signals == []

    async def test_multiple_signals_returned(self):
        sigs = [SAMPLE_SIGNAL_DICT, {**SAMPLE_SIGNAL_DICT, "title": "Another Signal"}]
        company = _make_company()
        fake = _make_fake_run_agent(sigs)
        with patch("scouts.run_agent", side_effect=fake):
            signals = await run_scout(company, "hiring", "hiring desc")
        assert len(signals) == 2

    async def test_log_receives_entry_when_provided(self):
        company = _make_company()
        log = []
        fake = _make_fake_run_agent([SAMPLE_SIGNAL_DICT])
        with patch("scouts.run_agent", side_effect=fake):
            await run_scout(company, "erp_migration", "ERP desc", log=log)
        # The fake appends an entry to log when called
        assert len(log) >= 1


# ---------------------------------------------------------------------------
# TestScoutAll
# ---------------------------------------------------------------------------

class TestScoutAll:
    """scout_all: runs all 4 beats concurrently via asyncio.gather, flattens results."""

    async def test_runs_all_4_beats_and_flattens(self):
        """Each beat submits 1 signal; scout_all returns 4 signals total."""
        async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            impls["submit_signals"](signals=[SAMPLE_SIGNAL_DICT])
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            signals = await scout_all(company)

        assert len(signals) == 4

    async def test_all_4_signal_types_covered(self):
        """The 4 beats produce signals with all 4 signal_type values."""
        async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            impls["submit_signals"](signals=[SAMPLE_SIGNAL_DICT])
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            signals = await scout_all(company)

        types_found = {s.signal_type for s in signals}
        assert types_found == {"erp_migration", "hiring", "ma_leadership", "pain"}

    async def test_empty_beats_return_empty_list(self):
        """If no beat finds signals, scout_all returns []."""
        async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            impls["submit_signals"](signals=[])
            return []

        company = _make_company()
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            signals = await scout_all(company)

        assert signals == []

    async def test_log_shared_across_beats(self):
        """A shared log list accumulates entries from all 4 beats without corruption."""
        async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(), log=None):
            if log is not None:
                log.append("scout_entry")
            impls["submit_signals"](signals=[])
            return []

        company = _make_company()
        log = []
        with patch("scouts.run_agent", side_effect=fake_run_agent):
            await scout_all(company, log=log)

        # 4 beats each appended 1 entry — single event loop: no race condition
        assert len(log) == 4

    async def test_one_beat_exception_does_not_crash_scout_all(self):
        """If one beat's run_scout raises, scout_all still returns the other beats' signals."""
        call_count = {"n": 0}

        async def fake_run_scout(company, beat_key, beat_desc, log=None):
            call_count["n"] += 1
            if beat_key == "erp_migration":
                raise RuntimeError("simulated scout failure")
            # Other beats return one signal each
            return [Signal(
                signal_type=beat_key,
                title="Test Signal",
                summary="summary",
                source_url="https://example.com",
                relevance="relevant",
            )]

        company = _make_company()
        with patch("scouts.run_scout", side_effect=fake_run_scout):
            signals = await scout_all(company)

        # 3 beats succeeded (hiring, ma_leadership, pain) → 3 signals
        assert len(signals) == 3
        signal_types = {s.signal_type for s in signals}
        assert "erp_migration" not in signal_types
        assert signal_types == {"hiring", "ma_leadership", "pain"}
