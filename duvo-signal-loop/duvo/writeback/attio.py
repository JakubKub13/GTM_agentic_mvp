"""Attio CRM write-back: create a company record + an evidence note (AI-suggested)."""

from duvo import config
from duvo.config import require
from duvo.infra import http_client, retry
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore
from duvo.writeback.registry import register_crm

log = get_logger(__name__)

_BASE = "https://api.attio.com/v2"


def _headers() -> dict:
    """Return authorisation headers; raises RuntimeError if ATTIO_API_KEY is missing."""
    return {
        "Authorization": f"Bearer {require('ATTIO_API_KEY', config.ATTIO_API_KEY)}",
        "Content-Type": "application/json",
    }


def _note_body(score: ICPScore) -> str:
    """Build the plaintext body for the evidence note."""
    lines = [
        "AI-SUGGESTED — review before outreach.",
        f"ICP score: {score.score}/10 ({score.tier}), confidence {score.confidence}.",
        f"Persona: {score.recommended_persona}",
        f"Angle: {score.recommended_angle}",
        "",
        "Why fit: " + "; ".join(score.why_fit),
        "Why not: " + "; ".join(score.why_not),
        "",
        "Reasoning: " + score.reasoning,
        "",
        "Drafted opener: " + score.outreach.first_line,
    ]
    if score.needs_human_research:
        lines.insert(1, ">> FLAGGED: signals too thin — human research needed before contact.")
    return "\n".join(lines)


async def _create_company(score: ICPScore) -> str:
    """POST a new company record to Attio.

    Tries first with ``domains`` included.  If Attio rejects that shape (any
    non-2xx response) it retries with ``name`` only.  Raises on two consecutive
    failures.

    Returns:
        The Attio ``record_id`` string for the created company.
    """
    url = f"{_BASE}/objects/companies/records"
    log.info("Attio: creating company record for '%s' (%s)", score.company_name, score.domain)

    last = None
    for attempt, values in enumerate(
        (
            {"name": score.company_name, "domains": [score.domain]},
            {"name": score.company_name},
        )
    ):
        if attempt > 0:
            log.debug(
                "Attio: first payload shape rejected — retrying with name-only for '%s'",
                score.company_name,
            )
        last = await retry.with_retries(
            lambda values=values: http_client.get_client().post(
                url, headers=_headers(), json={"data": {"values": values}}
            ),
            max_attempts=config.HTTP_MAX_RETRIES,
        )
        if last.status_code < 300:
            record_id: str = last.json()["data"]["id"]["record_id"]
            log.info("Attio: company record created — record_id=%s", record_id)
            return record_id

    # Both attempts failed; surface the error from the last response.
    log.error(
        "Attio: failed to create company '%s' after retry — status=%s",
        score.company_name,
        last.status_code if last else "unknown",
    )
    last.raise_for_status()  # type: ignore[union-attr]
    return ""  # unreachable; satisfies type checkers


async def _create_note(score: ICPScore, record_id: str) -> None:
    """POST an evidence note attached to the given company record."""
    payload = {
        "data": {
            "parent_object": "companies",
            "parent_record_id": record_id,
            "title": f"ICP {score.score}/10 ({score.tier}) — AI-suggested, review before outreach",
            "format": "plaintext",
            "content": _note_body(score),
        }
    }
    log.info("Attio: creating evidence note for record_id=%s", record_id)
    resp = await retry.with_retries(
        lambda: http_client.get_client().post(f"{_BASE}/notes", headers=_headers(), json=payload),
        max_attempts=config.HTTP_MAX_RETRIES,
    )
    resp.raise_for_status()
    log.info("Attio: evidence note created for record_id=%s", record_id)


@register_crm("attio")
async def upsert_account(score: ICPScore) -> str:
    """Create a company record + evidence note in Attio.

    Args:
        score: Fully-populated :class:`~models.ICPScore`.

    Returns:
        Confirmation string including the Attio record id and ICP score.
    """
    record_id = await _create_company(score)
    await _create_note(score, record_id)
    return f"attio company {record_id} (ICP {score.score}) + evidence note"
