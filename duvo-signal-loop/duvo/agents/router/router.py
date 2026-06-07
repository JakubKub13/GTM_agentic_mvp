"""Router agent: decides how to action a scored account."""

import json

from duvo import config
from duvo.agent_core import run_agent
from duvo.agents.router.policy import is_confident_tier1
from duvo.agents.router.prompts import load_router_prompt
from duvo.agents.router.tools import build_router_toolset
from duvo.infra import events, tracing
from duvo.infra.logging_setup import get_logger
from duvo.models import RunResult

_log = get_logger(__name__)

ROUTER_SYSTEM = load_router_prompt()


async def run_router(
    rr: RunResult, dry_run: bool, test_email: str = config.TEST_EMAIL, log=None
) -> None:
    """Drive the routing agent to decide write-back actions for a scored account.

    The agent is given the ICP score and picks which write-back tools to call.
    Each tool self-guards its own safety logic (Tier-1 gate) and respects
    *dry_run* to avoid real API calls during development or CI.
    """
    s = rr.score
    confident_t1 = is_confident_tier1(s)

    # Plan #9: tag this task's producer context with the router role so tool events
    # from run_agent are attributed correctly in the live feed.
    events.enrich_context(agent="router", beat=None)

    _log.info(
        "router start: company=%s tier=%s confident_t1=%s dry_run=%s",
        s.company_name,
        s.tier,
        confident_t1,
        dry_run,
    )

    tools, impls = build_router_toolset(rr, dry_run, test_email, confident_t1)
    user = json.dumps(
        {
            "company": s.company_name,
            "domain": s.domain,
            "score": s.score,
            "tier": s.tier,
            "confidence": s.confidence,
            "needs_human_research": s.needs_human_research,
            "persona": s.recommended_persona,
            "angle": s.recommended_angle,
        },
        ensure_ascii=False,
    )

    with tracing.span(
        name="🚦 router",
        as_type="agent",
        input=user,
        metadata={"company": s.company_name, "tier": s.tier, "confident_t1": confident_t1},
    ) as agent_span:
        await run_agent(
            ROUTER_SYSTEM, user, tools, impls, max_turns=6, final_tools={"finish"}, log=log
        )
        if agent_span is not None:
            agent_span.update(
                output={
                    "crm": rr.crm_status,
                    "slack": rr.slack_status,
                    "outreach": rr.outreach_status,
                }
            )
