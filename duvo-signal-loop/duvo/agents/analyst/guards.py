"""Deterministic analyst guardrails and defensive coercions."""

import json
from typing import Any

from duvo.infra.logging_setup import get_logger
from duvo.models import Company, ICPScore, OutreachDraft, Signal

_log = get_logger(__name__)

_VALID_TIERS = {"Tier 1", "Tier 2", "Tier 3"}
_VALID_CONFIDENCES = {"high", "medium", "low"}


def conservative_default(company: Company) -> ICPScore:
    """Return a conservative ICPScore when the analyst produces nothing usable."""
    return ICPScore(
        company_name=company.name,
        domain=company.domain,
        score=3,
        tier="Tier 3",
        confidence="low",
        why_fit=[],
        why_not=["No usable signals / analyst produced nothing."],
        recommended_persona="Supply Chain / Finance leadership",
        recommended_angle="",
        reasoning="Insufficient evidence.",
        needs_human_research=True,
        outreach=OutreachDraft(
            persona="Supply Chain leadership", subject="", first_line="", body=""
        ),
    )


def score_from_assessment_payload(company: Company, captured: dict[str, Any]) -> ICPScore:
    """Build an ICPScore from the analyst terminal payload with defensive coercions."""
    raw_score = captured.get("score", 3)
    try:
        int_score = int(raw_score)
    except (ValueError, TypeError):
        _log.warning(
            "analyst: non-numeric score %r — falling back to conservative score 3 for company=%r",
            raw_score,
            company.name,
        )
        int_score = 3
    clamped_score = max(1, min(10, int_score))
    if clamped_score != int_score:
        _log.warning(
            "analyst: score out of range (got %r) — clamped to %d for company=%r",
            raw_score,
            clamped_score,
            company.name,
        )
    captured["score"] = clamped_score

    raw_tier = captured.get("tier", "Tier 3")
    if raw_tier not in _VALID_TIERS:
        _log.warning(
            "analyst: invalid tier %r — falling back to 'Tier 3' for company=%r",
            raw_tier,
            company.name,
        )
        captured["tier"] = "Tier 3"

    raw_confidence = captured.get("confidence", "low")
    if raw_confidence not in _VALID_CONFIDENCES:
        _log.warning(
            "analyst: invalid confidence %r — falling back to 'low' for company=%r",
            raw_confidence,
            company.name,
        )
        captured["confidence"] = "low"

    outreach = _coerce_outreach(company, captured.pop("outreach", {}))
    try:
        return ICPScore(
            company_name=company.name, domain=company.domain, outreach=outreach, **captured
        )
    except Exception as exc:
        _log.warning(
            "analyst: ICPScore construction failed for company=%r (%s) — using conservative default",
            company.name,
            exc,
        )
        return conservative_default(company)


def _coerce_outreach(company: Company, outreach_raw: Any) -> OutreachDraft:
    """Coerce the nested outreach payload into an OutreachDraft."""
    if isinstance(outreach_raw, str):
        try:
            outreach_raw = json.loads(outreach_raw)
        except (json.JSONDecodeError, TypeError):
            outreach_raw = {}
    try:
        return OutreachDraft(**outreach_raw)
    except Exception as exc:
        _log.warning(
            "analyst: malformed outreach for company=%r (%s) — using empty fallback",
            company.name,
            exc,
        )
        return OutreachDraft(persona="", subject="", first_line="", body="")


def apply_guards(score: ICPScore, signals: list[Signal]) -> ICPScore:
    """Deterministic post-guard: thin/undated evidence cannot yield a confident high score.

    Rules applied in order:
    1. No dated signals → confidence=low, needs_human_research=True.
    2. confidence==low AND score>=7 → score capped to 6.
    3. Tier derived from score + needs_human_research (overrides agent's tier).
    4. needs_human_research + Tier 1 → downgrade to Tier 2.

    This function is synchronous — it is a pure transformation with no I/O.
    """
    dated = [s for s in signals if s.published_date]
    if not dated:
        _log.debug("guard: no dated signals → confidence=low, needs_human_research=True")
        score.confidence = "low"
        score.needs_human_research = True
    if score.confidence == "low" and score.score >= 7:
        _log.debug("guard: confidence=low with score=%d >= 7 → capping score to 6", score.score)
        score.score = 6
    if score.score >= 8 and not score.needs_human_research:
        _log.debug("guard: score=%d >= 8 and not needs_human_research → Tier 1", score.score)
        score.tier = "Tier 1"
    elif score.score >= 5:
        _log.debug("guard: score=%d >= 5 → Tier 2", score.score)
        score.tier = "Tier 2"
    else:
        _log.debug("guard: score=%d < 5 → Tier 3", score.score)
        score.tier = "Tier 3"
    if score.needs_human_research and score.tier == "Tier 1":
        _log.debug("guard: needs_human_research=True with Tier 1 → downgrading to Tier 2")
        score.tier = "Tier 2"
    return score
