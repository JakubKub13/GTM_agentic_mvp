"""Conductor: per-account run scouts → analyst → router, then render the report."""

import argparse
import asyncio
import csv
import json
from datetime import UTC, datetime
from uuid import uuid4

from duvo import config, store
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


def _now_iso() -> str:
    """UTC timestamp string for store rows."""
    return datetime.now(UTC).isoformat()


_EVENT_CHANNELS = ("crm", "slack", "outreach")


async def _persist_success(run_id, company, rr) -> None:
    """Mark the account done and record one write-back event per channel with the diff."""
    log = get_logger(__name__)
    s = rr.score
    prev = await store.last_done_for_domain(company.domain, exclude_run_id=run_id)
    changed = prev is None or prev["score"] != s.score or prev["tier"] != s.tier
    prev_score = prev["score"] if prev else None
    prev_tier = prev["tier"] if prev else None

    try:
        signals_json = json.dumps([sig.model_dump() for sig in rr.signals], ensure_ascii=False)
        score_json = s.model_dump_json()
    except Exception as exc:  # serialization must never abort a successful account
        log.warning(
            "persist: serializing result for %s failed (%s) — storing empty payloads",
            company.domain,
            exc,
        )
        signals_json, score_json = "[]", "{}"

    await store.mark_status(
        run_id=run_id,
        domain=company.domain,
        status="done",
        finished_at=_now_iso(),
        score=s.score,
        tier=s.tier,
        confidence=s.confidence,
        needs_human_research=s.needs_human_research,
        signals_count=len(rr.signals),
        signals_json=signals_json,
        score_json=score_json,
        error=None,
    )

    status_by_channel = {
        "crm": (rr.crm_status, config.CRM_PROVIDER),
        "slack": (rr.slack_status, "slack"),
        "outreach": (rr.outreach_status, config.OUTREACH_PROVIDER),
    }
    now = _now_iso()
    for channel in _EVENT_CHANNELS:
        status_text, provider = status_by_channel[channel]
        await store.record_event(
            run_id=run_id,
            domain=company.domain,
            channel=channel,
            provider=provider,
            status_text=status_text,
            changed=changed,
            prev_score=prev_score,
            prev_tier=prev_tier,
            created_at=now,
        )


