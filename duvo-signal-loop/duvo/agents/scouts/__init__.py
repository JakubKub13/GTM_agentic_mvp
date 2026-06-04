"""Scout agent package."""

from duvo.agents.scouts.beats import BEATS, BeatKey
from duvo.agents.scouts.isolation import run_scout_safe
from duvo.agents.scouts.scouts import run_scout, scout_all
from duvo.agents.scouts.tools.submit_signals import SUBMIT_SIGNALS_TOOL

SUBMIT_TOOL = SUBMIT_SIGNALS_TOOL
_run_scout_safe = run_scout_safe

__all__ = [
    "BEATS",
    "BeatKey",
    "SUBMIT_SIGNALS_TOOL",
    "SUBMIT_TOOL",
    "_run_scout_safe",
    "run_scout",
    "scout_all",
]
