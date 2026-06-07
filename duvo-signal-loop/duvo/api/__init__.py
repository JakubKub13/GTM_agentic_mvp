"""FastAPI HTTP layer for duvo-signal-loop (plan §"Backend — FastAPI layer").

One concern per module (per the house rules): ``jobs.py`` owns the in-process
run-task registry; sibling modules add the app, routes, auth and SSE.
"""
