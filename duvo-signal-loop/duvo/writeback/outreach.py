"""Outreach dispatcher — one interface the router uses; provider chosen by OUTREACH_PROVIDER."""

from duvo import config
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore
from duvo.writeback.registry import get_outreach

log = get_logger(__name__)


async def queue_lead(score: ICPScore, test_email: str) -> str:
    """Route to the outreach provider named by ``config.OUTREACH_PROVIDER`` (read at call time)."""
    provider = config.OUTREACH_PROVIDER
    log.info("outreach: dispatching to provider=%s for company=%s", provider, score.company_name)
    fn = get_outreach(provider)
    return await fn(score, test_email)
