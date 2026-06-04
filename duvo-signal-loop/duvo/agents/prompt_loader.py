"""Shared helper for loading agent-owned Markdown prompts."""

from pathlib import Path


def load_prompt_file(path: Path, **params: object) -> str:
    """Load a Markdown prompt and substitute placeholders when params are supplied."""
    text = path.read_text(encoding="utf-8").rstrip("\n")
    return text.format(**params) if params else text
