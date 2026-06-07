"""SQLite connection + schema init for the durable run-state store.

Stdlib ``sqlite3`` is synchronous, so every operation is offloaded via
``asyncio.to_thread`` (the house-rule pattern for sync-only libs) — the event
loop never blocks. WAL mode + a short ``busy_timeout`` let the concurrently
``gather``-ed account coroutines write without "database is locked". Every
public helper degrades to a safe default on error (like ``infra.tracing``) so a
persistence failure can never abort the pipeline.
"""

import asyncio
import os
import sqlite3
from collections.abc import Callable
from typing import Any

from duvo import config
from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")
_INITED: set[str] = set()  # db paths whose schema has been ensured this process

# Current schema version. `schema.sql` stamps fresh DBs with this via
# `PRAGMA user_version`; `_migrate` brings existing (lower-version) DBs up to it.
_SCHEMA_VERSION = 2


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    """``ALTER TABLE ... ADD COLUMN`` only if *column* is missing (guarded, idempotent)."""
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def _migrate(conn: sqlite3.Connection, from_version: int) -> None:
    """Bring an existing DB up to ``_SCHEMA_VERSION`` via guarded ``ALTER TABLE``.

    Versioned (not blind ``IF NOT EXISTS``): keyed on the DB's ``user_version`` *as it
    was before* ``schema.sql`` ran, so the work only runs on out-of-date DBs, while
    ``PRAGMA table_info`` guards make each ``ALTER TABLE`` idempotent even if a prior
    migration was interrupted. Fresh DBs created from ``schema.sql`` already carry every
    column and the current ``user_version``, so this is a no-op for them.

    Args:
        conn: An open connection whose schema may predate ``_SCHEMA_VERSION``.
        from_version: The ``PRAGMA user_version`` read before ``schema.sql`` executed
            (``schema.sql`` itself stamps the current version, masking the real age).
    """
    if from_version >= _SCHEMA_VERSION:
        return

    if from_version < 2:
        # Phase-1 columns (#15): run attribution + faithful-resume input, the
        # persisted agent audit log, and the write-back delivery lifecycle.
        _add_column(conn, "runs", "triggered_by", "triggered_by TEXT")
        _add_column(conn, "runs", "companies_json", "companies_json TEXT")
        _add_column(conn, "account_runs", "agent_log_json", "agent_log_json TEXT")
        # Legacy rows default to 'succeeded' so old, completed write-backs are not
        # mistaken for retryable ('attempting'/'failed') on a later resume.
        _add_column(
            conn,
            "writeback_events",
            "delivery_status",
            "delivery_status TEXT DEFAULT 'succeeded'",
        )

    conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _connect() -> sqlite3.Connection:
    path = config.DUVO_DB_PATH
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    if path not in _INITED:
        # Capture the on-disk version BEFORE schema.sql runs: schema.sql stamps the
        # current `user_version`, which would otherwise hide that an existing DB is old.
        from_version = conn.execute("PRAGMA user_version").fetchone()[0]
        # Idempotent DDL — a two-thread race here is safe (CREATE TABLE IF NOT EXISTS).
        with open(_SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
        # Versioned upgrade for pre-existing DBs (fresh ones are already current).
        # Runs once per process per path, under the same _INITED gate.
        _migrate(conn, from_version)
        conn.commit()
        _INITED.add(path)
    return conn


def _execute_sync(query: str, params: tuple) -> None:
    with _connect() as conn:
        conn.execute(query, params)
        # the connection context manager commits on clean exit


def _query_one_sync(query: str, params: tuple) -> sqlite3.Row | None:
    with _connect() as conn:
        cur = conn.execute(query, params)
        return cur.fetchone()


def _query_all_sync(query: str, params: tuple) -> list[sqlite3.Row]:
    with _connect() as conn:
        cur = conn.execute(query, params)
        return cur.fetchall()


async def _safe(fn: Callable[..., Any], default: Any, *args: Any) -> Any:
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as exc:
        _log.warning("store op failed (%s) — continuing without persistence", exc)
        return default


def _execute_tx_sync(work: Callable[[sqlite3.Connection], None]) -> None:
    with _connect() as conn:
        work(conn)
        # the connection context manager commits on clean exit, and rolls back if
        # `work` raises — so the whole `work` callback is one atomic transaction.


async def execute_tx(work: Callable[[sqlite3.Connection], None]) -> None:
    """Run *work* against one connection in a single transaction; **re-raise on error**.

    Unlike :func:`execute`/:func:`query_one`/:func:`query_all` (which route through
    :func:`_safe` and degrade to a default), this executor does **not** swallow
    exceptions: any failure inside *work* rolls back the whole transaction and
    propagates, so a caller can fail fast (plan #4 strict create-run preflight). Every
    statement *work* issues commits together or not at all.

    Args:
        work: A synchronous callback that receives the open ``sqlite3.Connection`` and
            issues all of its statements on it. It must not commit/close the connection.

    Raises:
        Exception: Whatever ``work`` (or SQLite) raises; the transaction is rolled back.
    """
    return await asyncio.to_thread(_execute_tx_sync, work)


async def execute(query: str, params: tuple = ()) -> None:
    """Run a write statement; commit. Returns None (also on error)."""
    return await _safe(_execute_sync, None, query, params)


async def query_one(query: str, params: tuple = ()) -> sqlite3.Row | None:
    """Return the first row, or None (also on error)."""
    return await _safe(_query_one_sync, None, query, params)


async def query_all(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Return all rows, or [] (also on error)."""
    return await _safe(_query_all_sync, [], query, params)
