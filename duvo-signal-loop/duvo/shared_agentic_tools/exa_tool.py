"""exa_search — the tool scouts and the analyst use to search the web."""

from exa_py import AsyncExa

from duvo.config import EXA_API_KEY, require
from duvo.infra.logging_setup import get_logger
from duvo.llm.base import ToolSpec, tool_schema

_log = get_logger(__name__)

_exa: AsyncExa | None = None


def _get_exa() -> AsyncExa:
    """Return the shared async Exa client, initialising it on first use.

    Lazy init means the module can be imported in tests without a real API key;
    the key is only validated when an actual search is attempted. ``AsyncExa``
    builds no HTTP client at construction (its ``_client`` is created lazily on
    the first awaited call), so this is safe to call before the event loop runs.

    Double-construct race with a single event loop is benign under CPython's GIL;
    clients are stateless.
    """
    global _exa
    if _exa is None:
        _exa = AsyncExa(api_key=require("EXA_API_KEY", EXA_API_KEY))
    return _exa


EXA_SEARCH_TOOL: ToolSpec = tool_schema(
    "exa_search",
    (
        "Search the web for recent, sourced information. Returns up to 5 results "
        "with title, published date, url, and a short summary. Use a focused query; "
        "run again with a refined query to follow a promising thread."
    ),
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Focused search query."},
            "start_published_date": {
                "type": "string",
                "description": "Optional ISO date (YYYY-MM-DD); only results published after it.",
            },
        },
        "required": ["query"],
    },
)


async def exa_search(query: str, start_published_date: str | None = None) -> str:
    """Search the web via Exa and return a formatted string of results (async).

    Uses the native async client (``AsyncExa.search``), so the call
    awaits directly on the event loop — no thread offload needed — and is
    cancellable by an enclosing ``asyncio.timeout``.

    Args:
        query:               Focused search query.
        start_published_date: Optional ISO date (YYYY-MM-DD); only results
                             published on or after this date are returned.

    Returns:
        A newline-joined string with TITLE / DATE / URL / SUMMARY for each
        result, or ``"no results"`` when the response is empty, or a
        ``"search failed: <error>"`` string on exception.
    """
    if start_published_date:
        _log.info("exa_search: query=%r  start_published_date=%s", query, start_published_date)
    else:
        _log.info("exa_search: query=%r", query)

    # contents={"summary": ...} returns a query-focused summary per result and no
    # full page text — same shape the formatting below reads (r.summary), and the
    # modern, non-deprecated replacement for the old search_and_contents(summary=...).
    kwargs: dict = {"num_results": 5, "contents": {"summary": {"query": query}}}
    if start_published_date:
        kwargs["start_published_date"] = start_published_date

    try:
        res = await _get_exa().search(query, **kwargs)
    except Exception as exc:
        _log.warning("exa_search failed for query=%r: %s", query, exc)
        return f"search failed: {exc}"

    results = getattr(res, "results", [])
    _log.debug("exa_search: %d result(s) returned for query=%r", len(results), query)

    lines = []
    for r in results:
        summary = (getattr(r, "summary", None) or "").strip().replace("\n", " ")
        lines.append(
            f"- TITLE: {getattr(r, 'title', None) or '(none)'}\n"
            f"  DATE: {getattr(r, 'published_date', None) or 'unknown'}\n"
            f"  URL: {getattr(r, 'url', '') or ''}\n"
            f"  SUMMARY: {summary[:400]}"
        )
    return "\n".join(lines) if lines else "no results"


async def aclose() -> None:
    """Close the shared async Exa client's HTTP pool and reset the module cache.

    Mirrors :func:`duvo.infra.http_client.aclose` — await it during shutdown so
    the underlying ``httpx.AsyncClient`` drains cleanly (no unclosed-socket
    ``ResourceWarning``). Safe to call when no client (or no HTTP pool) was ever
    created.

    Reads ``_exa._client`` directly rather than the ``.client`` property, which
    would lazily *create* a client only to immediately close it.
    """
    global _exa
    if _exa is not None and _exa._client is not None:
        _log.debug("Closing shared AsyncExa HTTP client")
        await _exa._client.aclose()
    _exa = None
