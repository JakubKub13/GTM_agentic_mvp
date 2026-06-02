"""Entry point for the duvo-signal-loop pipeline.

Thin shim that delegates to :func:`duvo.orchestrator.main` so the documented
``uv run python main.py`` workflow keeps working. The orchestration logic lives
in :mod:`duvo.orchestrator` (also runnable via ``python -m duvo.orchestrator``).
"""
from duvo.orchestrator import main

if __name__ == "__main__":
    main()
