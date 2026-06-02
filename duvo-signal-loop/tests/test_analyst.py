"""Tests for analyst.py — fully mocked, no real Anthropic/Exa calls."""
from unittest.mock import patch
import pytest

from models import Company, Signal, ICPScore, OutreachDraft
from analyst import run_analyst, apply_guards


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_company(**kw):
    defaults = {"name": "Acme Corp", "domain": "acme.com", "country": "DE", "description": "A retail chain."}
    defaults.update(kw)
    return Company(**defaults)


def _make_signal(*, published_date="2024-01-15", signal_type="erp_migration"):
    return Signal(
        signal_type=signal_type,
        title="ERP Migration Signal",
        summary="Acme is migrating to SAP S/4HANA.",
        source_url="https://example.com/acme-erp",
        published_date=published_date,
        relevance="Direct trigger.",
    )


VALID_ASSESSMENT = {
    "score": 8,
    "tier": "Tier 1",
    "confidence": "high",
    "why_fit": ["Retail company", "Has SAP ERP"],
    "why_not": [],
    "recommended_persona": "CFO",
    "recommended_angle": "ERP migration automation",
    "reasoning": "Strong fit based on signals.",
    "needs_human_research": False,
    "outreach": {
        "persona": "CFO",
        "subject": "Automating your SAP go-live",
        "first_line": "Saw you're migrating to SAP S/4HANA.",
        "body": "Body text here.",
    },
}


def _make_fake_run_agent(assessment_kwargs=None):
    """Return an async fake run_agent that calls record_assessment with assessment_kwargs.

    If assessment_kwargs is None, the agent never calls record_assessment
    (simulates the conservative-default path).

    The fake must be an async function because run_analyst does:
        await run_agent(...)
    """
    async def fake_run_agent(system, user, tools, impls, max_turns=8, final_tools=(),
                             log=None, max_tokens=2000):
        if assessment_kwargs is not None:
            impls["record_assessment"](**assessment_kwargs)
        return []
    return fake_run_agent


# ---------------------------------------------------------------------------
# TestRunAnalyst — normal path
# ---------------------------------------------------------------------------

class TestRunAnalystNormalPath:
    """run_analyst: builds ICPScore from record_assessment output after guards."""

    async def test_returns_icscore_instance(self):
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(VALID_ASSESSMENT)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert isinstance(result, ICPScore)

    async def test_company_name_and_domain_populated(self):
        company = _make_company(name="TestCo", domain="testco.com")
        signals = [_make_signal()]
        fake = _make_fake_run_agent(VALID_ASSESSMENT)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert result.company_name == "TestCo"
        assert result.domain == "testco.com"

    async def test_outreach_draft_populated(self):
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(VALID_ASSESSMENT)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert isinstance(result.outreach, OutreachDraft)
        assert result.outreach.subject == "Automating your SAP go-live"

    async def test_guards_applied_score_8_dated_signal_gives_tier1(self):
        """score=8, confidence=high, not needs_human_research, dated signal → Tier 1."""
        company = _make_company()
        signals = [_make_signal(published_date="2024-01-15")]
        fake = _make_fake_run_agent(VALID_ASSESSMENT)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        # After guards: score=8 >=8, not needs_human_research → Tier 1
        assert result.tier == "Tier 1"
        assert result.score == 8


# ---------------------------------------------------------------------------
# TestRunAnalystMaxTokens — the analyst asks for a larger output budget so the
# big record_assessment payload (assessment + outreach draft) isn't truncated.
# ---------------------------------------------------------------------------

class TestRunAnalystMaxTokens:
    """run_analyst passes a max_tokens above the run_agent default (2000)."""

    async def test_passes_max_tokens_above_default(self):
        company = _make_company()
        signals = [_make_signal()]
        captured = {}

        async def capturing_run_agent(system, user, tools, impls, max_turns=8,
                                      final_tools=(), log=None, max_tokens=2000):
            captured["max_tokens"] = max_tokens
            impls["record_assessment"](**VALID_ASSESSMENT)
            return []

        with patch("analyst.run_agent", side_effect=capturing_run_agent):
            await run_analyst(company, signals)

        assert captured["max_tokens"] > 2000


