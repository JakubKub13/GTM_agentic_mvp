"""Agent system prompts, kept as Markdown for easy review and editing.

Each agent's system prompt lives in its own ``<name>.md`` file beside this module
so prompt copy can be read, diffed, and edited without touching Python — the core
of good prompt hygiene. Code asks for a prompt by name via :func:`load_prompt`;
dynamic values (a scout's beat, the per-agent search caps) are written as
``{placeholder}`` slots and filled in at load time.
"""

from pathlib import Path

_PROMPT_DIR = Path(__file__).parent


def load_prompt(name: str, **params: object) -> str:
    """Load an agent system prompt from ``<name>.md`` and fill in any placeholders.

    The file is read and its trailing newline stripped so the returned text matches
    the original inline string literals exactly. When *params* are supplied the text
    is run through :meth:`str.format`, substituting ``{placeholder}`` slots; with no
    *params* the text is returned verbatim, so a static prompt containing literal
    braces is never misinterpreted as a format string.

    Args:
        name:    Prompt file stem, e.g. ``"scout"`` loads ``scout.md``.
        **params: Named values substituted into ``{placeholder}`` slots in the prompt.

    Returns:
        The prompt text, formatted with *params* when any are given.

    Raises:
        FileNotFoundError: If ``<name>.md`` does not exist.
        KeyError:          If the prompt references a placeholder absent from *params*.
    """
    text = (_PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8").rstrip("\n")
    return text.format(**params) if params else text
