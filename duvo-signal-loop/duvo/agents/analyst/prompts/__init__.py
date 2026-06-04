"""Analyst prompt loader."""

from pathlib import Path

from duvo.agents.prompt_loader import load_prompt_file

_PROMPT = Path(__file__).with_name("analyst.md")


def load_analyst_prompt(**params: object) -> str:
    """Load the analyst system prompt."""
    return load_prompt_file(_PROMPT, **params)
