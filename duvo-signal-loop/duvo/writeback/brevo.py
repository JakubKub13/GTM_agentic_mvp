"""Brevo outreach: create the contact in a Tier-1 review list. Never sends — a rep approves + sends."""

from duvo import config
from duvo.config import require
from duvo.infra import http_client, retry
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore
from duvo.writeback.registry import register_outreach

log = get_logger(__name__)

_BASE = "https://api.brevo.com/v3"


def _headers() -> dict:
    """Return Brevo authentication headers; raises RuntimeError if BREVO_API_KEY is missing."""
    return {
        "api-key": require("BREVO_API_KEY", config.BREVO_API_KEY),
        "Content-Type": "application/json",
        "accept": "application/json",
    }


@register_outreach("brevo")
async def queue_lead(score: ICPScore, test_email: str) -> str:
    """Add the lead as a contact to the Brevo review list.

    Tries first with the full rich payload (custom attributes). If Brevo rejects that
    shape with a >= 300 response (e.g. unmapped custom attributes), falls back to a
    minimal payload of email + listIds only so the lead always lands in the list.

    Args:
        score:      Fully-populated :class:`~models.ICPScore` for the lead.
        test_email: Email address to enrol in Brevo.

    Returns:
        A confirmation string mentioning the list id when the contact is queued.

    Raises:
        httpx.HTTPStatusError: When both payloads fail (last response raises).
    """
    list_id = int(require("BREVO_LIST_ID", config.BREVO_LIST_ID))
    log.info(
        "brevo: queuing contact for company=%s list_id=%s email=%s",
        score.company_name,
        list_id,
        test_email,
    )

    # Custom attributes must exist on the account or Brevo 400s; try the rich payload,
    # then fall back to email + list only so the lead always lands.
    full = {
        "email": test_email,
        "attributes": {
            "COMPANY": score.company_name,
            "DOMAIN": score.domain,
            "JOBTITLE": score.recommended_persona,
            "ICP_SCORE": str(score.score),
            "ICEBREAKER": score.outreach.first_line,
        },
        "listIds": [list_id],
        "updateEnabled": True,
    }
    minimal = {"email": test_email, "listIds": [list_id], "updateEnabled": True}

    last = None
    for idx, payload in enumerate((full, minimal)):
        if idx > 0:
            log.warning(
                "brevo: full payload rejected — falling back to minimal payload for company=%s",
                score.company_name,
            )
        last = await retry.with_retries(
            lambda payload=payload: http_client.get_client().post(
                f"{_BASE}/contacts", headers=_headers(), json=payload
            ),
            max_attempts=config.HTTP_MAX_RETRIES,
        )
        if last.status_code < 300:
            log.info(
                "brevo: contact queued successfully for company=%s list_id=%s",
                score.company_name,
                list_id,
            )
            return f"contact queued in Brevo review list {list_id} (not sent — rep reviews & sends)"

    log.error(
        "brevo: both payloads failed for company=%s list_id=%s — raising last error",
        score.company_name,
        list_id,
    )
    last.raise_for_status()  # type: ignore[union-attr]
    return ""  # unreachable; satisfies type checkers
