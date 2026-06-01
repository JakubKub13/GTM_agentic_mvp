"""Conductor: per-account run scouts → analyst → router, then render the report."""
import argparse
import csv
import time

from config import TEST_EMAIL, LOG_LEVEL
from logging_setup import configure_logging, get_logger
from models import Company, RunResult
from scouts import scout_all
from analyst import run_analyst
from router import run_router
from reporter import generate_report


def load_companies(path: str = "companies.csv") -> list[Company]:
    """Parse *path* (CSV with columns name, domain, country, description) into Company objects.

    Args:
        path: Path to the CSV file.  Defaults to ``companies.csv`` in the cwd.

    Returns:
        List of :class:`~models.Company` instances, one per CSV row.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        return [Company(**row) for row in csv.DictReader(fh)]


def run(
    dry_run: bool,
    test_email: str,
    limit: int | None,
    log_level: str = LOG_LEVEL,
) -> None:
    """Orchestrate the full pipeline for all accounts and emit an HTML report.

    Steps for each account:
      1. scouts.scout_all       — gather raw signals
      2. analyst.run_analyst    — score against ICP
      3. router.run_router      — write-back to CRM / Slack / Outreach
      4. reporter.generate_report — render HTML audit log

    Args:
        dry_run:   When True, write-back tools simulate side-effects only.
        test_email: Outreach recipient override (used in dry-run / test mode).
        limit:     If set, process only the first *limit* accounts.
        log_level: Logging level string (e.g. ``"INFO"``, ``"DEBUG"``).
    """
    configure_logging(log_level)
    # get_logger is called here (inside run) so configure_logging runs first.
    log = get_logger(__name__)

    companies = load_companies()
    if limit is not None:
        companies = companies[:limit]

    log.info(
        "Starting pipeline: %d accounts | dry_run=%s",
        len(companies),
        dry_run,
    )

    results: list[RunResult] = []
    for c in companies:
        log.info("-> %s", c.name)
        agent_log: list[str] = []

        signals = scout_all(c, agent_log)
        score = run_analyst(c, signals, agent_log)
        # router mutates rr's statuses in place; attach the full agent_log afterward.
        rr = RunResult(score=score, signals=signals)
        run_router(rr, dry_run, test_email, agent_log)
        rr.agent_log = agent_log
        results.append(rr)

        log.info(
            "%d/10 %s conf=%s human=%s (%d signals, %d tool calls)",
            score.score,
            score.tier,
            score.confidence,
            score.needs_human_research,
            len(signals),
            len(agent_log),
        )
        time.sleep(0.3)

    path = generate_report(results)
    log.info("Done. Open %s", path)
    print(f"Report: {path}")


if __name__ == "__main__":
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
    args = ap.parse_args()
    run(
        dry_run=args.dry_run,
        test_email=args.test_email,
        limit=args.limit,
        log_level=args.log_level,
    )
