"""Router-specific tools."""

from duvo.agents.router.tools.crm_upsert import CRM_UPSERT_TOOL, make_crm_upsert_tool
from duvo.agents.router.tools.finish import FINISH_TOOL, make_finish_tool
from duvo.agents.router.tools.outreach_queue import (
    OUTREACH_QUEUE_TOOL,
    make_outreach_queue_tool,
)
from duvo.agents.router.tools.slack_alert import SLACK_ALERT_TOOL, make_slack_alert_tool
from duvo.models import RunResult


def build_router_toolset(
    rr: RunResult,
    dry_run: bool,
    test_email: str,
    confident_t1: bool,
):
    """Return the router tool specs and implementations for one routing run."""
    tools = [CRM_UPSERT_TOOL, SLACK_ALERT_TOOL, OUTREACH_QUEUE_TOOL, FINISH_TOOL]
    impls = {
        "crm_upsert": make_crm_upsert_tool(rr, dry_run),
        "slack_alert": make_slack_alert_tool(rr, dry_run, confident_t1),
        "outreach_queue": make_outreach_queue_tool(rr, dry_run, test_email, confident_t1),
        "finish": make_finish_tool(rr),
    }
    return tools, impls


__all__ = [
    "CRM_UPSERT_TOOL",
    "FINISH_TOOL",
    "OUTREACH_QUEUE_TOOL",
    "SLACK_ALERT_TOOL",
    "build_router_toolset",
    "make_crm_upsert_tool",
    "make_finish_tool",
    "make_outreach_queue_tool",
    "make_slack_alert_tool",
]
