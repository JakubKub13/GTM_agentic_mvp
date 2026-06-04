"""Conductor: per-account run scouts → analyst → router, then render the report."""

import argparse
import asyncio
import csv
from datetime import UTC, datetime
from uuid import uuid4

from duvo import config
from duvo.agents.analyst import run_analyst
from duvo.agents.router import run_router
from duvo.agents.scouts import scout_all
from duvo.config import ACCOUNT_TIMEOUT_SECONDS, LOG_LEVEL, MAX_CONCURRENT_ACCOUNTS, TEST_EMAIL
from duvo.infra import http_client, tracing
from duvo.infra.logging_setup import configure_logging, get_logger
from duvo.models import Company, RunResult
from duvo.reporting.reporter import generate_report
from duvo.shared_agentic_tools import exa_tool


def load_companies(path: str = "companies.csv") -> list[Company]:
    """Parse *path* (CSV with columns name, domain, country, description) into Company objects.

    Args:
        path: Path to the CSV file.  Defaults to ``companies.csv`` in the cwd.

    Returns:
        List of :class:`~models.Company` instances, one per CSV row.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        return [Company(**row) for row in csv.DictReader(fh)]


async def _process_account(
    company: Company,
    dry_run: bool,
    test_email: str,
    semaphore: asyncio.Semaphore,
    run_id: str,
    run_date: str,
) -> RunResult | None:
    """Run the full pipeline for a single account inside a semaphore slot.

    Each account is fully isolated: any exception is logged and ``None`` is
    returned so that a single bad account does not abort the whole batch. The
    account is the root of one Langfuse trace (grouped into the batch session).

    Args:
        company:    The account to process.
        dry_run:    When True, write-back tools simulate side-effects only.
        test_email: Outreach recipient override used in dry-run / test mode.
        semaphore:  Bounds the number of accounts processed concurrently.
        run_id:     Batch run id — the Langfuse session all accounts share.
        run_date:   ISO date of the run, recorded in trace metadata.

    Returns:
        A populated :class:`~models.RunResult` on success, or ``None`` on error.
    """
    log = get_logger(__name__)
    async with semaphore:
        tags = ["duvo-signal-loop", f"model:{config.LLM_MODEL}", f"env:{config.APP_ENV}"]
        if dry_run:
            tags.append("dry-run")
        metadata = {
            "domain": company.domain,
            "country": company.country,
            "run_date": run_date,
            "batch_run_id": run_id,
            "dry_run": dry_run,
        }
        with tracing.trace_context(session_id=run_id, tags=tags, metadata=metadata):
            with tracing.span(
                name="🎯 account-run",
                as_type="chain",
                input={"company": company.name, "domain": company.domain},
            ) as root:
                try:
                    async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
                        agent_log: list[str] = []
                        signals = await scout_all(company, agent_log)
                        score = await run_analyst(company, signals, agent_log)
                        rr = RunResult(score=score, signals=signals)
                        await run_router(rr, dry_run, test_email, agent_log)
                        rr.agent_log = agent_log
                        if root is not None:
                            root.update(
                                output={
                                    "score": score.score,
                                    "tier": score.tier,
                                    "confidence": score.confidence,
                                }
                            )
                        log.info(
                            "%d/10 %s conf=%s human=%s (%d signals, %d tool calls)",
                            score.score,
                            score.tier,
                            score.confidence,
                            score.needs_human_research,
                            len(signals),
                            len(agent_log),
                        )
                        return rr
                except Exception as exc:
                    if root is not None:
                        root.update(level="ERROR", status_message=str(exc))
                    log.error("account %s failed: %s", company.name, exc)
                    return None


async def run(
    dry_run: bool,
    test_email: str,
    limit: int | None,
    log_level: str = LOG_LEVEL,
    concurrency: int | None = None,
) -> None:
    """Orchestrate the full pipeline for all accounts concurrently and emit an HTML report.

    Accounts are processed with bounded concurrency governed by a semaphore
    (``concurrency`` argument or :data:`config.MAX_CONCURRENT_ACCOUNTS`).  A
    failing account is isolated — it logs an error and yields ``None``; the
    rest of the batch continues unaffected.

    Steps per account (inside :func:`_process_account`):
      1. scouts.scout_all       — gather raw signals
      2. analyst.run_analyst    — score against ICP
      3. router.run_router      — write-back to CRM / Slack / Outreach
      4. reporter.generate_report — render HTML audit log (sync, called once after gather)

    The shared async HTTP client is always closed via ``http_client.aclose()``
    in a ``finally`` block — even when one or more accounts fail.

    Args:
        dry_run:     When True, write-back tools simulate side-effects only.
        test_email:  Outreach recipient override (used in dry-run / test mode).
        limit:       If set, process only the first *limit* accounts.
        log_level:   Logging level string (e.g. ``"INFO"``, ``"DEBUG"``).
        concurrency: Maximum accounts processed simultaneously.  Defaults to
                     :data:`config.MAX_CONCURRENT_ACCOUNTS` when ``None``.
    """
    configure_logging(log_level)
    tracing.init_tracing()
    log = get_logger(__name__)

    run_id = uuid4().hex
    run_date = datetime.now(UTC).date().isoformat()

    companies = load_companies()
    if limit is not None:
        companies = companies[:limit]

    sem = asyncio.Semaphore(concurrency or MAX_CONCURRENT_ACCOUNTS)

    log.info(
        "Starting pipeline: %d accounts | dry_run=%s | concurrency=%d",
        len(companies),
        dry_run,
        concurrency or MAX_CONCURRENT_ACCOUNTS,
    )

    try:
        # _process_account never raises (it catches + returns None), so a bare gather is safe;
        # if that changes, add return_exceptions=True to avoid cancelling siblings.
        raw_results = await asyncio.gather(
            *[_process_account(c, dry_run, test_email, sem, run_id, run_date) for c in companies]
        )
    finally:
        await http_client.aclose()
        await exa_tool.aclose()
        tracing.flush()

    results: list[RunResult] = [r for r in raw_results if r is not None]
    path = generate_report(results)
    log.info("Done. Open %s", path)
    print(f"Report: {path}")


def main() -> None:
    """Parse CLI arguments and run the pipeline.

    This is the console entry point; the repo-root ``main.py`` shim and
    ``python -m duvo.orchestrator`` both delegate here.
    """
    ap = argparse.ArgumentParser(description="duvo-signal-loop orchestrator")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="agents run; write-back tools simulate side-effects",
    )
    ap.add_argument(
        "--test-email",
        default=TEST_EMAIL,
        help="outreach test lead email (default: TEST_EMAIL from .env)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="process only the first N accounts",
    )
    ap.add_argument(
        "--log-level",
        default=LOG_LEVEL,
        help="logging level (DEBUG, INFO, WARNING, ERROR); default from LOG_LEVEL in .env",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=(
            "maximum accounts processed simultaneously "
            "(default: MAX_CONCURRENT_ACCOUNTS from .env, currently %(default)s)"
        ),
    )
    args = ap.parse_args()
    asyncio.run(
        run(
            dry_run=args.dry_run,
            test_email=args.test_email,
            limit=args.limit,
            log_level=args.log_level,
            concurrency=args.concurrency,
        )
    )


if __name__ == "__main__":
    main()
