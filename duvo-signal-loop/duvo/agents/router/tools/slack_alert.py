"""Router tool for guarded Slack Tier-1 alerts."""

from duvo.agents.router.tools.schema import router_tool_schema
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import ToolSpec
from duvo.models import RunResult

_log = get_logger(__name__)

SLACK_ALERT_TOOL: ToolSpec = router_tool_schema(
    "slack_alert",
    "Post a Tier-1 alert to #sales. Only for confident Tier 1.",
)


def make_slack_alert_tool(rr: RunResult, dry_run: bool, confident_t1: bool):
    """Build the Slack alert tool implementation for one routing run."""
    s = rr.score

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
            from duvo.writeback import slack

            try:
                status = await slack.alert_tier1(s)
            except Exception as exc:
                status = f"failed: {exc}"
                _log.warning("slack_alert failed for %s: %s", s.company_name, exc)
            rr.slack_status = status
        _log.info("slack_alert result: %s", status)
        return status

    return slack_alert
