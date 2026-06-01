"""Shared test helpers for the duvo-signal-loop test suite."""
from unittest.mock import AsyncMock, MagicMock

from models import ICPScore, OutreachDraft


def _fake_response(status_code: int, json_data: dict | None = None, raise_on_raise: bool = False):
    """Create a fake httpx.Response-like object for use in tests.

    Compatible with both old (requests-style) and new (httpx-style) call sites.
    All of status_code, .json(), and .raise_for_status() are synchronous on
    httpx responses, so this mock keeps that contract.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    if raise_on_raise:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    return resp


def make_fake_async_client(
    post: AsyncMock | None = None,
    get: AsyncMock | None = None,
    patch: AsyncMock | None = None,
):
    """Build a fake async HTTP client whose .post/.get/.patch are AsyncMocks.

    Pass pre-configured AsyncMock instances to control return values or
    side_effect chains.  Any method not supplied defaults to a no-op AsyncMock
    returning an empty 200 fake response.

    Example::

        client = make_fake_async_client(
            post=AsyncMock(return_value=_fake_response(201, {"id": "abc"})),
        )
        with patch("http_client.get_client", return_value=client):
            result = await my_module.some_func(score)
    """
    client = MagicMock()
    client.post = post if post is not None else AsyncMock(return_value=_fake_response(200))
    client.get = get if get is not None else AsyncMock(return_value=_fake_response(200))
    client.patch = patch if patch is not None else AsyncMock(return_value=_fake_response(200))
    return client


def make_score(
    company_name: str = "Acme Corp",
    domain: str = "acme.com",
    score: int = 8,
    tier: str = "Tier 1",
    confidence: str = "high",
    why_fit: list[str] | None = None,
    why_not: list[str] | None = None,
    recommended_persona: str = "VP of Ops",
    recommended_angle: str = "cost reduction",
    reasoning: str = "Strong fit based on hiring signals.",
    needs_human_research: bool = False,
    outreach_persona: str = "VP of Ops",
    outreach_subject: str = "Streamline ops at Acme",
    outreach_first_line: str = "Hi, noticed you're scaling quickly.",
    outreach_body: str = "Full body here.",
) -> ICPScore:
    """Build an ICPScore for testing.  All fields accept keyword overrides."""
    return ICPScore(
        company_name=company_name,
        domain=domain,
        score=score,
        tier=tier,
        confidence=confidence,
        why_fit=why_fit if why_fit is not None else ["scales fast", "right persona"],
        why_not=why_not if why_not is not None else ["competitive market"],
        recommended_persona=recommended_persona,
        recommended_angle=recommended_angle,
        reasoning=reasoning,
        needs_human_research=needs_human_research,
        outreach=OutreachDraft(
            persona=outreach_persona,
            subject=outreach_subject,
            first_line=outreach_first_line,
            body=outreach_body,
        ),
    )
