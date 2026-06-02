"""HubSpot adapter: custom property + upsert company + evidence note. Same interface as attio."""
import time

from duvo import config
from duvo.config import require
from duvo.infra import http_client
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore

log = get_logger(__name__)

_BASE = "https://api.hubapi.com"
_NOTE_TO_COMPANY = 190  # HubSpot default association type id


def _headers() -> dict:
    """Return authorisation headers; raises RuntimeError if HUBSPOT_TOKEN is missing."""
    return {
        "Authorization": f"Bearer {require('HUBSPOT_TOKEN', config.HUBSPOT_TOKEN)}",
        "Content-Type": "application/json",
    }


def _get_client():
    """Convenience alias so call sites read naturally."""
    return http_client.get_client()


async def ensure_icp_property() -> None:
    """Ensure the ``icp_score`` custom property exists on HubSpot companies.

    If the property already exists (GET returns 200) we skip creation silently.
    Otherwise we POST the property definition.
    """
    url = f"{_BASE}/crm/v3/properties/companies/icp_score"
    log.debug("HubSpot: checking whether icp_score property exists")
    resp = await _get_client().get(url, headers=_headers())
    if resp.status_code == 200:
        log.debug("HubSpot: icp_score property already exists — skipping creation")
        return
    log.info("HubSpot: creating icp_score custom property on companies")
    resp2 = await _get_client().post(
        f"{_BASE}/crm/v3/properties/companies",
        headers=_headers(),
        json={
            "name": "icp_score",
            "label": "ICP Score",
            "type": "number",
            "fieldType": "number",
            "groupName": "companyinformation",
        },
    )
    resp2.raise_for_status()


async def _upsert_company(score: ICPScore) -> str:
    """Search for the company by domain; PATCH if found, POST if not.

    Returns:
        The HubSpot company id string.
    """
    log.info("HubSpot: upserting company '%s' (domain=%s)", score.company_name, score.domain)

    search = {
        "filterGroups": [
            {
                "filters": [
                    {"propertyName": "domain", "operator": "EQ", "value": score.domain}
                ]
            }
        ],
        "properties": ["domain", "name"],
    }
    r = await _get_client().post(
        f"{_BASE}/crm/v3/objects/companies/search",
        headers=_headers(),
        json=search,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    props = {
        "name": score.company_name,
        "domain": score.domain,
        "icp_score": score.score,
    }

    if results:
        cid: str = results[0]["id"]
        log.info("HubSpot: company found (id=%s) — PATCHing properties", cid)
        pr = await _get_client().patch(
            f"{_BASE}/crm/v3/objects/companies/{cid}",
            headers=_headers(),
            json={"properties": props},
        )
        pr.raise_for_status()
    else:
        log.info("HubSpot: company not found — creating new record for '%s'", score.company_name)
        cr = await _get_client().post(
            f"{_BASE}/crm/v3/objects/companies",
            headers=_headers(),
            json={"properties": props},
        )
        cr.raise_for_status()
        cid = cr.json()["id"]
        log.info("HubSpot: company created (id=%s)", cid)

    return cid


def _note_body(score: ICPScore) -> str:
    """Build the HTML-friendly body (BR-joined) for the HubSpot engagement note."""
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
    return "<br>".join(lines)


async def _create_note(score: ICPScore, company_id: str) -> None:
    """Create a HubSpot note (engagement) associated with the company."""
    log.info("HubSpot: creating evidence note for company id=%s", company_id)
    payload = {
        "properties": {
            "hs_note_body": _note_body(score),
            "hs_timestamp": int(time.time() * 1000),
        },
        "associations": [
            {
                "to": {"id": company_id},
                "types": [
                    {
                        "associationCategory": "HUBSPOT_DEFINED",
                        "associationTypeId": _NOTE_TO_COMPANY,
                    }
                ],
            }
        ],
    }
    resp = await _get_client().post(f"{_BASE}/crm/v3/objects/notes", headers=_headers(), json=payload)
    resp.raise_for_status()
    log.info("HubSpot: evidence note created for company id=%s", company_id)


async def upsert_account(score: ICPScore) -> str:
    """Ensure icp_score property exists, upsert the company, then attach a note.

    Args:
        score: Fully-populated :class:`~models.ICPScore`.

    Returns:
        Confirmation string including the HubSpot company id and ICP score.
    """
    await ensure_icp_property()
    cid = await _upsert_company(score)
    await _create_note(score, cid)
    return f"company {cid} (icp_score={score.score}) + evidence note"
