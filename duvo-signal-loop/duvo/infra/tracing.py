"""Thin Langfuse tracing switch — the only module that imports ``langfuse`` (lazily).

When ``LANGFUSE_ENABLED`` is false or keys are missing, every helper degrades to a
no-op (``nullcontext`` / ``None``) so ``--dry-run`` and the offline test suite never
import ``langfuse`` or touch the network. Span calls in ``agent_core`` / the agents /
the orchestrator use the langfuse SDK directly through ``span`` / ``trace_context`` —
this module is an on/off switch, not a re-abstraction of spans.
"""

import contextlib
import re
from typing import Any

from duvo import config
from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

# Module-level singleton. Single-threaded asyncio: check-then-set is atomic enough.
_client: Any | None = None

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# tool name -> (langfuse observation as_type, emoji prefix for the span name)
_TOOL_OBS: dict[str, tuple[str, str]] = {
    "exa_search": ("retriever", "🔍"),
    "submit_signals": ("tool", "📥"),
    "record_assessment": ("tool", "📥"),
    "crm_upsert": ("tool", "🗂️"),
    "slack_alert": ("tool", "💬"),
    "outreach_queue": ("tool", "✉️"),
    "finish": ("tool", "🏁"),
}


def _mask(data: Any, **kwargs: Any) -> Any:
    """Langfuse mask callback: redact email addresses from any captured string I/O."""
    try:
        return _EMAIL_RE.sub("[email]", data) if isinstance(data, str) else data
    except Exception:
        return data


def init_tracing() -> None:
    """Create the Langfuse client if enabled and keys are present; otherwise a no-op.

    Lazily imports ``langfuse`` so disabled runs never import it. Never raises — a
    tracing failure must not abort the pipeline.
    """
    global _client
    if not (config.LANGFUSE_ENABLED and config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY):
        _log.debug("tracing disabled (LANGFUSE_ENABLED/keys not set)")
        _client = None
        return
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=config.LANGFUSE_PUBLIC_KEY,
            secret_key=config.LANGFUSE_SECRET_KEY,
            host=config.LANGFUSE_HOST,
            environment=config.APP_ENV,
            mask=_mask,
        )
        _log.info("Langfuse tracing enabled (host=%s env=%s)", config.LANGFUSE_HOST, config.APP_ENV)
    except Exception as exc:
        _log.warning("failed to init Langfuse tracing: %s — continuing without tracing", exc)
        _client = None


def enabled() -> bool:
    """Return True when a Langfuse client is active."""
    return _client is not None


def obs_for(tool_name: str) -> tuple[str, str]:
    """Return ``(as_type, emoji)`` for a tool name; default ``("tool", "🛠️")``."""
    return _TOOL_OBS.get(tool_name, ("tool", "🛠️"))


def span(**kwargs: Any):
    """Start a current observation when enabled, else a no-op context manager.

    Forwards directly to ``langfuse.start_as_current_observation`` (name, as_type,
    input, metadata, model, level, status_message, …). Yields ``None`` when disabled,
    so call sites guard updates with ``if handle is not None: handle.update(...)``.
    """
    if _client is None:
        return contextlib.nullcontext()
    return _client.start_as_current_observation(**kwargs)


def trace_context(*, session_id: str, tags: list[str], metadata: dict[str, Any]):
    """Propagate trace-level attributes when enabled, else a no-op context manager."""
    if _client is None:
        return contextlib.nullcontext()
    return _client.propagate_attributes(session_id=session_id, tags=tags, metadata=metadata)


def flush() -> None:
    """Flush pending traces before shutdown (never raises)."""
    if _client is None:
        return
    try:
        _client.flush()
    except Exception as exc:
        _log.warning("Langfuse flush failed: %s", exc)
