"""Router terminal tool."""

from duvo.agents.router.tools.schema import router_tool_schema
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import ToolSpec
from duvo.models import RunResult

_log = get_logger(__name__)

FINISH_TOOL: ToolSpec = router_tool_schema("finish", "Call when routing is complete.")


def make_finish_tool(rr: RunResult):
    """Build the terminal finish tool implementation for one routing run."""
    s = rr.score

    async def finish() -> str:
        _log.info(
            "routing complete: company=%s crm=%s slack=%s outreach=%s",
            s.company_name,
            rr.crm_status,
            rr.slack_status,
            rr.outreach_status,
        )
        return "done"

    return finish
