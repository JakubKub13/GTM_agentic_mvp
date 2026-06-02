"""lemlist adapter: queue a Tier-1 lead into a PAUSED campaign as a draft. Same interface as brevo."""
from duvo import config
from duvo.config import require
from duvo.infra import http_client
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore

log = get_logger(__name__)

_BASE = "https://api.lemlist.com/api"


async def queue_lead(score: ICPScore, test_email: str) -> str:
    """Add the lead to a paused lemlist campaign for rep review before sending.

    Args:
        score:      Fully-populated :class:`~models.ICPScore` for the lead.
        test_email: Email address to enrol in the campaign.

    Returns:
        A confirmation string mentioning the campaign id when the lead is queued.

    Raises:
        RuntimeError: When ``LEMLIST_CAMPAIGN_ID`` or ``LEMLIST_API_KEY`` is not configured.
        httpx.HTTPStatusError: When lemlist responds with a non-2xx status code.
    """
    # Check API key first so its error takes precedence over campaign id when both are missing
    api_key = require("LEMLIST_API_KEY", config.LEMLIST_API_KEY)
    campaign = require("LEMLIST_CAMPAIGN_ID", config.LEMLIST_CAMPAIGN_ID)

    log.info(
        "lemlist: queuing lead for company=%s campaign=%s email=%s",
        score.company_name, campaign, test_email,
    )

    url = f"{_BASE}/campaigns/{campaign}/leads"
    payload = {
        "email": test_email,
        "companyName": score.company_name,
        "companyDomain": score.domain,
        "jobTitle": score.recommended_persona,
        "icpScore": str(score.score),
        "icebreaker": score.outreach.first_line,
    }

    r = await http_client.get_client().post(
        url,
        auth=("", api_key),
        json=payload,
        params={"deduplicate": "true"},
    )

    try:
        r.raise_for_status()
    except Exception:
        log.error(
            "lemlist: failed to queue lead for company=%s campaign=%s — status=%s",
            score.company_name, campaign, r.status_code,
        )
        raise

    log.info(
        "lemlist: lead successfully queued for company=%s campaign=%s",
        score.company_name, campaign,
    )
    return f"lead queued in paused lemlist campaign {campaign} (awaiting rep approval)"
