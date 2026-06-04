"""Per-beat scout failure isolation."""

from duvo.agents.scouts.beats import BeatKey
from duvo.infra.logging_setup import get_logger
from duvo.models import Company, Signal

_log = get_logger(__name__)


async def run_scout_safe(
    company: Company,
    beat_key: BeatKey,
    beat_desc: str,
    log=None,
) -> list[Signal]:
    """Run one scout beat and degrade failures to an empty result."""
    from duvo.agents.scouts.scouts import run_scout

    try:
        return await run_scout(company, beat_key, beat_desc, log)
    except Exception as exc:
        _log.error("scout beat=%s failed for company=%r: %s", beat_key, company.name, exc)
        return []
