"""Deterministic router policy helpers."""

import re

from duvo.models import ICPScore


def is_confident_tier1(score: ICPScore) -> bool:
    """Return whether a score is eligible for Slack and outreach actions."""
    return score.tier == "Tier 1" and not score.needs_human_research


def lead_email_for(base_email: str, domain: str) -> str:
    """Derive a per-account lead email via plus-addressing.

    The demo uses one real inbox for every lead, but queueing them all under the
    same address makes the outreach tool collapse them into a single contact
    (last write wins). Plus-addressing gives each account a unique address that
    still delivers to the same inbox, so each lead becomes a distinct contact.
    """
    if "@" not in base_email:
        return base_email
    tag = re.sub(r"[^a-z0-9]+", "-", domain.lower()).strip("-")
    if not tag:
        return base_email
    local, _, host = base_email.partition("@")
    base_local = local.split("+", 1)[0]  # drop any existing +tag so we don't stack
    return f"{base_local}+{tag}@{host}"
