"""Credentials + model. Exa + Anthropic always required; write-back keys validated lazily."""
import os
from dotenv import load_dotenv

load_dotenv()

EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CRM_PROVIDER = os.environ.get("CRM_PROVIDER", "attio")  # "attio" (default) | "hubspot"
ATTIO_API_KEY = os.environ.get("ATTIO_API_KEY", "")
HUBSPOT_TOKEN = os.environ.get("HUBSPOT_TOKEN", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
OUTREACH_PROVIDER = os.environ.get("OUTREACH_PROVIDER", "brevo")  # "brevo" (default) | "lemlist"
BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
BREVO_LIST_ID = os.environ.get("BREVO_LIST_ID", "")
LEMLIST_API_KEY = os.environ.get("LEMLIST_API_KEY", "")
LEMLIST_CAMPAIGN_ID = os.environ.get("LEMLIST_CAMPAIGN_ID", "")
TEST_EMAIL = os.environ.get("TEST_EMAIL", "jakubkubala3@gmail.com")

CLAUDE_MODEL = "claude-sonnet-4-6"  # fast + strong for live demo; swap to opus if desired
MAX_SCOUT_SEARCHES = 4              # hard cap per scout agent
MAX_ANALYST_SEARCHES = 2           # hard cap on analyst verification searches

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

MAX_CONCURRENT_ACCOUNTS = int(os.environ.get("MAX_CONCURRENT_ACCOUNTS", "5"))   # account-level fan-out cap
HTTP_TIMEOUT_SECONDS = float(os.environ.get("HTTP_TIMEOUT_SECONDS", "30"))       # per write-back HTTP request
ANTHROPIC_TIMEOUT_SECONDS = float(os.environ.get("ANTHROPIC_TIMEOUT_SECONDS", "120"))  # per model call
ACCOUNT_TIMEOUT_SECONDS = float(os.environ.get("ACCOUNT_TIMEOUT_SECONDS", "300"))  # wall-clock cap per account (5 min)


def require(name: str, value: str) -> str:
    """Return *value* if non-empty; raise RuntimeError mentioning *name* otherwise.

    Use this for credentials that are strictly required at runtime (e.g. API keys
    that must be present before making a live network call). Leave optional write-back
    keys un-guarded so that ``--dry-run`` mode can operate without a full .env.

    Args:
        name:  The environment variable name (used in the error message).
        value: The resolved value from ``os.environ.get``.

    Returns:
        *value* unchanged when it is non-empty.

    Raises:
        RuntimeError: When *value* is an empty string, with a message that includes
            *name* so the operator knows exactly which variable to fill in.
    """
    if not value.strip():
        raise RuntimeError(f"Missing required env var: {name}. Add it to .env")
    return value
