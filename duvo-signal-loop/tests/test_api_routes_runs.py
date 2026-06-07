"""Tests for the run endpoints (plan #8, #12) — POST /runs + read endpoints.

Fully offline: ``run()`` is mocked so no pipeline / network / LLM call happens; the
only side effect is rows in a temp SQLite DB. The auth dependency is overridden with a
fake operator so the tests focus on this unit's behaviour, not the auth handshake
(covered by ``test_api_auth.py``).
"""

import asyncio
import io
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from duvo.api import auth, jobs, routes_runs
from duvo.store import db, runs


@pytest.fixture(autouse=True)
def _tmp_db(monkeypatch, tmp_path):
    from duvo import config

    monkeypatch.setattr(config, "DUVO_DB_PATH", str(tmp_path / "test.db"))
    db._INITED.clear()
    yield
    db._INITED.clear()


@pytest.fixture(autouse=True)
def _clean_registry():
    jobs._REGISTRY.clear()
    jobs._FINALIZERS.clear()
    yield
    jobs._REGISTRY.clear()
    jobs._FINALIZERS.clear()


def _operator() -> auth.User:
    return auth.User(email="op@duvo.io", name="Op", role="admin")


def _build_app(*, user: auth.User | None = None) -> FastAPI:
    """A minimal app mounting this unit's router with auth overridden to *user*."""
    app = FastAPI()
    app.include_router(routes_runs.router)
    # Override the auth + CSRF dependencies so endpoint logic is tested in isolation.
    app.dependency_overrides[auth.get_current_user] = lambda: (user or _operator())
    app.dependency_overrides[auth.require_csrf] = lambda: None
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


_CSV = b"name,domain,country,description\nAcme,acme.com,US,Widgets\nBeta,beta.io,UK,Gadgets\n"


# --------------------------------------------------------------------------- #
# POST /runs — happy path
# --------------------------------------------------------------------------- #
async def test_post_runs_dry_run_returns_201_and_persists(monkeypatch):
    started: dict = {}

    async def _fake_run(**kwargs):
        started.update(kwargs)
        # run() is the real owner of run-row writes; the strict preflight already
        # wrote the row + pending accounts, so the task just records that it ran.
        await asyncio.sleep(0)

    monkeypatch.setattr(routes_runs, "run", _fake_run)

    app = _build_app()
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "true", "concurrency": "3"},
            files={"file": ("companies.csv", io.BytesIO(_CSV), "text/csv")},
        )

    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    assert run_id

    # The strict preflight committed the run row + 2 pending accounts BEFORE the task.
    snapshot = await db.query_one("SELECT * FROM runs WHERE run_id = ?", (run_id,))
    assert snapshot is not None
    assert snapshot["accounts_total"] == 2
    assert snapshot["triggered_by"] == "op@duvo.io"
    accounts = await db.query_all(
        "SELECT domain, status FROM account_runs WHERE run_id = ? ORDER BY domain", (run_id,)
    )
    assert {a["domain"] for a in accounts} == {"acme.com", "beta.io"}
    assert {a["status"] for a in accounts} == {"pending"}

    # Let the spawned task run; assert run() got API-path arguments.
    task = jobs.get(run_id)
    if task is not None:
        await task
    assert started["run_id"] == run_id
    assert started["persist"] is True  # server always persists (#3)
    assert started["manage_clients"] is False  # app-scoped clients (#1/Option 2)
    assert started["triggered_by"] == "op@duvo.io"
    assert started["dry_run"] is True


# --------------------------------------------------------------------------- #
# POST /runs — real-run gate (#14)
# --------------------------------------------------------------------------- #
async def test_post_real_run_without_confirm_is_rejected(monkeypatch):
    calls: list = []

    async def _fake_run(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(routes_runs, "run", _fake_run)

    app = _build_app()
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "false"},  # real run, no confirm
            files={"file": ("companies.csv", io.BytesIO(_CSV), "text/csv")},
        )

    assert resp.status_code == 403
    assert calls == []  # task never launched
    # No phantom run row.
    rows = await db.query_all("SELECT run_id FROM runs", ())
    assert rows == []


async def test_post_real_run_viewer_role_is_rejected(monkeypatch):
    async def _fake_run(**kwargs):
        pass

    monkeypatch.setattr(routes_runs, "run", _fake_run)

    app = _build_app(user=auth.User(email="v@x.com", name="V", role="viewer"))
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "false", "confirm": "true"},
            files={"file": ("companies.csv", io.BytesIO(_CSV), "text/csv")},
        )

    assert resp.status_code == 403


async def test_post_real_run_confirmed_operator_is_accepted(monkeypatch):
    async def _fake_run(**kwargs):
        await asyncio.sleep(0)

    monkeypatch.setattr(routes_runs, "run", _fake_run)

    app = _build_app()
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "false", "confirm": "true"},
            files={"file": ("companies.csv", io.BytesIO(_CSV), "text/csv")},
        )

    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    row = await db.query_one("SELECT dry_run FROM runs WHERE run_id = ?", (run_id,))
    assert row["dry_run"] == 0
    task = jobs.get(run_id)
    if task is not None:
        await task


