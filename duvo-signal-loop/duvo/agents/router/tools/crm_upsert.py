"""Router tool for CRM account write-back."""

from duvo import config
from duvo.agents.router.tools.schema import router_tool_schema
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import ToolSpec
from duvo.models import RunResult
from duvo.writeback import crm

_log = get_logger(__name__)

CRM_UPSERT_TOOL: ToolSpec = router_tool_schema(
    "crm_upsert",
    "Log the company in the CRM with its ICP score and an evidence note. Always allowed.",
)


def make_crm_upsert_tool(rr: RunResult, dry_run: bool):
    """Build the CRM upsert tool implementation for one routing run."""
    s = rr.score

    async def crm_upsert() -> str:
        if dry_run:
            status = f"[dry-run] upsert account + note into {config.CRM_PROVIDER} (ICP {s.score})"
            rr.crm_status = status
        else:
            status = await crm.upsert_account(s)
            rr.crm_status = status
        _log.info("crm_upsert result: %s", status)
        return status

    return crm_upsert
