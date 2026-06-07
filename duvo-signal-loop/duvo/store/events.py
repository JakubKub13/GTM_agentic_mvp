"""The ``writeback_events`` table: a per-channel audit ledger of write-back actions.

Two concerns live here:

* **Action derivation + persistence** (the post-hoc ledger row). A run's ``dry_run`` flag
  — or a ``[dry-run]`` status-text prefix — forces the coarse action to ``simulated`` so a
  persisted dry-run can never be mistaken for a real side-effect (plan #3).
* **Delivery lifecycle + run-scoped idempotency** (plan #6a). The lifecycle column moves
  ``attempting`` → ``succeeded`` / ``failed`` around the actual Slack/outreach call, and
  :func:`idempotency_decision` reads it — scoped to ``(run_id, domain, channel)`` and
  filtered to real (``dry_run=0``, non-``simulated``) rows — to decide, on resume, whether
  to fire, suppress, or surface a send for a human.
"""

from __future__ import annotations

from duvo.store import db

_CHANNEL_OK = {"crm": "upserted", "slack": "alerted", "outreach": "queued"}

#: Status-text prefix a write-back adapter emits when simulating in dry-run mode.
_DRY_RUN_PREFIX = "[dry-run]"

#: Coarse action recorded for a simulated (dry-run) write-back. The idempotency guard
#: ignores rows carrying this action (plan #3).
SIMULATED = "simulated"


def derive_action(channel: str, status_text: str, *, dry_run: bool = False) -> str:
    """Map a write-back status string to a coarse ledger action.

    A dry-run is detected two ways (plan #3): the run's ``dry_run`` flag threaded in, or a
    ``[dry-run]`` prefix on *status_text*. Either forces ``simulated`` — never
    ``upserted``/``alerted``/``queued`` — so the row is inert to real-run logic.
    Otherwise ``refused`` / ``failed`` / ``skipped`` are detected by prefix, and failing
    those the channel's success verb is used (crm→upserted, slack→alerted,
    outreach→queued).

    Args:
        channel: The write-back channel (``crm`` | ``slack`` | ``outreach``).
        status_text: The adapter's status string for this channel.
        dry_run: When True, the action is forced to ``simulated`` regardless of verb.

    Returns:
        One of ``simulated`` | ``refused`` | ``failed`` | ``skipped`` | the channel verb.
    """
    t = (status_text or "").strip().lower()
    if dry_run or t.startswith(_DRY_RUN_PREFIX):
        return SIMULATED
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
    dry_run: bool = False,
) -> None:
    """Insert (or update on re-run) one final write-back event row.

    The ``action`` is derived from *status_text* (and *dry_run*, plan #3);
    ``idempotency_key`` is ``domain:channel`` (the natural per-channel key) and the
    durable scope is the ``UNIQUE(run_id, domain, channel)`` constraint. ``delivery_status``
    is recorded as ``succeeded`` — this is the post-hoc ledger row written *after* a
    channel finished, so it is terminal; the in-flight lifecycle (``attempting`` →
    ``succeeded``/``failed``) is driven separately by :func:`set_delivery_status`.

    Args:
        run_id: The run this event belongs to.
        domain: The account's domain.
        channel: ``crm`` | ``slack`` | ``outreach``.
        provider: The concrete provider that handled the channel (e.g. ``attio``).
        status_text: The adapter's status string.
        changed: Whether score/tier differs from the prior real run.
        prev_score: The prior run's score, or None.
        prev_tier: The prior run's tier, or None.
        created_at: ISO timestamp for the row.
        dry_run: When True, the action is forced to ``simulated``.
    """
    await db.execute(
        """
        INSERT INTO writeback_events
            (run_id, domain, channel, provider, action, changed, prev_score,
             prev_tier, status_text, idempotency_key, created_at, delivery_status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'succeeded')
        ON CONFLICT(run_id, domain, channel) DO UPDATE SET
            provider = excluded.provider,
            action = excluded.action,
            changed = excluded.changed,
            prev_score = excluded.prev_score,
            prev_tier = excluded.prev_tier,
            status_text = excluded.status_text,
            created_at = excluded.created_at,
            delivery_status = excluded.delivery_status
        """,
        (
            run_id,
            domain,
            channel,
            provider,
            derive_action(channel, status_text, dry_run=dry_run),
            int(changed),
            prev_score,
            prev_tier,
            status_text,
            f"{domain}:{channel}",
            created_at,
        ),
    )


