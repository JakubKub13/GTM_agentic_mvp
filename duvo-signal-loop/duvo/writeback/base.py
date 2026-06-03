"""Typed contracts every write-back adapter must satisfy.

Adapters expose module-level async functions; these callable Protocols describe
their signatures so mypy can check each adapter against the contract.
"""

from __future__ import annotations

from typing import Protocol

from duvo.models import ICPScore


class CRMProvider(Protocol):
    """An async ``upsert_account(score) -> status string`` callable."""

    async def __call__(self, score: ICPScore) -> str: ...


class OutreachProvider(Protocol):
    """An async ``queue_lead(score, test_email) -> status string`` callable."""

    async def __call__(self, score: ICPScore, test_email: str) -> str: ...
