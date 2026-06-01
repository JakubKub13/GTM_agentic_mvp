"""CRM dispatcher — the single interface the router uses; provider chosen by CRM_PROVIDER."""
import config
from logging_setup import get_logger
from models import ICPScore

log = get_logger(__name__)


def upsert_account(score: ICPScore) -> str:
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
        from writeback import hubspot
        return hubspot.upsert_account(score)

    from writeback import attio
    return attio.upsert_account(score)
