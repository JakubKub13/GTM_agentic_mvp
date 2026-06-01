"""exa_search — the tool scouts and the analyst use to search the web."""
from exa_py import Exa
from config import EXA_API_KEY, require
from logging_setup import get_logger

_log = get_logger(__name__)

_exa = None


def _get_exa() -> Exa:
    """Return the shared Exa client, initialising it on first use.

    Lazy init means the module can be imported in tests without a real API key;
    the key is only validated when an actual search is attempted.
    """
    global _exa
    if _exa is None:
        _exa = Exa(api_key=require("EXA_API_KEY", EXA_API_KEY))
    return _exa


EXA_SEARCH_TOOL = {
    "name": "exa_search",
    "description": (
        "Search the web for recent, sourced information. Returns up to 5 results "
        "with title, published date, url, and a short summary. Use a focused query; "
        "run again with a refined query to follow a promising thread."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Focused search query.",
            },
            "start_published_date": {
                "type": "string",
                "description": "Optional ISO date (YYYY-MM-DD); only results published after it.",
            },
        },
        "required": ["query"],
    },
}


def exa_search(query: str, start_published_date: str | None = None) -> str:
    """Search the web via Exa and return a formatted string of results.

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

    kwargs: dict = {"num_results": 5, "summary": {"query": query}}
    if start_published_date:
        kwargs["start_published_date"] = start_published_date

    try:
        res = _get_exa().search_and_contents(query, **kwargs)
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
