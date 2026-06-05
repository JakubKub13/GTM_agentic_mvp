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
        # Idempotent DDL — a two-thread race here is safe (CREATE TABLE IF NOT EXISTS).
        with open(_SCHEMA_PATH, encoding="utf-8") as fh:
            conn.executescript(fh.read())
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


async def execute(query: str, params: tuple = ()) -> None:
    """Run a write statement; commit. Returns None (also on error)."""
    return await _safe(_execute_sync, None, query, params)


async def query_one(query: str, params: tuple = ()) -> sqlite3.Row | None:
    """Return the first row, or None (also on error)."""
    return await _safe(_query_one_sync, None, query, params)


async def query_all(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Return all rows, or [] (also on error)."""
    return await _safe(_query_all_sync, [], query, params)
