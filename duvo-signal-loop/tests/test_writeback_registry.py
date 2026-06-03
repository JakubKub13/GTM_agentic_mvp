"""Tests for the write-back Protocols and registry."""

import logging
from contextlib import contextmanager

from duvo.writeback.base import CRMProvider, OutreachProvider
from tests.conftest import make_score


@contextmanager
def _capture_duvo_logs(level: int = logging.WARNING):
    """Capture log records from the non-propagating 'duvo' logger.

    pytest's caplog cannot intercept records from a logger whose propagate=False.
    Attaches a handler directly to the root 'duvo' logger and yields the collected
    records list so tests can assert on them.
    """
    records: list[logging.LogRecord] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _ListHandler(level)
    duvo_logger = logging.getLogger("duvo")
    duvo_logger.addHandler(handler)
    try:
        yield records
    finally:
        duvo_logger.removeHandler(handler)


async def test_crm_provider_protocol_accepts_matching_callable():
    async def upsert(score):
        return "ok"

    fn: CRMProvider = upsert  # structural typing: must satisfy the Protocol
    assert await fn(make_score()) == "ok"


async def test_outreach_provider_protocol_accepts_matching_callable():
    async def queue(score, test_email):
        return f"queued {test_email}"

    fn: OutreachProvider = queue
    assert await fn(make_score(), "a@b.com") == "queued a@b.com"


from duvo.writeback import registry  # noqa: E402


def test_register_and_get_crm():
    @registry.register_crm("fake_crm")
    async def upsert(score):
        return "fake"

    assert registry.get_crm("fake_crm") is upsert


def test_register_and_get_outreach():
    @registry.register_outreach("fake_out")
    async def queue(score, test_email):
        return "fake"

    assert registry.get_outreach("fake_out") is queue


def test_get_crm_imports_known_adapter_by_convention():
    # "attio" is not pre-imported; the registry must import duvo.writeback.attio,
    # which self-registers, then return its upsert_account.
    fn = registry.get_crm("attio")
    assert callable(fn)


def test_unknown_crm_provider_falls_back_to_default():
    fn = None
    with _capture_duvo_logs(logging.WARNING) as records:
        fn = registry.get_crm("salesforce")  # no such module
    # falls back to the default ("attio") with a warning
    assert callable(fn)
    assert any("unknown" in r.getMessage().lower() for r in records)
