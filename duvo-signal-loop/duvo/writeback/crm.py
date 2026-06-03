"""CRM dispatcher — the single interface the router uses; provider chosen by CRM_PROVIDER."""

from duvo import config
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore
from duvo.writeback.registry import get_crm

log = get_logger(__name__)


async def upsert_account(score: ICPScore) -> str:
    """Route the upsert to the CRM provider named by ``config.CRM_PROVIDER`` (read at call time)."""
    provider = config.CRM_PROVIDER
    log.info("CRM dispatcher: routing to provider=%s for company=%s", provider, score.company_name)
    fn = get_crm(provider)
    return await fn(score)
