"""The ``runs`` table: one row per batch run."""

from duvo.store import db


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
) -> None:
    """Insert a run row in status ``running``. Idempotent (resume keeps the original).

    Args:
        run_id: Unique identifier for this batch run.
        run_date: ISO date string for the run (YYYY-MM-DD).
        started_at: ISO timestamp when the run was started.
        dry_run: Whether this run is a dry-run (no write-backs).
        concurrency: Semaphore concurrency limit used for this run.
        model: LLM model identifier used for this run.
        app_env: Application environment (e.g. ``dev``, ``prod``).
        accounts_total: Total number of accounts to process.
    """
    await db.execute(
        """
        INSERT INTO runs (run_id, run_date, started_at, dry_run, concurrency,
                          model, app_env, accounts_total, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
        ON CONFLICT(run_id) DO NOTHING
        """,
        (run_id, run_date, started_at, int(dry_run), concurrency, model, app_env, accounts_total),
    )


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