# --------------------------------------------------------------------------- #
# POST /runs — CSV validation + row cap
# --------------------------------------------------------------------------- #
async def test_post_runs_empty_csv_is_rejected(monkeypatch):
    monkeypatch.setattr(routes_runs, "run", lambda **k: None)

    app = _build_app()
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "true"},
            files={"file": ("c.csv", io.BytesIO(b"name,domain\n"), "text/csv")},
        )
    assert resp.status_code == 400
    assert await db.query_all("SELECT run_id FROM runs", ()) == []


async def test_post_runs_row_cap_rejects_oversized_csv(monkeypatch):
    monkeypatch.setattr(routes_runs, "MAX_CSV_ROWS", 2)
    monkeypatch.setattr(routes_runs, "run", lambda **k: None)

    big = b"name,domain\n" + b"".join(
        f"c{i},c{i}.com\n".encode() for i in range(5)
    )
    app = _build_app()
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "true"},
            files={"file": ("c.csv", io.BytesIO(big), "text/csv")},
        )
    assert resp.status_code == 400
    assert await db.query_all("SELECT run_id FROM runs", ()) == []


async def test_post_runs_missing_required_columns_is_rejected(monkeypatch):
    monkeypatch.setattr(routes_runs, "run", lambda **k: None)

    app = _build_app()
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "true"},
            # No 'domain' column.
            files={"file": ("c.csv", io.BytesIO(b"name,country\nAcme,US\n"), "text/csv")},
        )
    assert resp.status_code == 400
    assert await db.query_all("SELECT run_id FROM runs", ()) == []


# --------------------------------------------------------------------------- #
# POST /runs — strict preflight failure leaves NOTHING (#4)
# --------------------------------------------------------------------------- #
async def test_post_runs_strict_preflight_failure_leaves_no_task_no_row(monkeypatch):
    launched: list = []

    async def _fake_run(**kwargs):
        launched.append(kwargs)

    async def _boom(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(routes_runs, "run", _fake_run)
    monkeypatch.setattr(routes_runs.runs, "start_run_strict", _boom)

    app = _build_app()
    async with _client(app) as c:
        resp = await c.post(
            "/runs",
            data={"dry_run": "true"},
            files={"file": ("c.csv", io.BytesIO(_CSV), "text/csv")},
        )

    assert resp.status_code >= 500
    assert launched == []  # never created the task
    assert jobs.active_run_ids() == []  # nothing registered
    assert await db.query_all("SELECT run_id FROM runs", ()) == []


# --------------------------------------------------------------------------- #
# GET /runs, GET /runs/{id}, GET /runs/{id}/accounts/{domain} (#12)
# --------------------------------------------------------------------------- #
async def _seed(run_id: str) -> None:
    await runs.start_run_strict(
        run_id=run_id,
        run_date="2026-06-06",
        started_at="2026-06-06T10:00:00Z",
        dry_run=False,
        concurrency=1,
        model="m",
        app_env="dev",
        triggered_by="op@duvo.io",
        companies_json=json.dumps([{"name": "Acme", "domain": "acme.com"}]),
        accounts=[{"domain": "acme.com", "company_name": "Acme", "country": "US"}],
    )


async def test_get_runs_lists_history():
    await _seed("r1")
    app = _build_app()
    async with _client(app) as c:
        resp = await c.get("/runs")
    assert resp.status_code == 200
    body = resp.json()
    ids = [r["run_id"] for r in body["runs"]]
    assert "r1" in ids


async def test_get_run_returns_run_and_accounts():
    await _seed("r1")
    app = _build_app()
    async with _client(app) as c:
        resp = await c.get("/runs/r1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run"]["run_id"] == "r1"
    assert [a["domain"] for a in body["accounts"]] == ["acme.com"]


async def test_get_run_missing_returns_404():
    app = _build_app()
    async with _client(app) as c:
        resp = await c.get("/runs/nope")
    assert resp.status_code == 404


async def test_get_account_detail_returns_detail_and_diff():
    await _seed("r1")
    app = _build_app()
    async with _client(app) as c:
        resp = await c.get("/runs/r1/accounts/acme.com")
    assert resp.status_code == 200
    body = resp.json()
    assert body["account"]["domain"] == "acme.com"
    assert "diff" in body


async def test_get_account_detail_missing_returns_404():
    await _seed("r1")
    app = _build_app()
    async with _client(app) as c:
        resp = await c.get("/runs/r1/accounts/ghost.com")
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# POST /runs — repo-default companies.csv fallback (no upload, plan #19)
# --------------------------------------------------------------------------- #
async def test_post_runs_without_file_uses_repo_default(monkeypatch):
    """No upload → server reads its repo-default companies via load_companies() (#19)."""
    from duvo.models import Company

    monkeypatch.setattr(routes_runs, "run", lambda **kw: asyncio.sleep(0))
    monkeypatch.setattr(
        "duvo.orchestrator.load_companies",
        lambda: [Company(name="Default", domain="default.com", country="US", description="d")],
    )

    app = _build_app()
    async with _client(app) as c:
        resp = await c.post("/runs", data={"dry_run": "true"})

    assert resp.status_code == 201
    run_id = resp.json()["run_id"]
    accounts = await db.query_all(
        "SELECT domain FROM account_runs WHERE run_id = ?", (run_id,)
    )
    assert {a["domain"] for a in accounts} == {"default.com"}
