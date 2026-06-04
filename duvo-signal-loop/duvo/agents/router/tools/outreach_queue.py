"""Router tool for guarded outreach queueing."""

from duvo import config
from duvo.agents.router.policy import lead_email_for
from duvo.agents.router.tools.schema import router_tool_schema
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import ToolSpec
from duvo.models import RunResult

_log = get_logger(__name__)

OUTREACH_QUEUE_TOOL: ToolSpec = router_tool_schema(
    "outreach_queue",
    "Queue the lead into the outreach tool's review list for a rep to approve and send. "
    "Only for confident Tier 1.",
)


def make_outreach_queue_tool(
    rr: RunResult,
    dry_run: bool,
    test_email: str,
    confident_t1: bool,
):
    """Build the outreach queue tool implementation for one routing run."""
    s = rr.score

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
            from duvo.writeback import outreach

            lead_email = lead_email_for(test_email, s.domain)
            try:
                status = await outreach.queue_lead(s, lead_email)
            except Exception as exc:
                status = f"failed: {exc}"
                _log.warning("outreach_queue failed for %s: %s", s.company_name, exc)
            rr.outreach_status = status
        _log.info("outreach_queue result: %s", status)
        return status

    return outreach_queue
