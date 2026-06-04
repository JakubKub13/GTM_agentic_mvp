"""Analyst agent: validate signals, score ICP fit, and draft outreach."""

import json
from typing import Any

from duvo.agent_core import run_agent
from duvo.agents.analyst.guards import (
    apply_guards,
    conservative_default,
    score_from_assessment_payload,
)
from duvo.agents.analyst.prompts import load_analyst_prompt
from duvo.agents.analyst.tools.record_assessment import (
    RECORD_ASSESSMENT_TOOL,
    make_record_assessment_tool,
)
from duvo.config import MAX_ANALYST_SEARCHES
from duvo.infra import tracing
from duvo.infra.logging_setup import get_logger
from duvo.models import Company, ICPScore, Signal
from duvo.shared_agentic_tools.exa_tool import EXA_SEARCH_TOOL, exa_search

_log = get_logger(__name__)

# The analyst's record_assessment payload is large (why_fit/why_not arrays, reasoning,
# plus the full nested outreach draft). The default 2000-token cap can truncate the
# outreach object mid-JSON, yielding an empty/malformed draft that falls back to blank.
# Give the analyst headroom so the draft completes.
ANALYST_MAX_TOKENS = 4096


async def run_analyst(company: Company, signals: list[Signal], log=None) -> ICPScore:
    """Run the analyst agent to assess ICP fit and draft outreach.

    The agent can optionally run verification searches (up to MAX_ANALYST_SEARCHES)
    before calling record_assessment. If it never calls record_assessment, a
    conservative default ICPScore is returned.

    Args:
        company: The company to assess.
        signals: Signals gathered by the scouts.
        log:     Optional audit log list passed through to run_agent.

    Returns:
        An ICPScore with deterministic guards applied.
    """
    _log.info("analyst starting: company=%r signals=%d", company.name, len(signals))

    signals_json = json.dumps([s.model_dump() for s in signals], ensure_ascii=False, indent=2)
    system = load_analyst_prompt(max_analyst_searches=MAX_ANALYST_SEARCHES)
    user = (
        f"Company: {company.name} ({company.domain}, {company.country}). "
        f"Context: {company.description}\n\nSignals:\n{signals_json}"
    )

    captured: dict[str, Any] = {}
    impls = {
        "exa_search": exa_search,
        "record_assessment": make_record_assessment_tool(captured),
    }
    with tracing.span(
        name="🧠 analyst",
        as_type="agent",
        input=user,
        metadata={"company": company.domain, "signals": len(signals)},
    ) as agent_span:
        await run_agent(
            system,
            user,
            [EXA_SEARCH_TOOL, RECORD_ASSESSMENT_TOOL],
            impls,
            max_turns=MAX_ANALYST_SEARCHES + 3,
            final_tools={"record_assessment"},
            log=log,
            max_tokens=ANALYST_MAX_TOKENS,
        )

        if not captured:
            _log.warning(
                "analyst: no record_assessment call from agent for company=%r — using conservative default",
                company.name,
            )
            result = conservative_default(company)
            if agent_span is not None:
                agent_span.update(output={"score": result.score, "tier": result.tier, "fallback": True})
            return result

        score = score_from_assessment_payload(company, captured)
        with tracing.span(
            name="🛡️ apply_guards",
            as_type="guardrail",
            input={
                "score": score.score,
                "tier": score.tier,
                "confidence": score.confidence,
                "needs_human_research": score.needs_human_research,
                "dated_signals": sum(1 for s in signals if s.published_date),
            },
        ) as gspan:
            result = apply_guards(score, signals)
            if gspan is not None:
                gspan.update(
                    output={
                        "score": result.score,
                        "tier": result.tier,
                        "confidence": result.confidence,
                        "needs_human_research": result.needs_human_research,
                    }
                )
        if agent_span is not None:
            agent_span.update(output={"score": result.score, "tier": result.tier})

    _log.info(
        "analyst done: company=%r score=%d tier=%s confidence=%s needs_human_research=%s",
        company.name,
        result.score,
        result.tier,
        result.confidence,
        result.needs_human_research,
    )
    return result
