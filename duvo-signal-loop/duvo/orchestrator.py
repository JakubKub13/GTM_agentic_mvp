"""Conductor: per-account run scouts → analyst → router, then render the report."""

import argparse
import asyncio
import csv
import json
from asyncio import Semaphore as _GlobalSemaphore
from datetime import UTC, datetime
from uuid import uuid4

from duvo import config, store
from duvo.agents.analyst import run_analyst
from duvo.agents.router import run_router
from duvo.agents.scouts import scout_all
from duvo.config import ACCOUNT_TIMEOUT_SECONDS, LOG_LEVEL, MAX_CONCURRENT_ACCOUNTS, TEST_EMAIL
from duvo.infra import events, http_client, tracing
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


# Plan #17: a process-wide global account semaphore bounding TOTAL concurrent accounts
# across ALL runs, on top of each run's per-run Semaphore(concurrency). Created lazily on
# the running event loop and keyed by that loop, so concurrent runs in one process share
# one cap while a fresh loop (e.g. each offline test) gets its own.
_global_sem: tuple[object, asyncio.Semaphore] | None = None


def _global_account_semaphore() -> asyncio.Semaphore:
    """Return the process-wide global account semaphore for the running loop (#17).

    Lazily constructs an :class:`asyncio.Semaphore` sized to
    :data:`config.DUVO_GLOBAL_MAX_ACCOUNTS` and caches it per event loop. A semaphore is
    bound to the loop it is created on, so caching by loop keeps concurrent runs sharing
    one cap without leaking a stale semaphore across loops.
    """
    global _global_sem
    loop = asyncio.get_running_loop()
    if _global_sem is None or _global_sem[0] is not loop:
        # Constructed via the directly-imported ``Semaphore`` (not ``asyncio.Semaphore``)
        # so it is distinct from the per-run semaphore at the construction site.
        _global_sem = (loop, _GlobalSemaphore(config.DUVO_GLOBAL_MAX_ACCOUNTS))
    return _global_sem[1]


def _reset_global_account_semaphore() -> None:
    """Drop the cached global semaphore so the next call rebuilds it (tests / reconfig)."""
    global _global_sem
    _global_sem = None


_EVENT_CHANNELS = ("crm", "slack", "outreach")

#: Side-effecting channels guarded by the run-scoped idempotency lifecycle (#6a). CRM is
#: omitted because it is dedup-safe via upsert-by-domain regardless of the guard.
_SIDE_EFFECT_CHANNELS = ("slack", "outreach")


def _provider_for(channel: str) -> str:
    """The concrete provider name recorded for *channel* in the ledger."""
    return {
        "crm": config.CRM_PROVIDER,
        "slack": "slack",
        "outreach": config.OUTREACH_PROVIDER,
    }[channel]


async def _writeback_decisions(run_id: str, domain: str, dry_run: bool) -> dict[str, str]:
    """Resolve the per-channel idempotency decision for the side-effecting channels (#6a).

    Run-scoped: only a retry *within the same run_id* (a resume) can suppress; a fresh run
    always re-fires. Dry-runs never consult the guard — they always ``fire`` (and are
    recorded as ``simulated``), so a persisted dry-run can never gate a real send (#3).

    Returns:
        Mapping ``channel`` → ``"fire"`` | ``"suppress"`` | ``"surface"``.
    """
    if dry_run:
        return {ch: "fire" for ch in _SIDE_EFFECT_CHANNELS}
    return {
        ch: await store.events.idempotency_decision(run_id=run_id, domain=domain, channel=ch)
        for ch in _SIDE_EFFECT_CHANNELS
    }


