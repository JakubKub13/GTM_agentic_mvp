"""Write-back provider registry: decorator registration + convention-based lookup.

A provider named ``<name>`` lives in ``duvo/writeback/<name>.py`` and registers
its function on import via ``@register_crm("<name>")`` / ``@register_outreach``.
``get_crm`` / ``get_outreach`` import that module on demand, so adding a provider
is a drop-in: create the file, decorate the function, done.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

_CRM: dict[str, Callable] = {}
_OUTREACH: dict[str, Callable] = {}

_PKG = "duvo.writeback"
_CRM_DEFAULT = "attio"
_OUTREACH_DEFAULT = "brevo"


def register_crm(name: str) -> Callable[[Callable], Callable]:
    """Register a CRM ``upsert_account`` function under *name*."""

    def deco(fn: Callable) -> Callable:
        _CRM[name] = fn
        return fn

    return deco


def register_outreach(name: str) -> Callable[[Callable], Callable]:
    """Register an outreach ``queue_lead`` function under *name*."""

    def deco(fn: Callable) -> Callable:
        _OUTREACH[name] = fn
        return fn

    return deco


def _lookup(reg: dict[str, Callable], name: str, default: str) -> Callable:
    """Resolve *name* to a registered callable, importing its module by convention.

    Unknown or non-registering modules log a warning and fall back to *default*.
    """
    if name not in reg:
        try:
            importlib.import_module(f"{_PKG}.{name}")
        except ModuleNotFoundError:
            _log.warning("unknown write-back provider %r — defaulting to %r", name, default)
            name = default
        if name not in reg:
            importlib.import_module(f"{_PKG}.{name}")
    if name not in reg:  # module imported but didn't register under this name
        _log.warning("provider %r did not register — defaulting to %r", name, default)
        importlib.import_module(f"{_PKG}.{default}")
        name = default
    return reg[name]


def get_crm(name: str) -> Callable:
    """Return the registered CRM ``upsert_account`` callable for *name*."""
    return _lookup(_CRM, name, _CRM_DEFAULT)


def get_outreach(name: str) -> Callable:
    """Return the registered outreach ``queue_lead`` callable for *name*."""
    return _lookup(_OUTREACH, name, _OUTREACH_DEFAULT)