async def _process_account(
    company: Company,
    dry_run: bool,
    test_email: str,
    semaphore: asyncio.Semaphore,
    run_id: str,
    run_date: str,
    persist: bool,
) -> RunResult | None:
    """Run the full pipeline for a single account inside a semaphore slot.

    Each account is fully isolated: any exception is logged and ``None`` is
    returned so that a single bad account does not abort the whole batch. The
    account-run span nests under the batch-level run span (see :func:`run`), so
    the whole batch renders as one Langfuse trace.

    Args:
        company:    The account to process.
        dry_run:    When True, write-back tools simulate side-effects only.
        test_email: Outreach recipient override used in dry-run / test mode.
        semaphore:  Bounds the number of accounts processed concurrently.
        run_id:     Batch run id (also the Langfuse session), recorded in span metadata.
        run_date:   ISO date of the run, recorded in span metadata.
        persist:    When True, persist run-state rows + write-back events to the store.

    Returns:
        A populated :class:`~models.RunResult` on success, or ``None`` on error.
    """
    log = get_logger(__name__)
    async with semaphore:
        with tracing.span(
            name="🎯 account-run",
            as_type="chain",
            input={"company": company.name, "domain": company.domain},
            metadata={
                "domain": company.domain,
                "country": company.country,
                "run_date": run_date,
                "batch_run_id": run_id,
                "dry_run": dry_run,
            },
        ) as root:
            try:
                if persist:
                    await store.upsert_account_run(
                        run_id=run_id,
                        domain=company.domain,
                        company_name=company.name,
                        country=company.country,
                        status="running",
                        started_at=_now_iso(),
                    )
                async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
                    agent_log: list[str] = []
                    signals = await scout_all(company, agent_log)
                    score = await run_analyst(company, signals, agent_log)
                    rr = RunResult(score=score, signals=signals)
                    await run_router(rr, dry_run, test_email, agent_log)
                    rr.agent_log = agent_log
                    if persist:
                        await _persist_success(run_id, company, rr)
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
                if persist:
                    await store.mark_status(
                        run_id=run_id,
                        domain=company.domain,
                        status="failed",
                        finished_at=_now_iso(),
                        error=str(exc),
                    )
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
    resume_run_id: str | None = None,
    skip_done_today: bool = False,
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
        dry_run:       When True, write-back tools simulate side-effects only.
        test_email:    Outreach recipient override (used in dry-run / test mode).
        limit:         If set, process only the first *limit* accounts.
        log_level:     Logging level string (e.g. ``"INFO"``, ``"DEBUG"``).
        concurrency:   Maximum accounts processed simultaneously.  Defaults to
                       :data:`config.MAX_CONCURRENT_ACCOUNTS` when ``None``.
        resume_run_id: If set, reuse this run_id and skip its already-done accounts (real runs only).
        skip_done_today: If True, skip accounts already done for today's run_date (real runs only).
    """
    configure_logging(log_level)
    tracing.init_tracing()
    log = get_logger(__name__)

    run_id = resume_run_id or uuid4().hex
    run_date = datetime.now(UTC).date().isoformat()

    companies = load_companies()
    if limit is not None:
        companies = companies[:limit]

    if not dry_run and (resume_run_id or skip_done_today):
        skip: set[str] = set()
        if resume_run_id:
            skip |= await store.done_domains_for_run(resume_run_id)
        if skip_done_today:
            skip |= await store.done_domains_for_date(run_date)
        before = len(companies)
        companies = [c for c in companies if c.domain not in skip]
        log.info("resume: skipping %d already-done account(s)", before - len(companies))

    persist = not dry_run
    if persist:
        await store.start_run(
            run_id=run_id,
            run_date=run_date,
            started_at=_now_iso(),
            dry_run=dry_run,
            concurrency=concurrency or MAX_CONCURRENT_ACCOUNTS,
            model=config.LLM_MODEL,
            app_env=config.APP_ENV,
            accounts_total=len(companies),
        )

    sem = asyncio.Semaphore(concurrency or MAX_CONCURRENT_ACCOUNTS)

    log.info(
        "Starting pipeline: %d accounts | dry_run=%s | concurrency=%d",
        len(companies),
        dry_run,
        concurrency or MAX_CONCURRENT_ACCOUNTS,
    )

    batch_tags = ["duvo-signal-loop", f"model:{config.LLM_MODEL}", f"env:{config.APP_ENV}"]
    if dry_run:
        batch_tags.append("dry-run")

    run_name = f"🚀 run {run_id[:8]} · {run_date}"

    raw_results: list = []

    try:
        # One batch-level trace per run: trace_context sets the session/tags/name
        # once, and the run span wraps the gather so every account-run nests under
        # it (asyncio.gather copies the current context into each task, so the run
        # span is the parent of each account-run). Propagating trace_name stamps the
        # run name onto every nested observation, so Langfuse's observation list
        # attributes each account-run to this one trace instead of leaving the Trace
        # Name column blank (which makes nested runs look like standalone traces).
        # _process_account never raises (it catches + returns None), so a bare gather
        # is safe.
        with tracing.trace_context(
            session_id=run_id,
            tags=batch_tags,
            metadata={
                "batch_run_id": run_id,
                "run_date": run_date,
                "dry_run": dry_run,
                "accounts": len(companies),
            },
            trace_name=run_name,
        ):
            with tracing.span(
                name=run_name,
                as_type="chain",
                input={"accounts": len(companies), "dry_run": dry_run},
            ) as batch_root:
                raw_results = await asyncio.gather(
                    *[
                        _process_account(c, dry_run, test_email, sem, run_id, run_date, persist)
                        for c in companies
                    ]
                )
                if batch_root is not None:
                    succeeded = [r for r in raw_results if r is not None]
                    batch_root.update(
                        output={
                            "accounts": len(companies),
                            "succeeded": len(succeeded),
                            "failed": len(companies) - len(succeeded),
                        }
                    )
    finally:
        await http_client.aclose()
        await exa_tool.aclose()
        tracing.flush()
        if not dry_run:
            ok = sum(1 for r in raw_results if r is not None)
            await store.finish_run(
                run_id=run_id,
                finished_at=_now_iso(),
                succeeded=ok,
                failed=len(companies) - ok,
            )

    results: list[RunResult] = [r for r in raw_results if r is not None]
    path = generate_report(results, run_id=run_id, run_date=run_date)
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
    ap.add_argument(
        "--resume",
        dest="resume_run_id",
        default=None,
        help="resume an existing run_id: skip its already-done accounts (real runs only)",
    )
    ap.add_argument(
        "--skip-done-today",
        action="store_true",
        help="skip accounts already marked done for today's run_date (cron convenience)",
    )
    args = ap.parse_args()
    asyncio.run(
        run(
            dry_run=args.dry_run,
            test_email=args.test_email,
            limit=args.limit,
            log_level=args.log_level,
            concurrency=args.concurrency,
            resume_run_id=args.resume_run_id,
            skip_done_today=args.skip_done_today,
        )
    )


if __name__ == "__main__":
    main()
