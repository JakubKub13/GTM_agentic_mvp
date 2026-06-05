"""The ``account_runs`` table: one row per (run, account)."""

import sqlite3

from duvo.store import db


async def upsert_account_run(
    *,
    run_id: str,
    domain: str,
    company_name: str,
    country: str,
    status: str,
    started_at: str,
) -> None:
    """Insert (or reset on re-run) the account row in the given status."""
    await db.execute(
        """
        INSERT INTO account_runs (run_id, domain, company_name, country, status, started_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(run_id, domain) DO UPDATE SET
            company_name = excluded.company_name,
            country = excluded.country,
            status = excluded.status,
            started_at = excluded.started_at
        """,
        (run_id, domain, company_name, country, status, started_at),
    )


async def mark_status(
    *,
    run_id: str,
    domain: str,
    status: str,
    finished_at: str,
    score: int | None = None,
    tier: str | None = None,
    confidence: str | None = None,
    needs_human_research: bool | None = None,
    signals_count: int | None = None,
    signals_json: str | None = None,
    score_json: str | None = None,
    error: str | None = None,
) -> None:
    """Update an account row's terminal status + result fields."""
    nhr = None if needs_human_research is None else int(needs_human_research)
    await db.execute(
        """
        UPDATE account_runs SET
            status = ?, finished_at = ?, score = ?, tier = ?, confidence = ?,
            needs_human_research = ?, signals_count = ?, signals_json = ?,
            score_json = ?, error = ?
        WHERE run_id = ? AND domain = ?
        """,
        (
            status,
            finished_at,
            score,
            tier,
            confidence,
            nhr,
            signals_count,
            signals_json,
            score_json,
            error,
            run_id,
            domain,
        ),
    )


async def last_done_for_domain(domain: str, *, exclude_run_id: str) -> sqlite3.Row | None:
    """Return the most recent ``done`` row for *domain* from any OTHER run, else None."""
    return await db.query_one(
        """
        SELECT score, tier, confidence FROM account_runs
        WHERE domain = ? AND status = 'done' AND run_id != ?
        ORDER BY finished_at DESC
        LIMIT 1
        """,
        (domain, exclude_run_id),
    )


async def done_domains_for_run(run_id: str) -> set[str]:
    """Return the set of domains already ``done`` within *run_id* (for --resume)."""
    rows = await db.query_all(
        "SELECT domain FROM account_runs WHERE run_id = ? AND status = 'done'", (run_id,)
    )
    return {r["domain"] for r in rows}


async def done_domains_for_date(run_date: str) -> set[str]:
    """Return domains ``done`` on *run_date* across all runs (for --skip-done-today)."""
    rows = await db.query_all(
        """
        SELECT ar.domain FROM account_runs ar
        JOIN runs r ON ar.run_id = r.run_id
        WHERE r.run_date = ? AND ar.status = 'done'
        """,
        (run_date,),
    )
    return {r["domain"] for r in rows}
