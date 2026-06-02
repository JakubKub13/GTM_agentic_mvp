"""Scout agents: one per signal beat, each a tool-using agent over exa_search."""
import asyncio
from typing import Literal

from config import MAX_SCOUT_SEARCHES
from models import Company, Signal
from agent_core import run_agent
from tools.exa_tool import EXA_SEARCH_TOOL, exa_search
from logging_setup import get_logger

_log = get_logger(__name__)

BEATS = [
    ("erp_migration", "ERP / supply-chain / finance software migrations and implementations "
                      "(SAP, S/4HANA, Oracle, new procurement or reconciliation systems)"),
    ("hiring", "hiring in supply chain, procurement, operations, or finance "
               "(new directors/managers, team build-outs)"),
    ("ma_leadership", "M&A activity and leadership changes (new CFO, COO, Supply Chain Director, "
                      "acquisitions, mergers)"),
    ("pain", "operational pain: manual reconciliation, invoice/PO matching, supplier-portal "
             "chaos, inventory or back-office inefficiency"),
]

SUBMIT_TOOL = {
    "name": "submit_signals",
    "description": "Submit the sourced signals you found (may be empty). Call once when done.",
    "input_schema": {
        "type": "object",
        "properties": {
            "signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "summary": {"type": "string", "description": "1-2 sentences."},
                        "source_url": {"type": "string"},
                        "published_date": {"type": "string", "description": "ISO date or empty if unknown."},
                        "relevance": {"type": "string", "description": "Why this matters for Duvo."},
                    },
                    "required": ["title", "summary", "source_url", "relevance"],
                },
            }
        },
        "required": ["signals"],
    },
}


async def run_scout(
    company: Company,
    beat_key: Literal["erp_migration", "hiring", "ma_leadership", "pain"],
    beat_desc: str,
    log=None,
) -> list[Signal]:
    """Run a single scout agent for one beat and return the signals it finds.

    Args:
        company:   The company to research.
        beat_key:  One of the four Literal signal_type values (matches the beat).
        beat_desc: Descriptive text for the scout's system prompt.
        log:       Shared list for audit entries. With asyncio, all coroutines run
                   on one event loop thread so list.append is safe with no locking.

    Returns:
        A list of Signal objects extracted from the agent's submit_signals call.
    """
    _log.info("scout starting: company=%r beat=%s", company.name, beat_key)

    system = (
        "You are a B2B GTM signal scout for Duvo (AI agents that automate retail/CPG back-office "
        f"operations like reconciliation and PO matching). Your beat: {beat_desc}.\n"
        "Find real, recent, SOURCED intent signals about the target company in your beat. "
        f"Search iteratively with exa_search: start broad, then refine to follow the best thread. "
        f"Do at most {MAX_SCOUT_SEARCHES} searches. Discard anything not clearly about the target "
        "company or not in your beat. NEVER invent facts — report only what a result supports; an "
        "empty result set is fine. When done, call submit_signals."
    )
    user = (f"Target company: {company.name} ({company.domain}, {company.country}). "
            f"Context: {company.description}")
    captured: dict = {"signals": []}

    def submit_signals(signals=None):
        # The model sometimes calls submit_signals() with no args to mean "found nothing".
        # Tolerate that (and a null) instead of raising, so the beat returns [] cleanly.
        signals = signals or []
        captured["signals"] = signals
        return f"received {len(signals)} signals"

    # exa_search is async — run_agent awaits awaitable impls automatically.
    # submit_signals is sync — run_agent calls it directly.
    impls = {"exa_search": exa_search, "submit_signals": submit_signals}
    await run_agent(system, user, [EXA_SEARCH_TOOL, SUBMIT_TOOL], impls,
                    max_turns=MAX_SCOUT_SEARCHES + 2, final_tools={"submit_signals"}, log=log)

    out = []
    for s in captured["signals"]:
        out.append(Signal(
            signal_type=beat_key,
            title=s.get("title", "(no title)"),
            summary=s.get("summary", ""),
            source_url=s.get("source_url", ""),
            published_date=(s.get("published_date") or None),
            relevance=s.get("relevance", ""),
        ))

    _log.info("scout done: company=%r beat=%s signals=%d", company.name, beat_key, len(out))
    return out


async def _run_scout_safe(company: Company, beat_key: Literal["erp_migration", "hiring", "ma_leadership", "pain"], beat_desc: str, log=None) -> list[Signal]:
    """Async wrapper around run_scout that isolates per-beat failures.

    If run_scout raises for one beat, logs the error and returns [] so the
    other beats' results are not discarded.
    """
    try:
        return await run_scout(company, beat_key, beat_desc, log)
    except Exception as exc:
        _log.error("scout beat=%s failed for company=%r: %s", beat_key, company.name, exc)
        return []


async def scout_all(company: Company, log=None) -> list[Signal]:
    """Run all 4 scout agents concurrently for one company via asyncio.gather.

    Args:
        company: The company to research.
        log:     Shared audit log list passed to each scout. All coroutines run
                 on one event loop thread — no locking needed for list.append.

    Returns:
        Flattened list of all Signal objects from all 4 beats.
    """
    results = await asyncio.gather(
        *[_run_scout_safe(company, key, desc, log) for key, desc in BEATS],
    )
    signals = [sig for group in results for sig in group]
    _log.info("scout_all done: company=%r total_signals=%d", company.name, len(signals))
    return signals
