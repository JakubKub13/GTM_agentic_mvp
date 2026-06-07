"""Tests for the FastAPI app factory + lifespan + SPA mount (plan #7, #6, #16).

Fully offline: the lifespan's network-touching collaborators (``http_client.aclose``,
``exa_tool.aclose``, ``tracing.init_tracing``, ``jobs.drain``) are patched so entering
and exiting the lifespan never reaches the network or constructs the Exa client. The
sweep helper writes only to a temp SQLite DB.
"""

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from duvo.api import app as app_mod
from duvo.store import db, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --------------------------------------------------------------------------- #
# create_app shape + router mounting
# --------------------------------------------------------------------------- #
def test_create_app_returns_fastapi_app():
    app = app_mod.create_app()
    assert isinstance(app, FastAPI)


def test_create_app_mounts_auth_and_runs_routes():
    app = app_mod.create_app()
    paths = {route.path for route in app.routes}
    # Auth router (#13) + run endpoints (#8/#12) are mounted.
    assert "/auth/login" in paths
    assert "/me" in paths
    assert "/runs" in paths
    assert "/runs/{run_id}/stream" in paths


def test_non_auth_routes_require_authentication():
    """The auth-guard dependency protects the read endpoints (#13)."""
    app = app_mod.create_app()

    async def _go():
        async with _client(app) as c:
            return await c.get("/runs")

    resp = asyncio.run(_go())
    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# Lifespan — startup (#7, #6) and shutdown (#7) sequencing
# --------------------------------------------------------------------------- #
async def test_lifespan_startup_configures_logging_tracing_and_sweeps(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(app_mod, "configure_logging", lambda: calls.append("log"))
    monkeypatch.setattr(app_mod.tracing, "init_tracing", lambda: calls.append("trace"))
    monkeypatch.setattr(app_mod.tracing, "flush", lambda: None)

    async def _fake_sweep():
        calls.append("sweep")
        return []

    monkeypatch.setattr(app_mod.runs, "sweep_interrupted_runs", _fake_sweep)

    # Shutdown collaborators must not touch the network.
    async def _noop():
        calls.append("close")

    monkeypatch.setattr(app_mod.jobs, "drain", _noop)
    monkeypatch.setattr(app_mod.http_client, "aclose", _noop)
    monkeypatch.setattr(app_mod.exa_tool, "aclose", _noop)

    app = app_mod.create_app()

    # Drive the lifespan context directly (ASGITransport does not emit lifespan events).
    async with app_mod.lifespan(app):
        # Inside the running lifespan: startup work has happened.
        assert "log" in calls
        assert "trace" in calls
        assert "sweep" in calls

    # Startup ran configure_logging + init_tracing exactly once, and the sweep.
    assert calls.count("log") == 1
    assert calls.count("trace") == 1
    assert calls.count("sweep") == 1


async def test_lifespan_shutdown_drains_then_closes_clients(monkeypatch):
    order: list[str] = []

    monkeypatch.setattr(app_mod, "configure_logging", lambda: None)
    monkeypatch.setattr(app_mod.tracing, "init_tracing", lambda: None)
    monkeypatch.setattr(app_mod.tracing, "flush", lambda: None)

    async def _fake_sweep():
        return []

    monkeypatch.setattr(app_mod.runs, "sweep_interrupted_runs", _fake_sweep)

    async def _drain():
        order.append("drain")

    async def _http_close():
        order.append("http")

    async def _exa_close():
        order.append("exa")

    monkeypatch.setattr(app_mod.jobs, "drain", _drain)
    monkeypatch.setattr(app_mod.http_client, "aclose", _http_close)
    monkeypatch.setattr(app_mod.exa_tool, "aclose", _exa_close)

    app = app_mod.create_app()

    async with app_mod.lifespan(app):
        pass  # enter + exit the lifespan

    # Drain runs before either client is closed (#7: drain active tasks, then close).
    assert order.index("drain") < order.index("http")
    assert order.index("drain") < order.index("exa")
    assert {"http", "exa"} <= set(order)


async def test_lifespan_does_not_eagerly_construct_exa(monkeypatch):
    """Exa stays lazy — the lifespan owns only its close, never builds it (#7)."""
    monkeypatch.setattr(app_mod, "configure_logging", lambda: None)
    monkeypatch.setattr(app_mod.tracing, "init_tracing", lambda: None)
    monkeypatch.setattr(app_mod.tracing, "flush", lambda: None)

    async def _fake_sweep():
        return []

    monkeypatch.setattr(app_mod.runs, "sweep_interrupted_runs", _fake_sweep)

    constructed: list[str] = []
    monkeypatch.setattr(
        app_mod.exa_tool, "_get_exa", lambda: constructed.append("exa") or None
    )

    async def _noop():
        pass

    monkeypatch.setattr(app_mod.jobs, "drain", _noop)
    monkeypatch.setattr(app_mod.http_client, "aclose", _noop)
    monkeypatch.setattr(app_mod.exa_tool, "aclose", _noop)

    app = app_mod.create_app()

    async with app_mod.lifespan(app):
        pass

    assert constructed == []  # never built the Exa client


# --------------------------------------------------------------------------- #
# Startup sweep helper (#6) — store side
# --------------------------------------------------------------------------- #
async def test_sweep_interrupted_runs_marks_stale_running_as_interrupted():
    # A stale 'running' run (e.g. left by a crashed process).
    await runs.start_run(
        run_id="stale",
        run_date="d",
        started_at="t",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
    )
    # A run that already finished must NOT be touched.
    await runs.start_run(
        run_id="done",
        run_date="d",
        started_at="t",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        accounts_total=1,
    )
    await runs.finish_run(run_id="done", finished_at="t2", succeeded=1, failed=0)

    swept = await runs.sweep_interrupted_runs()

    assert "stale" in swept
    assert "done" not in swept
    stale_row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", ("stale",))
    done_row = await db.query_one("SELECT status FROM runs WHERE run_id = ?", ("done",))
    assert stale_row["status"] == "interrupted"
    assert done_row["status"] == "done"


async def test_sweep_interrupted_runs_returns_empty_when_nothing_stale():
    swept = await runs.sweep_interrupted_runs()
    assert swept == []


# --------------------------------------------------------------------------- #
# SPA static mount (#16)
# --------------------------------------------------------------------------- #
def test_spa_fallback_serves_index_html(monkeypatch, tmp_path):
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html><title>duvo</title>")
    (build / "assets").mkdir()
    (build / "assets" / "app.js").write_text("console.log('app')")

    monkeypatch.setenv("DUVO_FRONTEND_DIR", str(build))
    app = app_mod.create_app()

    async def _go():
        async with _client(app) as c:
            root = await c.get("/")
            deep = await c.get("/dashboard/history")  # client-side route → index.html
            asset = await c.get("/assets/app.js")
            return root, deep, asset

    root, deep, asset = asyncio.run(_go())
    assert root.status_code == 200
    assert "duvo" in root.text
    # A non-API path the SPA owns falls back to index.html (history routing).
    assert deep.status_code == 200
    assert "duvo" in deep.text
    assert asset.status_code == 200
    assert "console.log" in asset.text


def test_missing_frontend_dir_does_not_break_app(monkeypatch, tmp_path):
    """A missing build dir must not crash app creation (dev / API-only, #16 guard)."""
    monkeypatch.setenv("DUVO_FRONTEND_DIR", str(tmp_path / "nonexistent"))
    app = app_mod.create_app()
    assert isinstance(app, FastAPI)
    # API routes still work even with no SPA mounted.
    paths = {route.path for route in app.routes}
    assert "/runs" in paths
