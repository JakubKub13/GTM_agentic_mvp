"""CRM dispatcher — the single interface the router uses; provider chosen by CRM_PROVIDER."""
from duvo import config
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore

log = get_logger(__name__)


async def upsert_account(score: ICPScore) -> str:
    """Route the upsert to the configured CRM provider.

    Reads ``config.CRM_PROVIDER`` at call time so that tests can monkeypatch
    the value without needing to reload this module.

    Args:
        score: Fully-populated :class:`~models.ICPScore` to write to the CRM.

    Returns:
        A human-readable confirmation string from the underlying adapter.
    """
    provider = config.CRM_PROVIDER
    log.info("CRM dispatcher: routing to provider=%s for company=%s", provider, score.company_name)

    if provider == "hubspot":
        from duvo.writeback import hubspot
        return await hubspot.upsert_account(score)

    if provider != "attio":
        log.warning("unknown CRM_PROVIDER=%r — defaulting to attio", provider)

    from duvo.writeback import attio
    return await attio.upsert_account(score)
