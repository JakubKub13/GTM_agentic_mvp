"""Analyst agent: validate signals (with its own verification searches), score, draft outreach."""

import json

from duvo.agent_core import run_agent
from duvo.agents.agent_prompts import load_prompt
from duvo.config import MAX_ANALYST_SEARCHES
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import tool_schema
from duvo.models import Company, ICPScore, OutreachDraft, Signal
from duvo.tools.exa_tool import EXA_SEARCH_TOOL, exa_search

_log = get_logger(__name__)

# The analyst's record_assessment payload is large (why_fit/why_not arrays, reasoning,
# plus the full nested outreach draft). The default 2000-token cap can truncate the
# outreach object mid-JSON, yielding an empty/malformed draft that falls back to blank.
# Give the analyst headroom so the draft completes.
ANALYST_MAX_TOKENS = 4096

RECORD_TOOL = tool_schema(
    "record_assessment",
    "Record the final ICP assessment and drafted outreach. Call once when done.",
    {
        "type": "object",
        "properties": {
            "score": {"type": "integer"},
            "tier": {"type": "string", "enum": ["Tier 1", "Tier 2", "Tier 3"]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "why_fit": {"type": "array", "items": {"type": "string"}},
            "why_not": {"type": "array", "items": {"type": "string"}},
            "recommended_persona": {"type": "string"},
            "recommended_angle": {"type": "string"},
            "reasoning": {"type": "string"},
            "needs_human_research": {"type": "boolean"},
            "outreach": {
                "type": "object",
                "properties": {
                    "persona": {"type": "string"},
                    "subject": {"type": "string"},
                    "first_line": {
                        "type": "string",
                        "description": "Personalized opener grounded in a real signal.",
                    },
                    "body": {"type": "string"},
                },
                "required": ["persona", "subject", "first_line", "body"],
            },
        },
        "required": [
            "score",
            "tier",
            "confidence",
            "why_fit",
            "why_not",
            "recommended_persona",
            "recommended_angle",
            "reasoning",
            "needs_human_research",
            "outreach",
        ],
    },
)

_VALID_TIERS = {"Tier 1", "Tier 2", "Tier 3"}
_VALID_CONFIDENCES = {"high", "medium", "low"}


def _conservative_default(company: Company) -> ICPScore:
    """Return a conservative-default ICPScore for a company when the agent produces nothing usable."""
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


async def run_analyst(company: Company, signals: list[Signal], log=None) -> ICPScore:
    """Run the analyst agent to assess ICP fit and draft outreach.

    The agent can optionally run verification searches (up to MAX_ANALYST_SEARCHES)
    before calling record_assessment. If it never calls record_assessment, a
    conservative default ICPScore is returned.

    Score clamping: the LLM may return a score outside 1-10, which would cause a
    ValidationError. We clamp to [1, 10] before constructing ICPScore. We also
    coerce tier/confidence to valid Literal values as a defensive measure.

    Args:
        company: The company to assess.
        signals: Signals gathered by the scouts.
        log:     Optional audit log list passed through to run_agent.

    Returns:
        An ICPScore with deterministic guards applied.
    """
    _log.info("analyst starting: company=%r signals=%d", company.name, len(signals))

    signals_json = json.dumps([s.model_dump() for s in signals], ensure_ascii=False, indent=2)
    system = load_prompt("analyst", max_analyst_searches=MAX_ANALYST_SEARCHES)
    user = (
        f"Company: {company.name} ({company.domain}, {company.country}). "
        f"Context: {company.description}\n\nSignals:\n{signals_json}"
    )

    captured: dict = {}

    def record_assessment(**kw):
        captured.update(kw)
        return "recorded"

    # exa_search is async — run_agent awaits awaitable impls automatically.
    # record_assessment is sync — run_agent calls it directly.
    impls = {"exa_search": exa_search, "record_assessment": record_assessment}
    await run_agent(
        system,
        user,
        [EXA_SEARCH_TOOL, RECORD_TOOL],
        impls,
        max_turns=MAX_ANALYST_SEARCHES + 3,
        final_tools={"record_assessment"},
        log=log,
        max_tokens=ANALYST_MAX_TOKENS,
    )

    if not captured:  # agent never produced an assessment — conservative default
        _log.warning(
            "analyst: no record_assessment call from agent for company=%r — using conservative default",
            company.name,
        )
        return _conservative_default(company)

    # --- Defensive coercions before model construction ---

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

    outreach_raw = captured.pop("outreach", {})
    # The model sometimes returns the nested outreach object as a JSON *string*
    # rather than a mapping; parse it before construction (defensive coercion).
    if isinstance(outreach_raw, str):
        try:
            outreach_raw = json.loads(outreach_raw)
        except (json.JSONDecodeError, TypeError):
            outreach_raw = {}
    try:
        outreach = OutreachDraft(**outreach_raw)
    except Exception as exc:
        _log.warning(
            "analyst: malformed outreach for company=%r (%s) — using empty fallback",
            company.name,
            exc,
        )
        outreach = OutreachDraft(persona="", subject="", first_line="", body="")
    try:
        score = ICPScore(
            company_name=company.name, domain=company.domain, outreach=outreach, **captured
        )
    except Exception as exc:
        _log.warning(
            "analyst: ICPScore construction failed for company=%r (%s) — using conservative default",
            company.name,
            exc,
        )
        return _conservative_default(company)
    result = apply_guards(score, signals)
    _log.info(
        "analyst done: company=%r score=%d tier=%s confidence=%s needs_human_research=%s",
        company.name,
        result.score,
        result.tier,
        result.confidence,
        result.needs_human_research,
    )
    return result


def apply_guards(score: ICPScore, signals: list[Signal]) -> ICPScore:
    """Deterministic post-guard: thin/undated evidence cannot yield a confident high score.

    Rules applied in order:
    1. No dated signals → confidence=low, needs_human_research=True.
    2. confidence==low AND score>=7 → score capped to 6.
    3. Tier derived from score + needs_human_research (overrides agent's tier).
    4. needs_human_research + Tier 1 → downgrade to Tier 2.

    This function is synchronous — it is a pure transformation with no I/O.

    Args:
        score:   ICPScore to guard (mutated in place).
        signals: The signals used for the assessment.

    Returns:
        The (mutated) ICPScore after guards are applied.
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
