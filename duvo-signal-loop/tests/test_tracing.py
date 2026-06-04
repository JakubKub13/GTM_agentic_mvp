"""Tests for the tracing switch — disabled by default, never raises, never imports langfuse."""

import contextlib

from duvo.infra import tracing


def test_enabled_is_false_by_default():
    # No client has been initialized in the test process.
    assert tracing.enabled() is False


def test_span_returns_nullcontext_when_disabled():
    cm = tracing.span(name="x", as_type="span")
    assert isinstance(cm, contextlib.nullcontext)
    with cm as handle:
        assert handle is None


def test_trace_context_returns_nullcontext_when_disabled():
    cm = tracing.trace_context(session_id="r1", tags=["t"], metadata={"k": "v"})
    assert isinstance(cm, contextlib.nullcontext)


def test_init_tracing_is_noop_without_keys(monkeypatch):
    from duvo import config

    monkeypatch.setattr(config, "LANGFUSE_ENABLED", True)
    monkeypatch.setattr(config, "LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setattr(config, "LANGFUSE_SECRET_KEY", "")
    tracing.init_tracing()
    assert tracing.enabled() is False


def test_flush_is_noop_when_disabled():
    tracing.flush()  # must not raise


def test_obs_for_known_and_unknown():
    assert tracing.obs_for("exa_search") == ("retriever", "🔍")
    assert tracing.obs_for("crm_upsert") == ("tool", "🗂️")
    assert tracing.obs_for("totally_unknown") == ("tool", "🛠️")


def test_mask_redacts_email():
    assert "[email]" in tracing._mask("contact jakubkubala3+acme-com@gmail.com now")
    assert tracing._mask({"not": "a string"}) == {"not": "a string"}


def test_span_forwards_to_client_when_enabled(monkeypatch):
    class _FakeClient:
        def __init__(self):
            self.calls = []

        def start_as_current_observation(self, **kwargs):
            self.calls.append(kwargs)
            return contextlib.nullcontext("handle")

    fake = _FakeClient()
    monkeypatch.setattr(tracing, "_client", fake)
    with tracing.span(name="x", as_type="span") as handle:
        assert handle == "handle"
    assert fake.calls == [{"name": "x", "as_type": "span"}]


def test_span_degrades_to_nullcontext_if_client_raises(monkeypatch):
    class _BoomClient:
        def start_as_current_observation(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(tracing, "_client", _BoomClient())
    cm = tracing.span(name="x", as_type="span")
    assert isinstance(cm, contextlib.nullcontext)
    with cm as handle:
        assert handle is None


def test_trace_context_uses_module_level_propagate_and_coerces_metadata(monkeypatch):
    import langfuse

    calls = []

    def fake_propagate(**kwargs):
        calls.append(kwargs)
        return contextlib.nullcontext()

    monkeypatch.setattr(langfuse, "propagate_attributes", fake_propagate)
    monkeypatch.setattr(tracing, "_client", object())
    with tracing.trace_context(
        session_id="r1", tags=["env:dev"], metadata={"dry_run": True, "domain": "x.com"}
    ):
        pass
    assert len(calls) == 1
    assert calls[0]["session_id"] == "r1"
    assert calls[0]["tags"] == ["env:dev"]
    assert calls[0]["metadata"] == {"dry_run": "True", "domain": "x.com"}  # str-coerced


def test_trace_context_degrades_if_propagate_raises(monkeypatch):
    import langfuse

    def boom(**kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(langfuse, "propagate_attributes", boom)
    monkeypatch.setattr(tracing, "_client", object())
    cm = tracing.trace_context(session_id="r1", tags=[], metadata={})
    assert isinstance(cm, contextlib.nullcontext)
