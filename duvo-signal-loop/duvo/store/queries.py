"""Read-side query helpers for the run-state store (plan #12).

The data layer behind the three read endpoints:

* :func:`list_runs` — ``GET /runs`` history, newest first.
* :func:`get_run` — ``GET /runs/{id}``: the ``runs`` row plus its ``account_runs``
  snapshot.
* :func:`get_account_detail` — ``GET /runs/{id}/accounts/{domain}``: the account row
  (``score_json``, ``signals_json``, ``agent_log_json``) plus the score/tier diff against
  the most recent *real* prior run — the same baseline ``writeback_events`` records as
  ``changed`` / ``prev_score`` / ``prev_tier`` (see ``store.account_runs.last_done_for_domain``
  and ``store.events.record_event``).

All reads route through :func:`db.query_one` / :func:`db.query_all`, so a persistence
failure degrades to ``None`` / ``[]`` rather than aborting the caller (house rule).
"""

from __future__ import annotations

import json
from typing import Any

from duvo.infra.logging_setup import get_logger
from duvo.models import Company
from duvo.store import db

_log = get_logger(__name__)


async def list_runs(*, limit: int = 100) -> list[dict[str, Any]]:
    """Return the run history, newest first (plan #12, ``GET /runs``).

    Args:
        limit: Maximum number of runs to return (most recent by ``started_at``).

    Returns:
        A list of run rows (as dicts) ordered by ``started_at`` descending, or ``[]``.
    """
    rows = await db.query_all(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]


async def companies_for_run(run_id: str) -> list[Company]:
    """Reconstruct a run's input companies from its stored ``companies_json`` (plan #1).

    Resume rebuilds the company list from the exact input the run was launched with —
    persisted on the ``runs`` row — **never** from repo-local ``companies.csv``, so an API
    run launched from an uploaded CSV is faithfully recoverable (incl. ``description``).

    Reconstruction never raises: a missing run, a NULL/empty ``companies_json`` (legacy
    rows), or malformed JSON all degrade to ``[]`` so the caller can fall back.

    Args:
        run_id: The run whose input companies to reconstruct.

    Returns:
        The list of :class:`~duvo.models.Company` the run was launched with, or ``[]``.
    """
    row = await db.query_one("SELECT companies_json FROM runs WHERE run_id = ?", (run_id,))
    if row is None or not row["companies_json"]:
        return []
    try:
        return [Company(**c) for c in json.loads(row["companies_json"])]
    except Exception as exc:  # malformed stored input must never abort a resume
        _log.warning("companies_for_run(%s): could not parse companies_json (%s)", run_id, exc)
        return []


async def get_run(run_id: str) -> dict[str, Any] | None:
    """Return a run plus its ``account_runs`` snapshot (plan #12, ``GET /runs/{id}``).

    Args:
        run_id: The run to fetch.

    Returns:
        ``{"run": <run row>, "accounts": [<account row>, ...]}`` with accounts ordered by
        domain, or ``None`` if the run does not exist.
    """
    run_row = await db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    if run_row is None:
        return None
    account_rows = await db.query_all(
        "SELECT * FROM account_runs WHERE run_id = ? ORDER BY domain",
        (run_id,),
    )
    return {"run": dict(run_row), "accounts": [dict(r) for r in account_rows]}


async def get_account_detail(run_id: str, domain: str) -> dict[str, Any] | None:
    """Return an account's full row plus its score/tier diff (plan #12, drill-down).

    The diff baseline is the most recent *real* (``dry_run = 0``) ``done`` row for
    *domain* from any **other** run — the identical baseline ``writeback_events`` uses (see
    ``account_runs.last_done_for_domain``), so the drill-down's ``DiffBadge`` matches the
    ledger's ``changed`` / ``prev_score`` / ``prev_tier``. A persisted dry-run can never be
    the baseline (plan #3).

    Args:
        run_id: The run the account belongs to.
        domain: The account's domain.

    Returns:
        ``{"account": <account row incl. score_json/signals_json/agent_log_json>,
        "diff": {"changed": bool, "prev_score": int | None, "prev_tier": str | None}}``,
        or ``None`` if the account row does not exist.
    """
    account_row = await db.query_one(
        "SELECT * FROM account_runs WHERE run_id = ? AND domain = ?",
        (run_id, domain),
    )
    if account_row is None:
        return None

    prior = await db.query_one(
        """
        SELECT ar.score, ar.tier FROM account_runs ar
        JOIN runs r ON ar.run_id = r.run_id
        WHERE ar.domain = ? AND ar.status = 'done' AND ar.run_id != ? AND r.dry_run = 0
        ORDER BY ar.finished_at DESC
        LIMIT 1
        """,
        (domain, run_id),
    )

    if prior is None:
        diff = {"changed": False, "prev_score": None, "prev_tier": None}
    else:
        prev_score = prior["score"]
        prev_tier = prior["tier"]
        changed = account_row["score"] != prev_score or account_row["tier"] != prev_tier
        diff = {"changed": bool(changed), "prev_score": prev_score, "prev_tier": prev_tier}

    return {"account": dict(account_row), "diff": diff}
