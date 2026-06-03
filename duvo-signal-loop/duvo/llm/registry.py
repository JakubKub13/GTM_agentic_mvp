"""LLM provider registry: decorator registration + convention-based lazy lookup.

A provider named ``<name>`` lives in ``duvo/llm/<name>_provider.py`` and
registers itself on import via ``@register_llm("<name>")``. ``get_llm_provider``
imports that module on first use, so no central list is edited to add one.
"""

import importlib
from collections.abc import Callable

from duvo.infra.logging_setup import get_logger
from duvo.llm.base import LLMProvider

_log = get_logger(__name__)

_REGISTRY: dict[str, Callable[[], LLMProvider]] = {}
_DEFAULT = "litellm"


def register_llm(name: str) -> Callable[[Callable[[], LLMProvider]], Callable[[], LLMProvider]]:
    """Register an :class:`LLMProvider` factory (usually the class itself) under *name*."""

    def deco(factory: Callable[[], LLMProvider]) -> Callable[[], LLMProvider]:
        _REGISTRY[name] = factory
        return factory

    return deco


def get_llm_provider(name: str | None = None) -> LLMProvider:
    """Return an instance of the registered provider *name* (default: ``"litellm"``).

    Imports ``duvo.llm.<name>_provider`` on demand so the provider self-registers.
    An unknown/unimportable name logs a warning and falls back to the default.

    Args:
        name: Provider key, or ``None`` to use the default.

    Returns:
        A fresh :class:`LLMProvider` instance.
    """
    name = name or _DEFAULT
    if name not in _REGISTRY:
        try:
            importlib.import_module(f"duvo.llm.{name}_provider")
        except ModuleNotFoundError:
            _log.warning("unknown LLM provider %r — defaulting to %r", name, _DEFAULT)
            name = _DEFAULT
            importlib.import_module(f"duvo.llm.{name}_provider")
    return _REGISTRY[name]()
