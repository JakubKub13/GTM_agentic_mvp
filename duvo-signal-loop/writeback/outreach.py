"""Outreach dispatcher — one interface the router uses; provider chosen by OUTREACH_PROVIDER."""
import config
from logging_setup import get_logger
from models import ICPScore

log = get_logger(__name__)


async def queue_lead(score: ICPScore, test_email: str) -> str:
    """Queue a Tier-1 lead into the configured outreach provider's review list/paused campaign.

    The provider is chosen at call time by reading ``config.OUTREACH_PROVIDER`` so that
    tests can monkeypatch the value without reloading this module.

    Args:
        score:      Fully-populated :class:`~models.ICPScore` for the lead.
        test_email: Email address to enrol in the outreach provider.

    Returns:
        A provider-specific confirmation string.
    """
    provider = config.OUTREACH_PROVIDER
    log.info("outreach: dispatching to provider=%s for company=%s", provider, score.company_name)

    if provider == "lemlist":
        from writeback import lemlist
        return await lemlist.queue_lead(score, test_email)

    if provider != "brevo":
        log.warning("unknown OUTREACH_PROVIDER=%r — defaulting to brevo", provider)

    from writeback import brevo
    return await brevo.queue_lead(score, test_email)
