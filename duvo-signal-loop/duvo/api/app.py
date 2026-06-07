"""FastAPI app factory + process lifespan + SPA static mount (plan #7, #6, #16).

:func:`create_app` builds the single in-process FastAPI application:

* **Lifespan (#7).** On **startup** it configures logging and initialises tracing
  **once** for the long-lived process (today these run inside ``orchestrator.run`` —
  wrong for a server that hosts many runs) and runs the interrupted-run sweep (#6,
  :func:`duvo.store.runs.sweep_interrupted_runs`) so a crash that left rows stuck in
  ``running`` is surfaced rather than silently resumed. On **shutdown** it drains the
  active run tasks (:func:`duvo.api.jobs.drain`) **before** closing the shared clients
  (``http_client.aclose`` then ``exa_tool.aclose``), so no in-flight request is severed
  under it. **Exa stays lazy** — the lifespan owns only its *close*; it never constructs
  the client (that would force ``EXA_API_KEY`` even for an auth/history-only startup).
  Tracing is flushed per-run-completion elsewhere, decoupled from client ownership.
* **Routers.** Mounts the auth router (#13) and the run endpoints (#8/#12); the auth
  guard dependency on each non-auth route enforces authentication.
* **SPA (#16).** Mounts the Vite build dir via ``StaticFiles`` with an ``index.html``
  fallback so client-side (history) routes resolve, giving one deployable. A missing
  build dir (dev / API-only) is tolerated — the app still serves the API.

The frontend build dir is read from ``DUVO_FRONTEND_DIR`` (default ``frontend/dist``
relative to the repo root), lazily and without editing ``config.py`` — mirroring the
auth module's env-with-default convention.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles

from duvo.api import auth, jobs, routes_runs
from duvo.infra import http_client, tracing
from duvo.infra.logging_setup import configure_logging, get_logger
from duvo.shared_agentic_tools import exa_tool
from duvo.store import runs

_log = get_logger(__name__)


def _frontend_dir() -> Path:
    """The Vite build directory to serve (env-overridable, repo-relative default)."""
    raw = os.environ.get("DUVO_FRONTEND_DIR", "")
    if raw:
        return Path(raw)
    # duvo/api/app.py -> repo root is two parents up from the package dir.
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Process-scoped startup/shutdown (plan #7).

    Startup: configure logging + init tracing once, then sweep stale ``running`` runs to
    ``interrupted`` (#6). Shutdown: drain active run tasks, then close the shared HTTP and
    Exa clients (the app owns their lifecycle under ``manage_clients=False`` runs). Exa is
    never eagerly constructed here — only its close is owned.
    """
    configure_logging()
    tracing.init_tracing()
    try:
        swept = await runs.sweep_interrupted_runs()
        if swept:
            _log.warning("startup swept %d stale run(s) to interrupted: %s", len(swept), swept)
    except Exception as exc:  # a sweep failure must not block the server from starting
        _log.warning("startup interrupted-run sweep failed: %s", exc)

    try:
        yield
    finally:
        # Drain in-flight run tasks first, then release the shared client pools.
        await jobs.drain()
        await http_client.aclose()
        await exa_tool.aclose()
        tracing.flush()


class _SpaStaticFiles(StaticFiles):
    """``StaticFiles`` that falls back to ``index.html`` for unmatched paths (#16).

    Real assets are served as usual; any other path (a client-side route like
    ``/runs/<id>``) returns ``index.html`` so the SPA's history router resolves it. A
    genuinely missing file with no ``index.html`` falls through to the default 404.
    """

    async def get_response(self, path: str, scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except Exception:
            index = Path(self.directory) / "index.html"  # type: ignore[arg-type]
            if index.is_file():
                return FileResponse(index)
            raise


def _mount_spa(app: FastAPI) -> None:
    """Mount the Vite build with SPA fallback, tolerating a missing build dir (#16)."""
    build = _frontend_dir()
    if not build.is_dir():
        _log.info("frontend build dir %s not present — serving API only", build)
        return
    # Mounted last so API routes take precedence over the catch-all static handler.
    app.mount("/", _SpaStaticFiles(directory=str(build), html=True), name="spa")
    _log.info("serving SPA from %s", build)


def create_app() -> FastAPI:
    """Build the in-process FastAPI app: lifespan + routers + SPA mount (plan #7/#16).

    Returns:
        The configured :class:`fastapi.FastAPI` application.
    """
    app = FastAPI(title="duvo-signal-loop", lifespan=lifespan)
    app.include_router(auth.router)
    app.include_router(routes_runs.router)
    _mount_spa(app)
    return app
