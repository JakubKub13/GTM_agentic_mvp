"""The ``writeback_events`` table: a per-channel audit ledger of write-back actions."""

from duvo.store import db

_CHANNEL_OK = {"crm": "upserted", "slack": "alerted", "outreach": "queued"}


def derive_action(channel: str, status_text: str) -> str:
    """Map a write-back status string to a coarse ledger action.

    ``refused`` / ``failed`` / ``skipped`` are detected by prefix; otherwise the
    channel's success verb is used (crm→upserted, slack→alerted, outreach→queued).
    """
    t = (status_text or "").strip().lower()
    if t.startswith("refused"):
        return "refused"
    if t.startswith("failed") or t.startswith("error"):
        return "failed"
    if t.startswith("skipped") or t == "":
        return "skipped"
    return _CHANNEL_OK.get(channel, "ok")


async def record_event(
    *,
    run_id: str,
    domain: str,
    channel: str,
    provider: str,
    status_text: str,
    changed: bool,
    prev_score: int | None,
    prev_tier: str | None,
    created_at: str,
) -> None:
    """Insert (or update on re-run) one write-back event row.

    The ``action`` is derived from *status_text*; ``idempotency_key`` is
    ``domain:channel`` (the natural per-channel key for future suppression).
    """
    await db.execute(
        """
        INSERT INTO writeback_events
            (run_id, domain, channel, provider, action, changed, prev_score,
             prev_tier, status_text, idempotency_key, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, domain, channel) DO UPDATE SET
            provider = excluded.provider,
            action = excluded.action,
            changed = excluded.changed,
            prev_score = excluded.prev_score,
            prev_tier = excluded.prev_tier,
            status_text = excluded.status_text,
            created_at = excluded.created_at
        """,
        (
            run_id,
            domain,
            channel,
            provider,
            derive_action(channel, status_text),
            int(changed),
            prev_score,
            prev_tier,
            status_text,
            f"{domain}:{channel}",
            created_at,
        ),
    )