async def _persist_success(run_id, company, rr, *, dry_run: bool = False) -> None:
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
        agent_log_json = json.dumps(rr.agent_log, ensure_ascii=False)
    except Exception as exc:  # serialization must never abort a successful account
        log.warning(
            "persist: serializing result for %s failed (%s) — storing empty payloads",
            company.domain,
            exc,
        )
        signals_json, score_json, agent_log_json = "[]", "{}", "[]"

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
    # Plan #10: persist the per-account agent_log at account completion via the
    # agent_log_json column (added in P1a). Best-effort like the rest of the store.
    await store.db.execute(
        "UPDATE account_runs SET agent_log_json = ? WHERE run_id = ? AND domain = ?",
        (agent_log_json, run_id, company.domain),
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
            dry_run=dry_run,
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
    # Plan #9: start a fresh producer context for this task (run_id + account). The agent
    # layers enrich it with agent/beat; asyncio.gather copies the context per task so tags
    # stay isolated across interleaved accounts and runs.
    events.set_context(run_id=run_id, account=company.domain)
    # Plan #17: bound TOTAL concurrent accounts across ALL runs with the process-wide
    # global semaphore, on top of this run's per-run semaphore.
    async with _global_account_semaphore(), semaphore:
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
                # Plan #20: push the live status transition to the SSE feed. A no-op without
                # subscribers (CLI/offline), so it's safe to publish unconditionally.
                events.publish(run_id, events.make_account_event(status="running"))
                async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
                    agent_log: list[str] = []
                    signals = await scout_all(company, agent_log)
                    score = await run_analyst(company, signals, agent_log)
                    rr = RunResult(score=score, signals=signals)
                    fired = await _run_router_guarded(
                        rr, company, dry_run, test_email, run_id, persist, agent_log
                    )
                    rr.agent_log = agent_log
                    if persist:
                        await _persist_success(run_id, company, rr, dry_run=dry_run)
                        # Finalize the #6a lifecycle AFTER record_event so the terminal
                        # succeeded/failed reflects the actual send, not record_event's
                        # blanket 'succeeded'.
                        await _finalize_writeback_lifecycle(rr, company, dry_run, run_id, fired)
                    # Plan #20: push the terminal status + result fields to the live feed.
                    events.publish(
                        run_id,
                        events.make_account_event(
                            status="done",
                            score=score.score,
                            tier=score.tier,
                            confidence=score.confidence,
                            signals_count=len(signals),
                        ),
                    )
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
            except asyncio.CancelledError:
                # Plan #6: a cancelled account is surfaced as 'cancelled', never silently
                # marked done. Re-raise so the run-level handler can mark the run interrupted.
                if persist:
                    await store.mark_status(
                        run_id=run_id,
                        domain=company.domain,
                        status="cancelled",
                        finished_at=_now_iso(),
                        error="cancelled",
                    )
                events.publish(run_id, events.make_account_event(status="cancelled"))
                if root is not None:
                    root.update(level="WARNING", status_message="cancelled")
                log.warning("account %s cancelled", company.name)
                raise
            except Exception as exc:
                if persist:
                    await store.mark_status(
                        run_id=run_id,
                        domain=company.domain,
                        status="failed",
                        finished_at=_now_iso(),
                        error=str(exc),
                    )
                events.publish(run_id, events.make_account_event(status="failed"))
                if root is not None:
                    root.update(level="ERROR", status_message=str(exc))
                log.error("account %s failed: %s", company.name, exc)
                return None


async def _run_router_guarded(
    rr: RunResult,
    company: Company,
    dry_run: bool,
    test_email: str,
    run_id: str,
    persist: bool,
    agent_log: list[str],
) -> list[str]:
    """Drive ``run_router`` with the run-scoped write-back idempotency lifecycle (#6a).

    Wraps the routing agent in the delivery lifecycle so a resume (same ``run_id``) is safe:

    * If every side-effecting channel (Slack/outreach) is already ``succeeded`` for this
      ``(run_id, domain)`` — or is a dangling ``attempting`` to surface for a human — the
      router is **suppressed**: the agent never runs, so the side effects are not re-fired.
      CRM is dedup-safe via upsert-by-domain regardless.
    * Otherwise the router fires; ``attempting`` is recorded **before** the call (closing the
      crash window) per side-effecting channel that will fire. The terminal
      ``succeeded``/``failed`` is written later, by :func:`_finalize_writeback_lifecycle`,
      so it reflects the actual send rather than ``record_event``'s blanket ``succeeded``.

    The guard only runs when ``persist`` is on (it needs the ledger). With persistence off
    (CLI ``--dry-run``) the router is called directly, unchanged.

    Returns:
        The list of side-effecting channels that fired (empty if the router was suppressed),
        for :func:`_finalize_writeback_lifecycle` to close out.
    """
    if not persist:
        await run_router(rr, dry_run, test_email, agent_log)
        return []

    decisions = await _writeback_decisions(run_id, company.domain, dry_run)
    fire_channels = [ch for ch, d in decisions.items() if d == "fire"]
    if not fire_channels:
        # Every side-effecting channel is already succeeded or surfaced — skip the agent so a
        # same-run retry does not re-fire Slack/outreach (resume safety, plan #6/#6a).
        get_logger(__name__).info(
            "router suppressed for %s (idempotency: %s)", company.domain, decisions
        )
        return []

    now = _now_iso()
    for ch in fire_channels:
        await store.events.set_delivery_status(
            run_id=run_id,
            domain=company.domain,
            channel=ch,
            provider=_provider_for(ch),
            delivery_status="attempting",
            created_at=now,
            dry_run=dry_run,
        )

    # A crash/cancel mid-send leaves the 'attempting' markers in place, so
    # idempotency_decision() will surface them for a human rather than re-fire on resume.
    await run_router(rr, dry_run, test_email, agent_log)
    return fire_channels


async def _finalize_writeback_lifecycle(
    rr: RunResult,
    company: Company,
    dry_run: bool,
    run_id: str,
    fired_channels: list[str],
) -> None:
    """Write the terminal ``succeeded``/``failed`` delivery status per fired channel (#6a).

    Called after :func:`_persist_success` (whose ``record_event`` writes a blanket
    ``succeeded``) so this terminal lifecycle status — derived from the channel's actual
    status text — wins. A ``failed`` send stays retryable on a same-``run_id`` resume.
    """
    if not fired_channels:
        return
    status_by_channel = {"slack": rr.slack_status, "outreach": rr.outreach_status}
    done = _now_iso()
    for ch in fired_channels:
        action = store.events.derive_action(ch, status_by_channel[ch], dry_run=dry_run)
        delivery = "failed" if action == "failed" else "succeeded"
        await store.events.set_delivery_status(
            run_id=run_id,
            domain=company.domain,
            channel=ch,
            provider=_provider_for(ch),
            delivery_status=delivery,
            created_at=done,
            dry_run=dry_run,
        )


async def run(
    *,
    dry_run: bool,
    test_email: str,
    run_id: str | None = None,
    companies: list[Company] | None = None,
    triggered_by: str | None = None,
    persist: bool | None = None,
    manage_clients: bool = True,
    limit: int | None = None,
    log_level: str = LOG_LEVEL,
    concurrency: int | None = None,
    resume_run_id: str | None = None,
    skip_done_today: bool = False,
) -> None:
    """Orchestrate the full pipeline for all accounts concurrently and emit an HTML report.

    Generalized to be API-drivable (plan #1): callers supply ``run_id`` and ``companies``
    explicitly, choose attribution via ``triggered_by``, decouple ``persist`` from
    ``dry_run`` (#3), and own the shared client lifecycle via ``manage_clients`` (Option 2).
    The defaults reproduce the historical CLI behaviour so the offline suite stays green:
    ``companies=None`` falls back to :func:`load_companies`, ``persist=None`` falls back to
    ``not dry_run``, and ``manage_clients=True`` closes the shared clients in ``finally``.

    Accounts are processed with bounded concurrency governed by a semaphore
    (``concurrency`` argument or :data:`config.MAX_CONCURRENT_ACCOUNTS`). A failing account
    is isolated — it logs an error and yields ``None``; the rest of the batch continues. An
    ``asyncio.CancelledError`` is **not** swallowed: the account is marked ``cancelled``, the
    run ``interrupted``, and the cancellation re-raised (plan #6 — surface, don't auto-resume).

    Steps per account (inside :func:`_process_account`):
      1. scouts.scout_all       — gather raw signals
      2. analyst.run_analyst    — score against ICP
      3. router.run_router      — write-back to CRM / Slack / Outreach
      4. reporter.generate_report — render HTML audit log (sync, called once after gather)

    Args:
        dry_run:       When True, write-back tools simulate side-effects only.
        test_email:    Outreach recipient override (used in dry-run / test mode).
        run_id:        Explicit run id. ``run_id = resume_run_id or run_id or uuid4().hex``;
                       resume reuses the original id, the API supplies a fresh one (#1).
        companies:     The accounts to process. When None, falls back to
                       :func:`load_companies` (the CLI path). Resume callers reconstruct
                       these from ``runs.companies_json`` — never from repo-local CSV (#1).
        triggered_by:  Who launched the run (``cli`` | ``<user.email>``), persisted on the row.
        persist:       When True, persist run-state + write-back events. None → ``not dry_run``
                       (CLI). The server passes ``persist=True`` always so the UI is coherent
                       even for a dry-run (#3).
        manage_clients: When True, close the shared HTTP/Exa clients in ``finally``. The API
                       passes False so concurrent runs share an app-scoped pool (Option 2).
        limit:         If set, process only the first *limit* accounts.
        log_level:     Logging level string (e.g. ``"INFO"``, ``"DEBUG"``).
        concurrency:   Maximum accounts processed simultaneously.  Defaults to
                       :data:`config.MAX_CONCURRENT_ACCOUNTS` when ``None``.
        resume_run_id: If set, reuse this run_id and skip its already-done accounts (real runs).
        skip_done_today: If True, skip accounts already done for today's run_date (real runs).

    Raises:
        asyncio.CancelledError: Propagated after the run is marked ``interrupted`` (#6).
    """
    configure_logging(log_level)
    tracing.init_tracing()
    log = get_logger(__name__)

    run_id = resume_run_id or run_id or uuid4().hex
    run_date = datetime.now(UTC).date().isoformat()
    persist = (not dry_run) if persist is None else persist

    if companies is None:
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

    if persist:
        try:
            companies_json = json.dumps(
                [c.model_dump() for c in companies], ensure_ascii=False
            )
        except Exception:  # serialization must never abort the run
            companies_json = None
        await store.start_run(
            run_id=run_id,
            run_date=run_date,
            started_at=_now_iso(),
            dry_run=dry_run,
            concurrency=concurrency or MAX_CONCURRENT_ACCOUNTS,
            model=config.LLM_MODEL,
            app_env=config.APP_ENV,
            accounts_total=len(companies),
            triggered_by=triggered_by,
            companies_json=companies_json,
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
    interrupted = False

    try:
        # One batch-level trace per run: trace_context sets the session/tags/name
        # once, and the run span wraps the gather so every account-run nests under
        # it (asyncio.gather copies the current context into each task, so the run
        # span is the parent of each account-run). Propagating trace_name stamps the
        # run name onto every nested observation, so Langfuse's observation list
        # attributes each account-run to this one trace instead of leaving the Trace
        # Name column blank (which makes nested runs look like standalone traces).
        # _process_account returns None on a normal failure (isolated) but RE-RAISES
        # asyncio.CancelledError, so a cancellation propagates out of the gather (#6).
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
    except asyncio.CancelledError:
        # Plan #6: surface, don't auto-resume. Mark the run 'interrupted' (not 'done'),
        # then re-raise so the caller/task sees the cancellation. Resume is human-initiated.
        interrupted = True
        log.warning("run %s interrupted (cancelled)", run_id)
        if persist:
            await store.db.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE run_id = ?",
                (_now_iso(), run_id),
            )
        # Plan #20/#11: push the terminal run status so the SSE stream closes cleanly.
        events.publish(run_id, events.make_status_event(run_id=run_id, status="interrupted"))
        raise
    finally:
        if manage_clients:
            await http_client.aclose()
            await exa_tool.aclose()
        # Tracing is flushed per-run-completion, decoupled from client ownership (#7),
        # so observability isn't lost when manage_clients=False.
        tracing.flush()
        if persist and not interrupted:
            ok = sum(1 for r in raw_results if r is not None)
            await store.finish_run(
                run_id=run_id,
                finished_at=_now_iso(),
                succeeded=ok,
                failed=len(companies) - ok,
            )
            # Plan #20/#11: the terminal run status closes any live SSE stream for this run.
            events.publish(run_id, events.make_status_event(run_id=run_id, status="done"))

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
    # CLI path (plan #1): run_id = resume or fresh; load the repo-local CSV here; attribute
    # to "cli"; persist iff not a dry-run (CLI behaviour unchanged); own the client lifecycle.
    run_id = args.resume_run_id or uuid4().hex
    asyncio.run(
        run(
            run_id=run_id,
            companies=load_companies(),
            triggered_by="cli",
            dry_run=args.dry_run,
            test_email=args.test_email,
            persist=not args.dry_run,
            manage_clients=True,
            limit=args.limit,
            log_level=args.log_level,
            concurrency=args.concurrency,
            resume_run_id=args.resume_run_id,
            skip_done_today=args.skip_done_today,
        )
    )


if __name__ == "__main__":
    main()