# ---------------------------------------------------------------------------
# TestRunAnalystConservativeDefault
# ---------------------------------------------------------------------------

class TestRunAnalystConservativeDefault:
    """When the agent never calls record_assessment, a conservative default is returned."""

    async def test_returns_score_3_tier3_low_confidence(self):
        company = _make_company()
        signals = []
        fake = _make_fake_run_agent(assessment_kwargs=None)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert result.score == 3
        assert result.tier == "Tier 3"
        assert result.confidence == "low"
        assert result.needs_human_research is True

    async def test_conservative_default_returns_icpscore(self):
        company = _make_company()
        fake = _make_fake_run_agent(assessment_kwargs=None)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, [])
        assert isinstance(result, ICPScore)


# ---------------------------------------------------------------------------
# TestScoreClamping
# ---------------------------------------------------------------------------

class TestScoreClamping:
    """Scores outside 1-10 are clamped before ICPScore construction."""

    async def test_score_15_clamped_to_10(self):
        assessment = {**VALID_ASSESSMENT, "score": 15}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        # Clamped to 10 — no ValidationError
        assert result.score == 10

    async def test_score_0_clamped_to_1(self):
        assessment = {**VALID_ASSESSMENT, "score": 0, "tier": "Tier 3", "confidence": "low",
                      "needs_human_research": True}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        # Clamped to 1, score<5 → Tier 3
        assert result.score == 1

    async def test_score_minus_5_clamped_to_1(self):
        assessment = {**VALID_ASSESSMENT, "score": -5, "tier": "Tier 3", "confidence": "low",
                      "needs_human_research": True}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert result.score == 1

    async def test_score_100_clamped_to_10(self):
        assessment = {**VALID_ASSESSMENT, "score": 100}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert result.score == 10

    async def test_non_numeric_score_string_returns_conservative_score(self):
        """If the model returns a non-numeric score like 'high', run_analyst must not crash
        and must return a valid ICPScore with a conservative score in [1, 10]."""
        assessment = {**VALID_ASSESSMENT, "score": "high"}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert isinstance(result, ICPScore)
        assert 1 <= result.score <= 10

    async def test_none_score_returns_conservative_score(self):
        """If the model returns None as score, run_analyst must not crash and must return
        a valid ICPScore with a conservative score in [1, 10]."""
        assessment = {**VALID_ASSESSMENT, "score": None}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert isinstance(result, ICPScore)
        assert 1 <= result.score <= 10


# ---------------------------------------------------------------------------
# TestTierConfidenceCoercion
# ---------------------------------------------------------------------------

class TestTierConfidenceCoercion:
    """Invalid tier/confidence values are coerced to safe defaults."""

    async def test_invalid_tier_coerced_to_tier3(self):
        # score=4 (< 5) → guards assign Tier 3; invalid tier "Tier 99" is coerced to "Tier 3"
        # before ICPScore construction (without coercion, Pydantic raises ValidationError).
        # No dated signals → guards also force confidence=low, needs_human_research=True.
        assessment = {
            **VALID_ASSESSMENT,
            "tier": "Tier 99",          # invalid — must be coerced
            "score": 4,                  # ensures guards map to Tier 3 (consistent with coercion)
            "confidence": "low",
            "needs_human_research": True,
        }
        company = _make_company()
        signals = []                     # no signals → guards keep Tier 3
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            # Without coercion this raises ValidationError; coercion must have run.
            result = await run_analyst(company, signals)
        assert result.tier == "Tier 3"

    async def test_invalid_confidence_coerced_to_low(self):
        # invalid confidence "very_high" is coerced to "low" before ICPScore construction
        # (without coercion, Pydantic raises ValidationError).
        # No dated signals → guards also force confidence=low, so the assertion is stable.
        assessment = {
            **VALID_ASSESSMENT,
            "confidence": "very_high",   # invalid — must be coerced
        }
        company = _make_company()
        signals = []                     # no dated signals → guards enforce low anyway
        fake = _make_fake_run_agent(assessment)
        with patch("analyst.run_agent", side_effect=fake):
            # Without coercion this raises ValidationError; coercion must have run.
            result = await run_analyst(company, signals)
        assert result.confidence == "low"


