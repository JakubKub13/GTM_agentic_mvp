"""Tests for models.py — Pydantic contract shared across all agents."""
import pytest
from pydantic import ValidationError


class TestCompany:
    def test_construction_with_required_fields(self):
        from models import Company

        c = Company(name="Acme", domain="acme.com")
        assert c.name == "Acme"
        assert c.domain == "acme.com"

    def test_optional_fields_default_to_empty_string(self):
        from models import Company

        c = Company(name="Acme", domain="acme.com")
        assert c.country == ""
        assert c.description == ""

    def test_missing_required_field_raises_validation_error(self):
        from models import Company

        with pytest.raises(ValidationError):
            Company(name="Acme")  # domain is required


class TestSignal:
    def test_construction_with_required_fields(self):
        from models import Signal

        s = Signal(
            signal_type="erp_migration",
            title="New ERP rollout",
            summary="Company migrating to SAP",
            source_url="https://example.com/news",
        )
        assert s.signal_type == "erp_migration"
        assert s.title == "New ERP rollout"

    def test_published_date_defaults_to_none(self):
        from models import Signal

        s = Signal(
            signal_type="hiring",
            title="VP Sales hired",
            summary="...",
            source_url="https://example.com",
        )
        assert s.published_date is None

    def test_relevance_defaults_to_empty_string(self):
        from models import Signal

        s = Signal(
            signal_type="pain",
            title="t",
            summary="s",
            source_url="u",
        )
        assert s.relevance == ""


class TestOutreachDraft:
    def test_construction_with_all_fields(self):
        from models import OutreachDraft

        d = OutreachDraft(
            persona="VP Sales",
            subject="Quick question",
            first_line="Saw your recent announcement",
            body="Full body text here",
        )
        assert d.persona == "VP Sales"
        assert d.subject == "Quick question"

    def test_missing_required_field_raises_validation_error(self):
        from models import OutreachDraft

        with pytest.raises(ValidationError):
            OutreachDraft(persona="VP Sales")  # subject, first_line, body required


class TestICPScore:
    def _make_outreach(self):
        from models import OutreachDraft
        return OutreachDraft(
            persona="VP Sales",
            subject="Subject",
            first_line="Hi",
            body="Body text",
        )

    def test_construction_with_required_fields(self):
        from models import ICPScore

        score = ICPScore(
            company_name="Rohlik",
            domain="rohlik.cz",
            score=8,
            tier="Tier 1",
            confidence="high",
            why_fit=["large ops", "scaling"],
            why_not=["no budget signal"],
            recommended_persona="VP Ops",
            recommended_angle="ERP pain",
            reasoning="Strong signals across hiring and ops.",
            needs_human_research=False,
            outreach=self._make_outreach(),
        )
        assert score.score == 8
        assert score.tier == "Tier 1"

    def test_embeds_outreach_draft(self):
        from models import ICPScore, OutreachDraft

        score = ICPScore(
            company_name="X",
            domain="x.com",
            score=5,
            tier="Tier 2",
            confidence="medium",
            why_fit=[],
            why_not=[],
            recommended_persona="AE",
            recommended_angle="Growth",
            reasoning="OK",
            needs_human_research=True,
            outreach=self._make_outreach(),
        )
        assert isinstance(score.outreach, OutreachDraft)

    def test_missing_required_field_raises_validation_error(self):
        from models import ICPScore

        with pytest.raises(ValidationError):
            # score field missing
            ICPScore(
                company_name="X",
                domain="x.com",
                tier="Tier 1",
                confidence="high",
                why_fit=[],
                why_not=[],
                recommended_persona="AE",
                recommended_angle="Growth",
                reasoning="OK",
                needs_human_research=False,
                outreach=self._make_outreach(),
            )


class TestRunResult:
    def _make_score(self):
        from models import ICPScore, OutreachDraft
        return ICPScore(
            company_name="X",
            domain="x.com",
            score=5,
            tier="Tier 2",
            confidence="medium",
            why_fit=[],
            why_not=[],
            recommended_persona="AE",
            recommended_angle="Growth",
            reasoning="OK",
            needs_human_research=False,
            outreach=OutreachDraft(
                persona="AE",
                subject="Sub",
                first_line="Hi",
                body="Body",
            ),
        )

    def test_construction_with_required_fields(self):
        from models import RunResult

        r = RunResult(score=self._make_score(), signals=[])
        assert r.score is not None
        assert r.signals == []

    def test_default_statuses_are_skipped(self):
        from models import RunResult

        r = RunResult(score=self._make_score(), signals=[])
        assert r.crm_status == "skipped"
        assert r.slack_status == "skipped"
        assert r.outreach_status == "skipped"

    def test_agent_log_defaults_to_empty_list(self):
        from models import RunResult

        r = RunResult(score=self._make_score(), signals=[])
        assert r.agent_log == []
