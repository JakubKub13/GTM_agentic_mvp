"""Tier-1 alert to #sales via incoming webhook."""
from duvo import config
from duvo.config import require
from duvo.infra import http_client
from duvo.infra.logging_setup import get_logger
from duvo.models import ICPScore

log = get_logger(__name__)


async def alert_tier1(score: ICPScore) -> str:
    """Post a Tier-1 block-kit alert to the #sales Slack channel via incoming webhook.

    Args:
        score: A fully-populated :class:`~models.ICPScore` for a Tier-1 account.

    Returns:
        The string ``"alert posted to #sales"`` on success.

    Raises:
        RuntimeError: When ``SLACK_WEBHOOK_URL`` is not configured.
        httpx.HTTPStatusError: When Slack responds with a non-2xx status code.
    """
    webhook_url = require("SLACK_WEBHOOK_URL", config.SLACK_WEBHOOK_URL)
    log.info("slack: posting Tier-1 alert for company=%s score=%s", score.company_name, score.score)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"\U0001f7e2 Tier 1: {score.company_name} ({score.score}/10)",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Persona*\n{score.recommended_persona}"},
                {"type": "mrkdwn", "text": f"*Confidence*\n{score.confidence}"},
                {"type": "mrkdwn", "text": f"*Angle*\n{score.recommended_angle}"},
                {"type": "mrkdwn", "text": f"*Domain*\n{score.domain}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Drafted opener (paused in outreach — approve to send):*\n"
                    f">{score.outreach.first_line}"
                ),
            },
        },
    ]

    try:
        r = await http_client.get_client().post(
            webhook_url,
            json={"blocks": blocks, "text": f"Tier 1: {score.company_name}"},
        )
        r.raise_for_status()
    except Exception:
        log.error(
            "slack: webhook POST failed for company=%s — check SLACK_WEBHOOK_URL and Slack status",
            score.company_name,
        )
        raise

    log.info("slack: alert successfully posted for company=%s", score.company_name)
    return "alert posted to #sales"
