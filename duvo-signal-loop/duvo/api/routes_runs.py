"""Run endpoints — launch, read, and live-stream batch runs (plan #8, #10, #11, #12).

One ``APIRouter`` for everything a run needs over HTTP:

* ``POST /runs`` (#8): authenticate → authorize (the #14 real-run gate) → validate the
  uploaded CSV (columns + row cap) → generate a fresh ``run_id`` → ``start_run_strict()``
  (the #4 preflight that commits the run row + all ``pending`` accounts in one transaction)
  → ``asyncio.create_task(run(...))`` registered in :mod:`duvo.api.jobs` → ``201 {run_id}``.
  If the preflight raises, the task is **never** launched and **nothing** is persisted, so a
  failure is a clean ``5xx`` rather than a phantom ``201`` for an unrecorded run (#4).
* ``GET /runs`` / ``GET /runs/{id}`` / ``GET /runs/{id}/accounts/{domain}`` (#12): read the
  persisted snapshot + the account drill-down + score/tier diff, via
  :mod:`duvo.store.queries`.
* ``GET /runs/{id}/stream`` (#10, #11): ``text/event-stream`` via ``sse-starlette``. Register
  a bounded subscriber queue on the in-process event bus, **flush the DB snapshot** (account
  status + completed accounts' ``agent_log_json`` — never an in-flight account's earlier tool
  lines, #10), then stream ``account`` / ``tool`` / ``status`` events live until the run reaches
  a terminal status, then close. Heartbeats + ``Cache-Control: no-cache`` + disabled proxy
  buffering keep the stream alive through proxies (#11).

Auth is enforced via the :mod:`duvo.api.auth` dependencies; the orchestrator's ``run`` is
called with the API-path arguments (``persist=True`` always, ``manage_clients=False``, the
caller's email as ``triggered_by``) per plan #1/#3.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sse_starlette.sse import EventSourceResponse

from duvo import config
from duvo.api import auth, jobs
from duvo.api.auth import AuthorizationError, User
from duvo.infra import events
from duvo.infra.logging_setup import get_logger
from duvo.models import Company
from duvo.orchestrator import run
from duvo.store import db, queries, runs

_log = get_logger(__name__)

# Statuses at which the SSE stream stops awaiting live events and closes (#11). Mirrors the
# terminal set the run lifecycle records (done + the interrupt/cancel/fail outcomes).
_TERMINAL_STATUSES = frozenset({"done", "interrupted", "cancelled", "failed"})

# CSV upload guardrails (plan "Risks": validate size/columns, cap rows before parsing).
MAX_CSV_BYTES = 5 * 1024 * 1024  # 5 MiB ceiling on the uploaded body
MAX_CSV_ROWS = 5000  # row cap before building Company objects
_REQUIRED_CSV_COLUMNS = frozenset({"name", "domain"})

# SSE heartbeat interval (seconds) — comment pings that keep proxies from closing an idle
# connection (#11). sse-starlette emits these via the `ping` argument.
_HEARTBEAT_SECONDS = 15

router = APIRouter()


# --------------------------------------------------------------------------- #
# CSV parsing + validation (#8 / "Risks": CSV upload trust)
# --------------------------------------------------------------------------- #
def _parse_companies(raw: bytes) -> list[Company]:
    """Validate and parse uploaded CSV *raw* bytes into :class:`Company` objects.

    Enforces the upload-trust guardrails before any parsing into the domain model:
    a size ceiling, the required columns (``name``, ``domain``), a non-empty body, and a
    row cap — so a malformed or oversized upload is rejected as a ``400`` rather than
    fanning out an unbounded run.

    Args:
        raw: The uploaded file body.

    Returns:
        One :class:`Company` per CSV data row.

    Raises:
        HTTPException: 400 when the CSV is too large, empty, missing required columns,
            exceeds the row cap, or contains a row that cannot be coerced to a Company.
    """
    if len(raw) > MAX_CSV_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV exceeds the {MAX_CSV_BYTES}-byte upload limit",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="CSV is not valid UTF-8"
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    columns = {c.strip() for c in (reader.fieldnames or [])}
    missing = _REQUIRED_CSV_COLUMNS - columns
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV missing required column(s): {', '.join(sorted(missing))}",
        )

    companies: list[Company] = []
    for i, row in enumerate(reader):
        if i >= MAX_CSV_ROWS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"CSV exceeds the {MAX_CSV_ROWS}-row cap",
            )
        # Drop None keys (ragged rows) and empty cells so Company defaults apply.
        clean = {k: v for k, v in row.items() if k and v is not None}
        try:
            companies.append(Company(**clean))
        except Exception as exc:  # malformed row → 400, never a 500
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"CSV row {i + 1} is invalid: {exc}",
            ) from exc

    if not companies:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="CSV contains no account rows"
        )
    return companies


def _now_iso() -> str:
    """UTC timestamp string for store rows."""
    return datetime.now(UTC).isoformat()


# --------------------------------------------------------------------------- #
# POST /runs (#8)
# --------------------------------------------------------------------------- #
@router.post("/runs", status_code=status.HTTP_201_CREATED)
async def create_run(
    file: UploadFile | None = None,
    dry_run: bool = Form(default=True),
    confirm: bool = Form(default=False),
    concurrency: int | None = Form(default=None),
    skip_done_today: bool = Form(default=False),
    test_email: str = Form(default=""),
    user: User = Depends(auth.get_current_user),
    _csrf: None = Depends(auth.require_csrf),
) -> dict[str, str]:
    """Launch a batch run and return its ``run_id`` (plan #8).

    Flow: authenticate (the ``user`` dependency) → for a real (non-dry) run, pass the #14
    authorization gate (allowlisted role + explicit ``confirm``) → validate/parse the CSV →
    generate a fresh ``run_id`` → ``start_run_strict`` (commits the run row + all ``pending``
    accounts in one transaction, #4/#5) → spawn the orchestrator task (registered with its
    terminal-state safety net, #8) → ``201 {run_id}``.

    A failed preflight propagates as a ``5xx`` with **no** task spawned and **no** rows
    written; a real run that fails the #14 gate is a ``403`` before any persistence (#4/#14).

    Args:
        file: The uploaded ``companies.csv``.
        dry_run: When True (UI default), write-backs are simulated; the run still persists.
        confirm: Explicit confirmation required to launch a real (non-dry) run (#14).
        concurrency: Optional per-run account concurrency; ``None`` uses the config default.
        skip_done_today: Skip accounts already completed for today (real runs only).
        test_email: Outreach recipient override for dry-run / test mode.
        user: The authenticated principal (attribution + the #14 role check).

    Returns:
        ``{"run_id": <id>}``.

    Raises:
        HTTPException: 400 (bad CSV), 403 (real-run gate), or 500 (preflight failure).
    """
    if not dry_run:
        try:
            auth.require_real_run_authorization(user, confirm)
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    if file is not None:
        raw = await file.read()
        companies = _parse_companies(raw)
    else:
        # No upload → use the repo-default companies.csv (plan #19: "companies.csv or repo
        # default"). A missing/empty default is a 400, never a 500.
        from duvo.orchestrator import load_companies

        try:
            companies = load_companies()
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="no CSV uploaded and no repo-default companies.csv found",
            ) from exc
        if not companies:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="repo-default companies.csv is empty",
            )

    run_id = uuid4().hex
    run_date = datetime.now(UTC).date().isoformat()
    conc = concurrency if concurrency is not None else config.MAX_CONCURRENT_ACCOUNTS

    try:
        companies_json = json.dumps([c.model_dump() for c in companies], ensure_ascii=False)
    except Exception:  # serialization must never block a launch
        companies_json = None

    # #4 preflight: run row + all pending accounts in ONE transaction, fail-fast (no _safe).
    # On failure nothing is committed, the task is never spawned, and we return 5xx.
    try:
        await runs.start_run_strict(
            run_id=run_id,
            run_date=run_date,
            started_at=_now_iso(),
            dry_run=dry_run,
            concurrency=conc,
            model=config.LLM_MODEL,
            app_env=config.APP_ENV,
            triggered_by=user.email,
            companies_json=companies_json,
            accounts=[
                {"domain": c.domain, "company_name": c.name, "country": c.country}
                for c in companies
            ],
        )
    except Exception as exc:
        _log.error("start_run_strict failed for run %s — not launching task", run_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to record run",
        ) from exc

    # Server path (plan #1/#3): persist always, app-scoped clients, user attribution.
    task = asyncio.create_task(
        run(
            run_id=run_id,
            companies=companies,
            triggered_by=user.email,
            dry_run=dry_run,
            test_email=test_email,
            persist=True,
            manage_clients=False,
            concurrency=conc,
            skip_done_today=skip_done_today,
        )
    )
    jobs.register(run_id, task)
    _log.info("launched run %s (%d accounts, dry_run=%s) by %s", run_id, len(companies), dry_run, user.email)
    return {"run_id": run_id}


# --------------------------------------------------------------------------- #
# Read endpoints (#12)
# --------------------------------------------------------------------------- #
@router.get("/runs")
async def list_runs(
    limit: int = 100,
    _user: User = Depends(auth.get_current_user),
) -> dict[str, list[dict[str, Any]]]:
    """Return the run history, newest first (plan #12, ``GET /runs``)."""
    return {"runs": await queries.list_runs(limit=limit)}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    _user: User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    """Return a run plus its ``account_runs`` snapshot (plan #12, ``GET /runs/{id}``)."""
    result = await queries.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return result


@router.get("/runs/{run_id}/accounts/{domain}")
async def get_account_detail(
    run_id: str,
    domain: str,
    _user: User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    """Return an account's full row + score/tier diff (plan #12, the drill-down)."""
    result = await queries.get_account_detail(run_id, domain)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="account not found")
    return result


# --------------------------------------------------------------------------- #
# SSE stream (#10, #11)
# --------------------------------------------------------------------------- #
async def _run_status(run_id: str) -> str | None:
    """Read the persisted run status, or ``None`` when the run row is absent."""
    row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", (run_id,))
    return row["status"] if row is not None else None


def _account_snapshot_event(row: dict[str, Any]) -> events.Event:
    """Build an ``account`` snapshot event from a persisted ``account_runs`` row (#10).

    Replays the account's status; a **completed** account also carries its persisted
    ``agent_log_json`` (decoded). An in-flight account's earlier tool lines are NOT replayed
    (the reconnect catches up live from here) — the MVP semantics stated in plan #10.
    """
    agent_log: Any = None
    if row.get("status") in _TERMINAL_STATUSES and row.get("agent_log_json"):
        try:
            agent_log = json.loads(row["agent_log_json"])
        except (ValueError, TypeError):
            agent_log = None
    return events.Event(
        type="account",
        run_id=row.get("run_id", ""),
        account=row.get("domain"),
        # Carried alongside the typed Event keys; consumed by the SPA's AccountRow.
        **{  # type: ignore[typeddict-item]
            "status": row.get("status"),
            "score": row.get("score"),
            "tier": row.get("tier"),
            "confidence": row.get("confidence"),
            "signals_count": row.get("signals_count"),
            "agent_log": agent_log,
        },
    )


def _sse(event: events.Event) -> dict[str, str]:
    """Format an :class:`events.Event` as an ``sse-starlette`` message dict."""
    return {"event": event.get("type", "message"), "data": json.dumps(event)}


async def _stream_events(run_id: str) -> AsyncIterator[dict[str, str]]:
    """Yield SSE messages for *run_id*: snapshot flush → live stream → terminal close.

    On (re)connect this:

    1. **Subscribes** a bounded queue on the event bus *before* reading the snapshot, so no
       live event published during the snapshot read is lost.
    2. **Flushes the DB snapshot** (#10): one ``account`` event per persisted account
       (status + completed accounts' ``agent_log``), then a ``status`` event with the run's
       current status. If the run is already terminal, it closes here.
    3. **Streams live** ``account`` / ``tool`` / ``status`` events from the queue until the
       run reaches a terminal status (re-checked when a ``status`` event arrives, and after a
       heartbeat-driven idle wakeup), then closes — always unsubscribing in ``finally`` so a
       dropped client never leaks a queue.

    Args:
        run_id: The run to stream.

    Yields:
        ``sse-starlette`` message dicts (``{"event": ..., "data": ...}``).
    """
    queue = events.subscribe(run_id)
    try:
        # 1) Snapshot: accounts then current run status (#10).
        snapshot = await queries.get_run(run_id)
        current_status: str | None
        if snapshot is None:
            current_status = None
            yield _sse(events.Event(type="status", run_id=run_id, **{"status": "not_found"}))  # type: ignore[typeddict-item]
        else:
            for account_row in snapshot["accounts"]:
                yield _sse(_account_snapshot_event(account_row))
            current_status = snapshot["run"].get("status")
            yield _sse(
                events.Event(type="status", run_id=run_id, **{"status": current_status})  # type: ignore[typeddict-item]
            )

        # Already terminal (or no such run): nothing live to wait for — close.
        if current_status is None or current_status in _TERMINAL_STATUSES:
            return

        # 2) Live stream until terminal (#11). A heartbeat-driven timeout lets us re-check
        #    the persisted status even if the producer published no terminal `status` event.
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except TimeoutError:
                # Idle: re-check persisted status so a run that ended without publishing a
                # terminal `status` event still closes the stream.
                if (await _run_status(run_id)) in _TERMINAL_STATUSES:
                    return
                continue

            yield _sse(event)
            if event.get("type") == "status" and event.get("status") in _TERMINAL_STATUSES:  # type: ignore[typeddict-item]
                return
    finally:
        events.unsubscribe(run_id, queue)


@router.get("/runs/{run_id}/stream")
async def stream_run(
    run_id: str,
    _user: User = Depends(auth.get_current_user),
) -> EventSourceResponse:
    """Live SSE feed for a run (plan #11): snapshot replay → live → terminal close.

    Returns a ``text/event-stream`` response with heartbeat comment pings,
    ``Cache-Control: no-cache`` and disabled proxy buffering so it survives intermediaries.
    """
    return EventSourceResponse(
        _stream_events(run_id),
        ping=_HEARTBEAT_SECONDS,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering
        },
    )
