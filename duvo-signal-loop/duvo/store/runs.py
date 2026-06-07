"""The ``runs`` table: one row per batch run."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from duvo.store import db

_RUNS_INSERT = """
    INSERT INTO runs (run_id, run_date, started_at, dry_run, concurrency,
                      model, app_env, accounts_total, status,
                      triggered_by, companies_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
"""


async def start_run(
    *,
    run_id: str,
    run_date: str,
    started_at: str,
    dry_run: bool,
    concurrency: int,
    model: str,
    app_env: str,
    accounts_total: int,
    triggered_by: str | None = None,
    companies_json: str | None = None,
) -> None:
    """Insert a run row in status ``running``. Idempotent (resume keeps the original).

    The single writer of the ``runs`` row (plan #2): the API does not pre-insert a
    partial row. The ``ON CONFLICT DO NOTHING`` is intentional for resume — the strict
    variant :func:`start_run_strict` raises on conflict instead.

    Args:
        run_id: Unique identifier for this batch run.
        run_date: ISO date string for the run (YYYY-MM-DD).
        started_at: ISO timestamp when the run was started.
        dry_run: Whether this run is a dry-run (no write-backs).
        concurrency: Semaphore concurrency limit used for this run.
        model: LLM model identifier used for this run.
        app_env: Application environment (e.g. ``dev``, ``prod``).
        accounts_total: Total number of accounts to process.
        triggered_by: Who launched the run (``cli`` | ``<user.email>``), or None.
        companies_json: Full JSON of the run's input companies, for faithful resume.
    """
    await db.execute(
        _RUNS_INSERT + "ON CONFLICT(run_id) DO NOTHING",
        (
            run_id,
            run_date,
            started_at,
            int(dry_run),
            concurrency,
            model,
            app_env,
            accounts_total,
            triggered_by,
            companies_json,
        ),
    )


async def start_run_strict(
    *,
    run_id: str,
    run_date: str,
    started_at: str,
    dry_run: bool,
    concurrency: int,
    model: str,
    app_env: str,
    triggered_by: str | None,
    companies_json: str | None,
    accounts: list[Mapping[str, str]],
) -> None:
    """Create-run preflight: the ``runs`` row + all ``pending`` ``account_runs`` in ONE tx.

    Strict counterpart of :func:`start_run` for the API path (plan #4, #5). It does **not**
    swallow errors: the whole transaction (run row + every account row) commits together
    or rolls back together, and any failure propagates so ``POST /runs`` can return 5xx
    rather than a phantom ``201`` for an unrecorded run or one with a blank account
    snapshot. There is **no** ``ON CONFLICT DO NOTHING`` here — a duplicate ``run_id``
    raises (the API generates a fresh ``run_id``, so a conflict is a real error).

    ``accounts_total`` is derived from ``accounts`` (the single source of truth for the
    snapshot), so the seeded ``pending`` rows and the run's total can never disagree.

    Args:
        run_id: Unique identifier for this batch run (must not already exist).
        run_date: ISO date string for the run (YYYY-MM-DD).
        started_at: ISO timestamp when the run was started.
        dry_run: Whether this run is a dry-run (no write-backs).
        concurrency: Semaphore concurrency limit used for this run.
        model: LLM model identifier used for this run.
        app_env: Application environment (e.g. ``dev``, ``prod``).
        triggered_by: Who launched the run (``cli`` | ``<user.email>``), or None.
        companies_json: Full JSON of the run's input companies, for faithful resume.
        accounts: One mapping per account to pre-seed, each with ``domain``,
            ``company_name`` and ``country``.

    Raises:
        Exception: On any DB error (e.g. a ``run_id`` conflict); the transaction is
            rolled back, leaving neither a run row nor any account rows.
    """
    accounts_total = len(accounts)

    def _work(conn: sqlite3.Connection) -> None:
        conn.execute(
            _RUNS_INSERT,
            (
                run_id,
                run_date,
                started_at,
                int(dry_run),
                concurrency,
                model,
                app_env,
                accounts_total,
                triggered_by,
                companies_json,
            ),
        )
        conn.executemany(
            """
            INSERT INTO account_runs
                (run_id, domain, company_name, country, status, started_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            [
                (
                    run_id,
                    acct["domain"],
                    acct.get("company_name"),
                    acct.get("country"),
                    started_at,
                )
                for acct in accounts
            ],
        )

    await db.execute_tx(_work)


async def sweep_interrupted_runs() -> list[str]:
    """Mark every stale ``running`` run as ``interrupted`` — the startup sweep (plan #6).

    A long-lived API process can crash mid-run, leaving ``runs`` rows stuck in
    ``running`` with no live task behind them. On the next startup the lifespan calls
    this to flip those rows to ``interrupted`` so the UI can surface them. Resume is
    **human-initiated, not automatic** (#6): a crash *after* Slack/outreach fired but
    *before* the account was marked ``done`` would otherwise re-fire side effects.

    Terminal runs (``done`` / ``failed`` / ``cancelled`` / already ``interrupted``) are
    left untouched.

    Returns:
        The ``run_id``s that were swept from ``running`` to ``interrupted`` (possibly
        empty).
    """
    stale = await db.query_all("SELECT run_id FROM runs WHERE status = 'running'")
    if not stale:
        return []
    await db.execute("UPDATE runs SET status = 'interrupted' WHERE status = 'running'")
    return [row["run_id"] for row in stale]


async def finish_run(*, run_id: str, finished_at: str, succeeded: int, failed: int) -> None:
    """Mark the run done and record success/failure counts.

    Args:
        run_id: Unique identifier for the run to close.
        finished_at: ISO timestamp when the run completed.
        succeeded: Number of accounts that completed successfully.
        failed: Number of accounts that failed.
    """
    await db.execute(
        """
        UPDATE runs
        SET finished_at = ?, accounts_succeeded = ?, accounts_failed = ?, status = 'done'
        WHERE run_id = ?
        """,
        (finished_at, succeeded, failed, run_id),
    )
