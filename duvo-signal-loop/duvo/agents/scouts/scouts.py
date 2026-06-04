"""Scout agents: one per signal beat, each a tool-using agent over exa_search."""

import asyncio
from typing import Any

from duvo.agent_core import run_agent
from duvo.agents.scouts.beats import BEATS, BeatKey
from duvo.agents.scouts.isolation import run_scout_safe
from duvo.agents.scouts.prompts import load_scout_prompt
from duvo.agents.scouts.tools.submit_signals import (
    SUBMIT_SIGNALS_TOOL,
    make_submit_signals_tool,
    signals_from_payload,
)
from duvo.config import MAX_SCOUT_SEARCHES
from duvo.infra.logging_setup import get_logger
from duvo.models import Company, Signal
from duvo.shared_agentic_tools.exa_tool import EXA_SEARCH_TOOL, exa_search

_log = get_logger(__name__)


async def run_scout(
    company: Company,
    beat_key: BeatKey,
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

    system = load_scout_prompt(beat_desc=beat_desc, max_scout_searches=MAX_SCOUT_SEARCHES)
    user = (
        f"Target company: {company.name} ({company.domain}, {company.country}). "
        f"Context: {company.description}"
    )
    captured: dict[str, list[dict[str, Any]]] = {"signals": []}

    impls = {
        "exa_search": exa_search,
        "submit_signals": make_submit_signals_tool(captured),
    }
    await run_agent(
        system,
        user,
        [EXA_SEARCH_TOOL, SUBMIT_SIGNALS_TOOL],
        impls,
        max_turns=MAX_SCOUT_SEARCHES + 2,
        final_tools={"submit_signals"},
        log=log,
    )

    out = signals_from_payload(captured["signals"], beat_key)
    _log.info("scout done: company=%r beat=%s signals=%d", company.name, beat_key, len(out))
    return out


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
        *[run_scout_safe(company, key, desc, log) for key, desc in BEATS],
    )
    signals = [sig for group in results for sig in group]
    _log.info("scout_all done: company=%r total_signals=%d", company.name, len(signals))
    return signals