async def set_delivery_status(
    *,
    run_id: str,
    domain: str,
    channel: str,
    provider: str,
    delivery_status: str,
    created_at: str,
    dry_run: bool = False,
) -> None:
    """Record the delivery lifecycle around a Slack/outreach call (plan #6a).

    Called twice per real send: ``attempting`` **before** the call and
    ``succeeded``/``failed`` **after**. Recording the marker *before* the call closes the
    crash window — a process that dies mid-call leaves a dangling ``attempting`` row that
    :func:`idempotency_decision` will ``surface`` for a human, rather than silently
    re-firing on resume. Keyed on ``UNIQUE(run_id, domain, channel)`` so the second call
    updates the first row in place.

    For a dry-run the row is stamped ``simulated`` (it is inert to the guard, plan #3),
    so the lifecycle column is irrelevant; the action keeps it out of real-run logic.

    Args:
        run_id: The run this delivery belongs to.
        domain: The account's domain.
        channel: ``crm`` | ``slack`` | ``outreach``.
        provider: The concrete provider handling the channel.
        delivery_status: ``attempting`` | ``succeeded`` | ``failed``.
        created_at: ISO timestamp for the row.
        dry_run: When True, the row's action is ``simulated``.
    """
    action = SIMULATED if dry_run else "pending"
    await db.execute(
        """
        INSERT INTO writeback_events
            (run_id, domain, channel, provider, action, changed, prev_score,
             prev_tier, status_text, idempotency_key, created_at, delivery_status)
        VALUES (?, ?, ?, ?, ?, 0, NULL, NULL, NULL, ?, ?, ?)
        ON CONFLICT(run_id, domain, channel) DO UPDATE SET
            provider = excluded.provider,
            created_at = excluded.created_at,
            delivery_status = excluded.delivery_status
        """,
        (
            run_id,
            domain,
            channel,
            provider,
            action,
            f"{domain}:{channel}",
            created_at,
            delivery_status,
        ),
    )


async def idempotency_decision(*, run_id: str, domain: str, channel: str) -> str:
    """Decide whether to fire a write-back for ``(run_id, domain, channel)`` (plan #6a).

    Run-scoped: suppression applies only to retries *within the same run* (a resume reuses
    the same ``run_id``); a fresh future run with a new ``run_id`` always re-fires, so we
    never mute a domain's Slack/outreach forever. Dry-run rows are excluded two ways — the
    query JOINs ``runs`` and filters ``dry_run=0``, and ``simulated`` rows are ignored — so
    a persisted dry-run can never suppress, retry-gate, or surface a real send (plan #3).

    Returns:
        - ``"suppress"`` — a real ``succeeded`` row exists for this exact scope; skip it.
        - ``"surface"``  — a dangling ``attempting`` row (crash mid-call); a human must
          decide (never silently re-fired).
        - ``"fire"``     — no real row, or a ``failed`` row; send / retry normally.
    """
    row = await db.query_one(
        """
        SELECT we.delivery_status AS delivery_status
        FROM writeback_events we
        JOIN runs r ON r.run_id = we.run_id
        WHERE we.run_id = ? AND we.domain = ? AND we.channel = ?
          AND r.dry_run = 0
          AND we.action != ?
        """,
        (run_id, domain, channel, SIMULATED),
    )
    if row is None:
        return "fire"
    status = row["delivery_status"]
    if status == "succeeded":
        return "suppress"
    if status == "attempting":
        return "surface"
    # 'failed' (or any other non-terminal state) → retry.
    return "fire"
