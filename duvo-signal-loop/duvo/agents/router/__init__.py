"""Router agent package."""

from duvo.agents.router.policy import is_confident_tier1, lead_email_for
from duvo.agents.router.router import ROUTER_SYSTEM, run_router
from duvo.agents.router.tools.schema import router_tool_schema

_tool_schema = router_tool_schema

__all__ = [
    "ROUTER_SYSTEM",
    "_tool_schema",
    "is_confident_tier1",
    "lead_email_for",
    "router_tool_schema",
    "run_router",
]