# ---------------------------------------------------------------------------
# TestPartialAssessmentFallback
# ---------------------------------------------------------------------------

class TestPartialAssessmentFallback:
    """run_analyst: a partial record_assessment (missing required field) returns the
    conservative default instead of crashing with a ValidationError."""

    async def test_missing_reasoning_returns_conservative_default(self):
        """If the LLM records an assessment but omits 'reasoning' (a required ICPScore field),
        ICPScore construction raises ValidationError; run_analyst must catch it and return
        the conservative default (score=3, Tier 3, low, needs_human_research=True)."""
        partial_assessment = {k: v for k, v in VALID_ASSESSMENT.items() if k != "reasoning"}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(partial_assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert isinstance(result, ICPScore)
        assert result.score == 3
        assert result.tier == "Tier 3"
        assert result.confidence == "low"
        assert result.needs_human_research is True


# ---------------------------------------------------------------------------
# TestOutreachDraftFallback
# ---------------------------------------------------------------------------

class TestOutreachDraftFallback:
    """run_analyst: malformed outreach dict falls back to empty OutreachDraft."""

    async def test_missing_body_field_returns_valid_icpscore(self):
        """If record_assessment outreach is missing 'body', run_analyst must not raise."""
        bad_outreach_assessment = {
            **VALID_ASSESSMENT,
            "outreach": {
                "persona": "CFO",
                "subject": "Test",
                "first_line": "Hi there.",
                # "body" intentionally omitted — should trigger fallback
            },
        }
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(bad_outreach_assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert isinstance(result, ICPScore)
        assert isinstance(result.outreach, OutreachDraft)
        # Fallback: all fields should be empty strings
        assert result.outreach.persona == ""
        assert result.outreach.subject == ""
        assert result.outreach.first_line == ""
        assert result.outreach.body == ""

    async def test_completely_missing_outreach_key_returns_valid_icpscore(self):
        """If record_assessment provides no outreach key at all, run_analyst must not raise."""
        no_outreach_assessment = {k: v for k, v in VALID_ASSESSMENT.items() if k != "outreach"}
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(no_outreach_assessment)
        with patch("analyst.run_agent", side_effect=fake):
            result = await run_analyst(company, signals)
        assert isinstance(result, ICPScore)
        assert result.outreach.body == ""


# ---------------------------------------------------------------------------
# TestApplyGuards — unit tests (no agent involved; apply_guards is sync)
# ---------------------------------------------------------------------------

class TestApplyGuards:
    """apply_guards: deterministic post-guard logic tested directly (sync — no I/O)."""

    def _make_score(self, *, score=8, tier="Tier 1", confidence="high",
                    needs_human_research=False) -> ICPScore:
        return ICPScore(
            company_name="TestCo",
            domain="testco.com",
            score=score,
            tier=tier,
            confidence=confidence,
            why_fit=["Good fit"],
            why_not=[],
            recommended_persona="CFO",
            recommended_angle="ERP migration",
            reasoning="Solid.",
            needs_human_research=needs_human_research,
            outreach=OutreachDraft(
                persona="CFO",
                subject="Test",
                first_line="Hi there.",
                body="Body.",
            ),
        )

    def _dated_signal(self):
        return _make_signal(published_date="2024-01-15")

    def _undated_signal(self):
        return _make_signal(published_date=None)

    # -- No dated signals

    def test_no_dated_signals_forces_confidence_low(self):
        score = self._make_score(confidence="high", needs_human_research=False)
        result = apply_guards(score, [self._undated_signal()])
        assert result.confidence == "low"

    def test_no_dated_signals_forces_needs_human_research_true(self):
        score = self._make_score(needs_human_research=False)
        result = apply_guards(score, [self._undated_signal()])
        assert result.needs_human_research is True

    def test_no_signals_at_all_forces_confidence_low(self):
        score = self._make_score(confidence="high")
        result = apply_guards(score, [])
        assert result.confidence == "low"
        assert result.needs_human_research is True

    # -- Low confidence + high score is capped

    def test_low_confidence_score_7_capped_to_6(self):
        score = self._make_score(score=7, confidence="low", tier="Tier 2", needs_human_research=True)
        result = apply_guards(score, [self._dated_signal()])
        assert result.score == 6

    def test_low_confidence_score_9_capped_to_6(self):
        score = self._make_score(score=9, confidence="low", tier="Tier 1", needs_human_research=True)
        result = apply_guards(score, [self._dated_signal()])
        assert result.score == 6

    def test_low_confidence_score_6_not_capped(self):
        score = self._make_score(score=6, confidence="low", tier="Tier 2", needs_human_research=True)
        result = apply_guards(score, [self._dated_signal()])
        assert result.score == 6  # unchanged

    def test_high_confidence_score_9_not_capped(self):
        score = self._make_score(score=9, confidence="high", tier="Tier 1", needs_human_research=False)
        result = apply_guards(score, [self._dated_signal()])
        assert result.score == 9

    # -- Tier assignment

    def test_score_8_not_needs_human_research_gives_tier1(self):
        score = self._make_score(score=8, confidence="high", needs_human_research=False, tier="Tier 3")
        result = apply_guards(score, [self._dated_signal()])
        assert result.tier == "Tier 1"

    def test_score_9_not_needs_human_research_gives_tier1(self):
        score = self._make_score(score=9, confidence="high", needs_human_research=False, tier="Tier 3")
        result = apply_guards(score, [self._dated_signal()])
        assert result.tier == "Tier 1"

    def test_score_5_gives_tier2(self):
        score = self._make_score(score=5, confidence="high", needs_human_research=False, tier="Tier 1")
        result = apply_guards(score, [self._dated_signal()])
        assert result.tier == "Tier 2"

    def test_score_7_not_needs_human_research_gives_tier2(self):
        score = self._make_score(score=7, confidence="high", needs_human_research=False, tier="Tier 1")
        result = apply_guards(score, [self._dated_signal()])
        assert result.tier == "Tier 2"

    def test_score_4_gives_tier3(self):
        score = self._make_score(score=4, confidence="high", needs_human_research=False, tier="Tier 1")
        result = apply_guards(score, [self._dated_signal()])
        assert result.tier == "Tier 3"

    def test_score_1_gives_tier3(self):
        score = self._make_score(score=1, confidence="high", needs_human_research=False, tier="Tier 1")
        result = apply_guards(score, [self._dated_signal()])
        assert result.tier == "Tier 3"

    # -- needs_human_research blocks Tier 1

    def test_needs_human_research_true_with_score_9_gives_tier2(self):
        """needs_human_research=True prevents Tier 1 assignment even with high score."""
        score = self._make_score(score=9, confidence="high", needs_human_research=True, tier="Tier 1")
        result = apply_guards(score, [self._dated_signal()])
        # score>=8 but needs_human_research=True → elif score>=5 → Tier 2
        assert result.tier == "Tier 2"

    def test_needs_human_research_with_no_dated_gives_tier2_not_tier1(self):
        """No dated signals → confidence=low, needs_human_research=True, score capped → Tier 2."""
        score = self._make_score(score=9, confidence="high", needs_human_research=False, tier="Tier 1")
        result = apply_guards(score, [self._undated_signal()])
        # No dated → confidence=low, needs_human_research=True, score(9) capped to 6 → Tier 2
        assert result.tier == "Tier 2"
        assert result.score == 6
