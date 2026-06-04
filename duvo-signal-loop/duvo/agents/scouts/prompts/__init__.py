"""Scout prompt loader."""

from pathlib import Path

from duvo.agents.prompt_loader import load_prompt_file

_PROMPT = Path(__file__).with_name("scout.md")


def load_scout_prompt(**params: object) -> str:
    """Load the scout system prompt."""
    return load_prompt_file(_PROMPT, **params)
