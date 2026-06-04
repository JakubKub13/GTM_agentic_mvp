"""Router prompt loader."""

from pathlib import Path

from duvo.agents.prompt_loader import load_prompt_file

_PROMPT = Path(__file__).with_name("router.md")


def load_router_prompt(**params: object) -> str:
    """Load the router system prompt."""
    return load_prompt_file(_PROMPT, **params)
