"""Router agent: decides how to action a scored account; its tools are the write-backs."""
import json

import config
from models import RunResult
from agent_core import run_agent
from logging_setup import get_logger
from writeback import crm

_log = get_logger(__name__)

ROUTER_SYSTEM = (
    "You are Duvo's GTM routing agent. You decide how to action one scored account into the "
    "sales stack. Rules:\n"
    "- ALWAYS call crm_upsert to log the account in the CRM with its evidence note.\n"
    "- If the account is a CONFIDENT Tier 1 (tier == 'Tier 1' and needs_human_research is false), "
    "also call slack_alert and outreach_queue (queues the lead for a rep to review and send — "
    "never sent automatically).\n"
    "- If it is flagged needs_human_research, or not Tier 1, ONLY call crm_upsert.\n"
    "Call finish when done. Some tools may refuse if their own safety check fails — that is "
    "expected; do not retry a refused tool."
)


def _tool_schema(name: str, desc: str) -> dict:
    """Build a minimal Anthropic tool schema dict with no input parameters.

    Args:
        name: The tool name (must match the key in the ``impls`` dict).
        desc: A human-readable description shown to the model.

    Returns:
        A dict suitable for passing to ``run_agent`` as one entry in *tools*.
    """
    return {
        "name": name,
        "description": desc,
        "input_schema": {"type": "object", "properties": {}},
    }


async def run_router(rr: RunResult, dry_run: bool, test_email: str = config.TEST_EMAIL, log=None) -> None:
    """Drive the routing agent to decide write-back actions for a scored account.

    The agent is given the ICP score and picks which write-back tools to call.
    Each tool self-guards its own safety logic (Tier-1 gate) and respects
    *dry_run* to avoid real API calls during development or CI.

    Slack and outreach modules are imported lazily inside their tool functions
    so this module imports correctly even when those modules don't exist yet.

    Args:
        rr:         :class:`~models.RunResult` whose ``score`` drives routing.
                    ``crm_status``, ``slack_status``, and ``outreach_status``
                    are mutated in-place by the tool implementations.
        dry_run:    When ``True``, no real write-back calls are made; status
                    fields are set to descriptive ``[dry-run] ...`` strings.
        test_email: Email address used by the outreach adapter.  Defaults to
                    ``config.TEST_EMAIL`` (bound at definition time; pass
                    explicitly when the caller needs a different address).
        log:        Optional list for audit entries; forwarded verbatim to
                    :func:`~agent_core.run_agent`.

    Returns:
        ``None``.  All results are written back into *rr*.
    """
    s = rr.score
    confident_t1 = s.tier == "Tier 1" and not s.needs_human_research

    _log.info(
        "router start: company=%s tier=%s confident_t1=%s dry_run=%s",
        s.company_name, s.tier, confident_t1, dry_run,
    )

    # ------------------------------------------------------------------
    # Tool implementations (async closures — run_agent awaits them)
    # ------------------------------------------------------------------

    async def crm_upsert() -> str:
        if dry_run:
            status = f"[dry-run] upsert account + note into {config.CRM_PROVIDER} (ICP {s.score})"
            rr.crm_status = status
        else:
            status = await crm.upsert_account(s)
            rr.crm_status = status
        _log.info("crm_upsert result: %s", status)
        return status

    async def slack_alert() -> str:
        # Safety guard MUST come first — before the lazy import and any await —
        # so that a non-confident Tier-1 never imports or calls the send module.
        if not confident_t1:
            msg = "refused: not a confident Tier 1 (safety guard)"
            _log.info("slack_alert self-refused: %s", msg)
            return msg
        if dry_run:
            status = "[dry-run] alert #sales"
            rr.slack_status = status
        else:
            from writeback import slack  # lazy — module added in a later group
            status = await slack.alert_tier1(s)
            rr.slack_status = status
        _log.info("slack_alert result: %s", status)
        return status

    async def outreach_queue() -> str:
        # Safety guard MUST come first — before the lazy import and any await —
        # so that a non-confident Tier-1 never imports or calls the send module.
        if not confident_t1:
            msg = "refused: not a confident Tier 1 (safety guard)"
            _log.info("outreach_queue self-refused: %s", msg)
            return msg
        if dry_run:
            status = f"[dry-run] queue lead for review in {config.OUTREACH_PROVIDER}"
            rr.outreach_status = status
        else:
            from writeback import outreach  # lazy — module added in a later group
            status = await outreach.queue_lead(s, test_email)
            rr.outreach_status = status
        _log.info("outreach_queue result: %s", status)
        return status

    async def finish() -> str:
        """Signal the agent loop to terminate; routing is complete."""
        _log.info(
            "routing complete: company=%s crm=%s slack=%s outreach=%s",
            s.company_name, rr.crm_status, rr.slack_status, rr.outreach_status,
        )
        return "done"

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    tools = [
        _tool_schema(
            "crm_upsert",
            "Log the company in the CRM with its ICP score and an evidence note. Always allowed.",
        ),
        _tool_schema(
            "slack_alert",
            "Post a Tier-1 alert to #sales. Only for confident Tier 1.",
        ),
        _tool_schema(
            "outreach_queue",
            "Queue the lead into the outreach tool's review list for a rep to approve and send. "
            "Only for confident Tier 1.",
        ),
        _tool_schema("finish", "Call when routing is complete."),
    ]
    impls = {
        "crm_upsert": crm_upsert,
        "slack_alert": slack_alert,
        "outreach_queue": outreach_queue,
        "finish": finish,
    }

    # ------------------------------------------------------------------
    # Agent user message
    # ------------------------------------------------------------------

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

    await run_agent(ROUTER_SYSTEM, user, tools, impls, max_turns=6, final_tools={"finish"}, log=log)
