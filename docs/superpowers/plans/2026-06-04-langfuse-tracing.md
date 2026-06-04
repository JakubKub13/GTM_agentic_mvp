# Langfuse Tracing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-account Langfuse v4 tracing (typed observations, token usage + cost, tags, batch-run grouping, email masking) to the scouts → analyst → router pipeline, gated off by default so the offline test suite and `--dry-run` are unaffected.

**Architecture:** Manual, provider-neutral spans. A thin `duvo/infra/tracing.py` switch is the only file that imports `langfuse` (lazily). **Agent-level spans live in the agent modules** (`scouts.py`/`analyst.py`/`router.py`, wrapping each `run_agent` call) — this avoids changing `run_agent`'s signature, which existing test fakes depend on. **Generation + tool spans live inside `agent_core.run_agent`**. The orchestrator opens the per-account root trace, applies tags/metadata, flushes on shutdown, and marks failed accounts. Usage/cost is carried on the neutral `LLMResponse` so `langfuse` never leaks into `agent_core`.

> **Refinement vs spec:** the spec table said "add a `name` param to `run_agent`". During planning we found the existing `run_agent` test fakes have fixed positional signatures (no `name` kwarg), so passing `name=` would break ~20 tests. Instead the agent span is opened in the agent module that calls `run_agent`. Same trace tree, same per-scout isolation — just a cleaner, test-safe location.

**Tech Stack:** Python 3.11, `langfuse` (v4, OpenTelemetry-native), `litellm`, `pytest` (`asyncio_mode=auto`), `uv`, `ruff`.

**Working directory:** all paths are relative to `duvo-signal-loop/` (the package repo root). Run all commands from there.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `duvo/infra/tracing.py` | On/off switch: init, enabled, `span()`, `trace_context()`, `flush()`, `obs_for()`, `_mask`. Only file importing `langfuse`. | Create |
| `duvo/config.py` | `LANGFUSE_*` + `APP_ENV` env vars. | Modify |
| `duvo/llm/base.py` | `LLMResponse.usage`, `LLMResponse.cost_usd`. | Modify |
| `duvo/llm/litellm_provider.py` | Populate usage + cost from the wire response. | Modify |
| `duvo/agent_core.py` | Generation span per model call; tool span per dispatch (typed via `obs_for`). | Modify |
| `duvo/agents/scouts/scouts.py` | `agent` span around `run_agent`, metadata {beat, company}. | Modify |
| `duvo/agents/analyst/analyst.py` | `agent` span + `guardrail` span around `apply_guards`. | Modify |
| `duvo/agents/router/router.py` | `agent` span around `run_agent`. | Modify |
| `duvo/orchestrator.py` | `init_tracing()`, `run_id`, root `chain` trace + tags, `flush()`, error level on failure. | Modify |
| `.env.example` | Langfuse section. | Modify |
| `.claude/rules/architecture.md` | Note `tracing.py` in the infra row + boundary rule. | Modify |
| `tests/test_config.py` | Defaults for new env vars. | Modify |
| `tests/test_llm_base.py` | New `LLMResponse` fields. | Modify |
| `tests/test_litellm_provider.py` | usage/cost extraction. | Modify |
| `tests/test_tracing.py` | The switch behavior. | Create |
| `tests/test_agent_core.py` | Spans created when enabled; disabled unchanged. | Modify |
| `tests/test_main.py` | `init_tracing`/`flush` invoked. | Modify |

---

## Task 1: Langfuse dependency + config vars

**Files:**
- Modify: `pyproject.toml` (add `langfuse` dependency)
- Modify: `duvo/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: Add the dependency**

Run: `uv add langfuse`
Expected: `pyproject.toml` `dependencies` gains a `langfuse>=3` entry (resolves to v4) and `uv.lock` updates. Expected output ends with `Resolved … Installed …`.

- [ ] **Step 2: Write the failing config test**

Add to the end of `tests/test_config.py`:

```python
class TestLangfuseConfig:
    """Defaults for the Langfuse tracing config (tracing is OFF unless explicitly enabled)."""

    def test_langfuse_enabled_defaults_false(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_ENABLED", raising=False)
        import importlib

        from duvo import config

        importlib.reload(config)
        assert config.LANGFUSE_ENABLED is False

    def test_langfuse_enabled_true_only_for_literal_true(self, monkeypatch):
        monkeypatch.setenv("LANGFUSE_ENABLED", "TRUE")
        import importlib

        from duvo import config

        importlib.reload(config)
        assert config.LANGFUSE_ENABLED is True

    def test_langfuse_host_default(self, monkeypatch):
        monkeypatch.delenv("LANGFUSE_HOST", raising=False)
        import importlib

        from duvo import config

        importlib.reload(config)
        assert config.LANGFUSE_HOST == "https://cloud.langfuse.com"

    def test_app_env_default_is_dev(self, monkeypatch):
        monkeypatch.delenv("APP_ENV", raising=False)
        import importlib

        from duvo import config

        importlib.reload(config)
        assert config.APP_ENV == "dev"

    def test_langfuse_keys_default_empty(self):
        from duvo import config

        assert isinstance(config.LANGFUSE_PUBLIC_KEY, str)
        assert isinstance(config.LANGFUSE_SECRET_KEY, str)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py::TestLangfuseConfig -q`
Expected: FAIL with `AttributeError: module 'duvo.config' has no attribute 'LANGFUSE_ENABLED'`.

- [ ] **Step 4: Add the config vars**

In `duvo/config.py`, after the `LOG_LEVEL = ...` line (currently line 32), add:

```python
# Langfuse tracing (OFF by default — keeps --dry-run and the offline test suite key/network-free).
LANGFUSE_ENABLED = os.environ.get("LANGFUSE_ENABLED", "false").strip().lower() == "true"
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
APP_ENV = os.environ.get("APP_ENV", "dev")  # tags + Langfuse environment label
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py::TestLangfuseConfig -q`
Expected: PASS (5 passed).

- [ ] **Step 6: Add the `.env.example` section**

Append to `.env.example`:

```bash
# ----------------------------------------------------------------------------
#  LANGFUSE — LLM tracing / observability (OPTIONAL; OFF by default).
#  Leave LANGFUSE_ENABLED=false for --dry-run, demos, and the offline test suite.
#
#  Where to get keys:
#    1. Sign up at https://cloud.langfuse.com (free tier; or self-host).
#    2. Create a project → Settings → API Keys → create. Copy the public
#       ("pk-lf-...") and secret ("sk-lf-...") keys.
#    3. Set LANGFUSE_ENABLED=true and paste both keys below.
#  Host: https://cloud.langfuse.com (EU) — use your region/self-host URL if different.
#  Docs: https://langfuse.com/docs/observability/sdk/overview
# ----------------------------------------------------------------------------
LANGFUSE_ENABLED=false
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
LANGFUSE_HOST=https://cloud.langfuse.com

#  APP_ENV — environment label applied to traces (tags + Langfuse environment).
APP_ENV=dev
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock duvo/config.py .env.example tests/test_config.py
git commit -m "feat(tracing): add langfuse dependency + LANGFUSE_*/APP_ENV config"
```

---

## Task 2: `LLMResponse` carries usage + cost

**Files:**
- Modify: `duvo/llm/base.py:70-82` (the `LLMResponse` dataclass)
- Test: `tests/test_llm_base.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_llm_base.py`:

```python
def test_llm_response_usage_and_cost_default_none():
    from duvo.llm.base import LLMResponse, Message

    resp = LLMResponse(message=Message(role="assistant", content="x"), tool_calls=[], stop_reason="stop")
    assert resp.usage is None
    assert resp.cost_usd is None


def test_llm_response_accepts_usage_and_cost():
    from duvo.llm.base import LLMResponse, Message

    resp = LLMResponse(
        message=Message(role="assistant", content="x"),
        tool_calls=[],
        stop_reason="stop",
        usage={"input": 10, "output": 5, "total": 15},
        cost_usd=0.0012,
    )
    assert resp.usage == {"input": 10, "output": 5, "total": 15}
    assert resp.cost_usd == 0.0012
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_llm_base.py -k usage_and_cost -q`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'usage'`.

- [ ] **Step 3: Add the fields**

In `duvo/llm/base.py`, in the `LLMResponse` dataclass, add two fields after `stop_reason: str`:

```python
    message: Message
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict[str, int] | None = None  # {"input","output","total"} token counts, provider-neutral
    cost_usd: float | None = None  # total USD cost for this call, if the provider reports it
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_llm_base.py -k usage_and_cost -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add duvo/llm/base.py tests/test_llm_base.py
git commit -m "feat(tracing): carry usage + cost_usd on neutral LLMResponse"
```

---

## Task 3: LiteLLM provider populates usage + cost

**Files:**
- Modify: `duvo/llm/litellm_provider.py` (the `_from_wire_response` function, lines 77-92)
- Test: `tests/test_litellm_provider.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_litellm_provider.py` (the `_wire_response` helper there builds a `SimpleNamespace` with `choices`; we extend it locally):

```python
async def test_usage_extracted_from_wire_response():
    from types import SimpleNamespace

    resp_obj = _wire_response(content="hi", finish_reason="stop")
    resp_obj.usage = SimpleNamespace(prompt_tokens=100, completion_tokens=40, total_tokens=140)
    resp_obj._hidden_params = {"response_cost": 0.0021}
    req = LLMRequest(model="openai/gpt-4o", system="s", messages=[Message(role="user", content="hi")])
    with patch("duvo.llm.litellm_provider.litellm.acompletion", AsyncMock(return_value=resp_obj)):
        resp = await LiteLLMProvider().complete(req)
    assert resp.usage == {"input": 100, "output": 40, "total": 140}
    assert resp.cost_usd == 0.0021


async def test_missing_usage_and_cost_degrade_to_none():
    resp_obj = _wire_response(content="hi", finish_reason="stop")  # no .usage, no _hidden_params
    req = LLMRequest(model="openai/gpt-4o", system="s", messages=[Message(role="user", content="hi")])
    with patch("duvo.llm.litellm_provider.litellm.acompletion", AsyncMock(return_value=resp_obj)):
        with patch("duvo.llm.litellm_provider.litellm.completion_cost", side_effect=Exception("no cost")):
            resp = await LiteLLMProvider().complete(req)
    assert resp.usage is None
    assert resp.cost_usd is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_litellm_provider.py -k "usage" -q`
Expected: FAIL — `resp.usage` is `None` (field exists but provider doesn't populate it yet), so `test_usage_extracted_from_wire_response` fails on the equality assertion.

- [ ] **Step 3: Implement extraction**

In `duvo/llm/litellm_provider.py`, add two helpers above `_from_wire_response` (after `_parse_arguments`):

```python
def _usage_from_wire(resp: Any) -> dict[str, int] | None:
    """Map a litellm usage object to neutral {"input","output","total"} (None if absent)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return None
    out: dict[str, int] = {}
    pt = getattr(u, "prompt_tokens", None)
    ct = getattr(u, "completion_tokens", None)
    tt = getattr(u, "total_tokens", None)
    if isinstance(pt, int):
        out["input"] = pt
    if isinstance(ct, int):
        out["output"] = ct
    if isinstance(tt, int):
        out["total"] = tt
    return out or None


def _cost_from_wire(resp: Any) -> float | None:
    """Return the call's USD cost from litellm (None if it can't be determined)."""
    hidden = getattr(resp, "_hidden_params", None)
    if isinstance(hidden, dict):
        cost = hidden.get("response_cost")
        if cost is not None:
            try:
                return float(cost)
            except (TypeError, ValueError):
                pass
    try:
        return float(litellm.completion_cost(completion_response=resp))
    except Exception:
        return None
```

Then change the `return LLMResponse(...)` at the end of `_from_wire_response` to:

```python
    return LLMResponse(
        message=assistant,
        tool_calls=tool_calls,
        stop_reason=choice.finish_reason or "",
        usage=_usage_from_wire(resp),
        cost_usd=_cost_from_wire(resp),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_litellm_provider.py -q`
Expected: PASS (all provider tests, including the 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add duvo/llm/litellm_provider.py tests/test_litellm_provider.py
git commit -m "feat(tracing): extract token usage + cost from litellm response"
```

---

## Task 4: `infra/tracing.py` — the on/off switch

**Files:**
- Create: `duvo/infra/tracing.py`
- Test: `tests/test_tracing.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracing.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_tracing.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'duvo.infra.tracing'`.

- [ ] **Step 3: Create the module**

Create `duvo/infra/tracing.py`:

```python
"""Thin Langfuse tracing switch — the only module that imports ``langfuse`` (lazily).

When ``LANGFUSE_ENABLED`` is false or keys are missing, every helper degrades to a
no-op (``nullcontext`` / ``None``) so ``--dry-run`` and the offline test suite never
import ``langfuse`` or touch the network. Span calls in ``agent_core`` / the agents /
the orchestrator use the langfuse SDK directly through ``span`` / ``trace_context`` —
this module is an on/off switch, not a re-abstraction of spans.
"""

import contextlib
import re
from typing import Any

from duvo import config
from duvo.infra.logging_setup import get_logger

_log = get_logger(__name__)

# Module-level singleton. Single-threaded asyncio: check-then-set is atomic enough.
_client: Any | None = None

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# tool name -> (langfuse observation as_type, emoji prefix for the span name)
_TOOL_OBS: dict[str, tuple[str, str]] = {
    "exa_search": ("retriever", "🔍"),
    "submit_signals": ("tool", "📥"),
    "record_assessment": ("tool", "📥"),
    "crm_upsert": ("tool", "🗂️"),
    "slack_alert": ("tool", "💬"),
    "outreach_queue": ("tool", "✉️"),
    "finish": ("tool", "🏁"),
}


def _mask(data: Any) -> Any:
    """Langfuse mask callback: redact email addresses from any captured string I/O."""
    try:
        return _EMAIL_RE.sub("[email]", data) if isinstance(data, str) else data
    except Exception:
        return data


def init_tracing() -> None:
    """Create the Langfuse client if enabled and keys are present; otherwise a no-op.

    Lazily imports ``langfuse`` so disabled runs never import it. Never raises — a
    tracing failure must not abort the pipeline.
    """
    global _client
    if not (config.LANGFUSE_ENABLED and config.LANGFUSE_PUBLIC_KEY and config.LANGFUSE_SECRET_KEY):
        _log.debug("tracing disabled (LANGFUSE_ENABLED/keys not set)")
        _client = None
        return
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=config.LANGFUSE_PUBLIC_KEY,
            secret_key=config.LANGFUSE_SECRET_KEY,
            host=config.LANGFUSE_HOST,
            environment=config.APP_ENV,
            mask=_mask,
        )
        _log.info("Langfuse tracing enabled (host=%s env=%s)", config.LANGFUSE_HOST, config.APP_ENV)
    except Exception as exc:
        _log.warning("failed to init Langfuse tracing: %s — continuing without tracing", exc)
        _client = None


def enabled() -> bool:
    """Return True when a Langfuse client is active."""
    return _client is not None


def obs_for(tool_name: str) -> tuple[str, str]:
    """Return ``(as_type, emoji)`` for a tool name; default ``("tool", "🛠️")``."""
    return _TOOL_OBS.get(tool_name, ("tool", "🛠️"))


def span(**kwargs: Any):
    """Start a current observation when enabled, else a no-op context manager.

    Forwards directly to ``langfuse.start_as_current_observation`` (name, as_type,
    input, metadata, model, level, status_message, …). Yields ``None`` when disabled,
    so call sites guard updates with ``if handle is not None: handle.update(...)``.
    """
    if _client is None:
        return contextlib.nullcontext()
    return _client.start_as_current_observation(**kwargs)


def trace_context(*, session_id: str, tags: list[str], metadata: dict[str, Any]):
    """Propagate trace-level attributes when enabled, else a no-op context manager."""
    if _client is None:
        return contextlib.nullcontext()
    return _client.propagate_attributes(session_id=session_id, tags=tags, metadata=metadata)


def flush() -> None:
    """Flush pending traces before shutdown (never raises)."""
    if _client is None:
        return
    try:
        _client.flush()
    except Exception as exc:
        _log.warning("Langfuse flush failed: %s", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_tracing.py -q`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add duvo/infra/tracing.py tests/test_tracing.py
git commit -m "feat(tracing): thin infra/tracing.py on/off switch (langfuse lazy)"
```

---

## Task 5: Generation + tool spans in `agent_core.run_agent`

**Files:**
- Modify: `duvo/agent_core.py` (the loop body, lines 51-109)
- Test: `tests/test_agent_core.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_agent_core.py` (it already imports `patch` and the conftest fakes). This test patches the `tracing` name *inside* `agent_core` with a recording spy:

```python
from contextlib import contextmanager


class _SpyTracing:
    """Stand-in for duvo.agent_core.tracing that records every span() call."""

    def __init__(self):
        self.spans = []

    def obs_for(self, tool_name):
        return ({"exa_search": "retriever"}.get(tool_name, "tool"), "🔧")

    @contextmanager
    def span(self, **kwargs):
        self.spans.append(kwargs)

        class _Rec:
            def update(self, **kw):
                pass

        yield _Rec()


class TestRunAgentTracing:
    async def test_generation_and_tool_spans_are_created(self):
        provider = FakeLLMProvider(
            [
                make_llm_response(
                    tool_calls=[make_tool_call("exa_search", {"query": "q"}, "tu-1")],
                    stop_reason="tool_calls",
                ),
                make_llm_response(content="final"),
            ]
        )
        spy = _SpyTracing()
        with _patch_provider(provider), patch("duvo.agent_core.tracing", spy):
            from duvo.agent_core import run_agent

            await run_agent("sys", "go", [], impls={"exa_search": lambda query: "r"})

        as_types = [s["as_type"] for s in spy.spans]
        assert "generation" in as_types  # at least one model call span
        # the exa_search tool dispatch became a retriever span with the emoji-prefixed name
        retriever_spans = [s for s in spy.spans if s["as_type"] == "retriever"]
        assert len(retriever_spans) == 1
        assert "exa_search" in retriever_spans[0]["name"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_core.py::TestRunAgentTracing -q`
Expected: FAIL with `AttributeError: <module 'duvo.agent_core'> does not have the attribute 'tracing'` (the module doesn't import `tracing` yet).

- [ ] **Step 3: Instrument `run_agent`**

In `duvo/agent_core.py`, add the import near the top (after the existing `duvo.*` imports, around line 13):

```python
from duvo.infra import tracing
```

Replace the loop body (the `for turn in range(max_turns):` block, lines 55-109) with this instrumented version — the only additions are the `tracing.span(...)` wrappers around the model call and each tool dispatch; all existing logging/append/error behavior is preserved:

```python
    for turn in range(max_turns):
        _log.debug("agent turn %d / %d", turn + 1, max_turns)

        request = LLMRequest(
            model=LLM_MODEL,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )
        with tracing.span(
            name=f"💬 turn-{turn + 1}",
            as_type="generation",
            model=LLM_MODEL,
            model_parameters={"max_tokens": max_tokens},
            input=[{"role": m.role, "content": m.content} for m in messages],
        ) as gen:
            response = await provider.complete(request)
            if gen is not None:
                gen.update(
                    output=response.message.content,
                    usage_details=response.usage,
                    cost_details=({"total": response.cost_usd} if response.cost_usd is not None else None),
                )
        messages.append(response.message)

        if not response.tool_calls:
            _log.debug("loop ended: no tool calls in response (turn %d)", turn + 1)
            return messages

        hit_final = False
        for tc in response.tool_calls:
            _log.info("tool called: %s(%s)", tc.name, _short(tc.arguments))
            if log is not None:
                log.append(f"{tc.name}({_short(tc.arguments)})")

            as_type, emoji = tracing.obs_for(tc.name)
            with tracing.span(
                name=f"{emoji} {tc.name}",
                as_type=as_type,
                input=tc.arguments,
            ) as tsp:
                if tc.name not in impls:
                    _log.warning("model called unknown tool: %s", tc.name)
                    output = f"unknown tool: {tc.name}"
                    if tsp is not None:
                        tsp.update(output=output, level="WARNING")
                else:
                    try:
                        output = impls[tc.name](**tc.arguments)
                        if inspect.isawaitable(output):
                            output = await output
                        if tsp is not None:
                            tsp.update(output=str(output))
                    except Exception as exc:
                        _log.warning("tool %s raised: %s", tc.name, exc)
                        output = f"tool error: {exc}"
                        if tsp is not None:
                            tsp.update(output=output, level="ERROR", status_message=str(exc))

            messages.append(
                Message(
                    role="tool",
                    tool_call_id=tc.id,
                    name=tc.name,
                    content=str(output),
                )
            )
            if tc.name in final:
                hit_final = True

        if hit_final:
            _log.debug("loop ended: final tool reached (turn %d)", turn + 1)
            return messages

    _log.warning(
        "loop ended: max_turns=%d reached without a final tool — transcript may be incomplete",
        max_turns,
    )
    return messages
```

- [ ] **Step 4: Run the new test and the full agent_core suite**

Run: `uv run pytest tests/test_agent_core.py -q`
Expected: PASS (all existing tests + `TestRunAgentTracing`). The existing tests run with tracing disabled (`tracing.span` → `nullcontext`), so behavior is unchanged.

- [ ] **Step 5: Commit**

```bash
git add duvo/agent_core.py tests/test_agent_core.py
git commit -m "feat(tracing): generation + typed tool spans in run_agent"
```

---

## Task 6: Agent-level + guardrail spans in the agent modules

**Files:**
- Modify: `duvo/agents/scouts/scouts.py` (`run_scout`, lines 41-66)
- Modify: `duvo/agents/analyst/analyst.py` (`run_analyst`, lines 46-88)
- Modify: `duvo/agents/router/router.py` (`run_router`, lines 28-53)
- Test: relies on existing `tests/test_scouts.py`, `tests/test_analyst.py`, `tests/test_router.py` staying green (tracing disabled → no behavior change), plus one new analyst guardrail-span test.

- [ ] **Step 1: Write the failing test (analyst guardrail span)**

Add to `tests/test_analyst.py` (it already imports `patch`; add `contextmanager` import at the top of the file if not present: `from contextlib import contextmanager`):

```python
class _SpyTracing:
    def __init__(self):
        self.spans = []

    @contextmanager
    def span(self, **kwargs):
        self.spans.append(kwargs)

        class _Rec:
            def update(self, **kw):
                pass

        yield _Rec()


class TestAnalystGuardrailSpan:
    async def test_apply_guards_wrapped_in_guardrail_span(self):
        company = _make_company()
        signals = [_make_signal()]
        fake = _make_fake_run_agent(VALID_ASSESSMENT)
        spy = _SpyTracing()
        with (
            patch("duvo.agents.analyst.analyst.run_agent", side_effect=fake),
            patch("duvo.agents.analyst.analyst.tracing", spy),
        ):
            await run_analyst(company, signals)
        as_types = [s.get("as_type") for s in spy.spans]
        assert "agent" in as_types  # the 🧠 analyst span
        assert "guardrail" in as_types  # the 🛡️ apply_guards span
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_analyst.py::TestAnalystGuardrailSpan -q`
Expected: FAIL with `AttributeError: <module 'duvo.agents.analyst.analyst'> does not have the attribute 'tracing'`.

- [ ] **Step 3a: Instrument `scouts.py`**

In `duvo/agents/scouts/scouts.py`, add the import after the existing imports (around line 18):

```python
from duvo.infra import tracing
```

Replace the body of `run_scout` from the `impls = {...}` line through `return out` (lines 50-66) with:

```python
    impls = {
        "exa_search": exa_search,
        "submit_signals": make_submit_signals_tool(captured),
    }
    with tracing.span(
        name=f"🔎 scout:{beat_key}",
        as_type="agent",
        input=user,
        metadata={"beat": beat_key, "company": company.domain},
    ) as agent_span:
        await run_agent(
            system,
            user,
            [EXA_SEARCH_TOOL, SUBMIT_SIGNALS_TOOL],
            impls,
            max_turns=MAX_SCOUT_SEARCHES + 2,
            final_tools={"submit_signals"},
            log=log,
        )
        out = signals_from_payload(captured["signals"], beat_key)
        if agent_span is not None:
            agent_span.update(output={"signals": len(out)})

    _log.info("scout done: company=%r beat=%s signals=%d", company.name, beat_key, len(out))
    return out
```

- [ ] **Step 3b: Instrument `analyst.py`**

In `duvo/agents/analyst/analyst.py`, add the import after the existing imports (around line 20):

```python
from duvo.infra import tracing
```

Replace the body from `captured: dict[str, Any] = {}` through the final `return result` (lines 55-88) with:

```python
    captured: dict[str, Any] = {}
    impls = {
        "exa_search": exa_search,
        "record_assessment": make_record_assessment_tool(captured),
    }
    with tracing.span(
        name="🧠 analyst",
        as_type="agent",
        input=user,
        metadata={"company": company.domain, "signals": len(signals)},
    ) as agent_span:
        await run_agent(
            system,
            user,
            [EXA_SEARCH_TOOL, RECORD_ASSESSMENT_TOOL],
            impls,
            max_turns=MAX_ANALYST_SEARCHES + 3,
            final_tools={"record_assessment"},
            log=log,
            max_tokens=ANALYST_MAX_TOKENS,
        )

        if not captured:
            _log.warning(
                "analyst: no record_assessment call from agent for company=%r — using conservative default",
                company.name,
            )
            result = conservative_default(company)
            if agent_span is not None:
                agent_span.update(output={"score": result.score, "tier": result.tier, "fallback": True})
            return result

        score = score_from_assessment_payload(company, captured)
        with tracing.span(
            name="🛡️ apply_guards",
            as_type="guardrail",
            input={
                "score": score.score,
                "tier": score.tier,
                "confidence": score.confidence,
                "needs_human_research": score.needs_human_research,
                "dated_signals": sum(1 for s in signals if s.published_date),
            },
        ) as gspan:
            result = apply_guards(score, signals)
            if gspan is not None:
                gspan.update(
                    output={
                        "score": result.score,
                        "tier": result.tier,
                        "confidence": result.confidence,
                        "needs_human_research": result.needs_human_research,
                    }
                )
        if agent_span is not None:
            agent_span.update(output={"score": result.score, "tier": result.tier})

    _log.info(
        "analyst done: company=%r score=%d tier=%s confidence=%s needs_human_research=%s",
        company.name,
        result.score,
        result.tier,
        result.confidence,
        result.needs_human_research,
    )
    return result
```

- [ ] **Step 3c: Instrument `router.py`**

In `duvo/agents/router/router.py`, add the import after the existing imports (around line 11):

```python
from duvo.infra import tracing
```

Replace the final `await run_agent(...)` line (line 53) with:

```python
    with tracing.span(
        name="🚦 router",
        as_type="agent",
        input=user,
        metadata={"company": s.company_name, "tier": s.tier, "confident_t1": confident_t1},
    ) as agent_span:
        await run_agent(
            ROUTER_SYSTEM, user, tools, impls, max_turns=6, final_tools={"finish"}, log=log
        )
        if agent_span is not None:
            agent_span.update(
                output={
                    "crm": rr.crm_status,
                    "slack": rr.slack_status,
                    "outreach": rr.outreach_status,
                }
            )
```

- [ ] **Step 4: Run the agent test suites**

Run: `uv run pytest tests/test_scouts.py tests/test_analyst.py tests/test_router.py -q`
Expected: PASS (all existing tests + `TestAnalystGuardrailSpan`). Existing tests run with tracing disabled, so the `with tracing.span(...)` wrappers are `nullcontext` no-ops.

- [ ] **Step 5: Commit**

```bash
git add duvo/agents/scouts/scouts.py duvo/agents/analyst/analyst.py duvo/agents/router/router.py tests/test_analyst.py
git commit -m "feat(tracing): agent + guardrail spans in scouts/analyst/router"
```

---

## Task 7: Orchestrator — root trace, tags, init + flush

**Files:**
- Modify: `duvo/orchestrator.py`
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_main.py` inside `class TestRun` (helpers `_base_patches`, `PATCH_BASE` already exist):

```python
    async def test_tracing_init_and_flush_invoked(self):
        from unittest.mock import MagicMock

        from duvo.orchestrator import run

        mocks = _base_patches()
        mock_init = MagicMock()
        mock_flush = MagicMock()
        with (
            patch(f"{PATCH_BASE}.load_companies", mocks["load"]),
            patch(f"{PATCH_BASE}.scout_all", mocks["scout"]),
            patch(f"{PATCH_BASE}.run_analyst", mocks["analyst"]),
            patch(f"{PATCH_BASE}.run_router", mocks["router"]),
            patch(f"{PATCH_BASE}.generate_report", mocks["report"]),
            patch("duvo.infra.http_client.aclose", mocks["aclose"]),
            patch(f"{PATCH_BASE}.tracing.init_tracing", mock_init),
            patch(f"{PATCH_BASE}.tracing.flush", mock_flush),
        ):
            await run(dry_run=False, test_email="test@test.com", limit=None)

        mock_init.assert_called_once()
        mock_flush.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_main.py::TestRun::test_tracing_init_and_flush_invoked -q`
Expected: FAIL with `AttributeError: <module 'duvo.orchestrator'> does not have the attribute 'tracing'`.

- [ ] **Step 3: Wire the orchestrator**

In `duvo/orchestrator.py`:

(a) Update the imports block (lines 3-15). Change `import asyncio` group and the infra import:

```python
import argparse
import asyncio
import csv
from datetime import datetime, timezone
from uuid import uuid4

from duvo import config
from duvo.agents.analyst import run_analyst
from duvo.agents.router import run_router
from duvo.agents.scouts import scout_all
from duvo.config import ACCOUNT_TIMEOUT_SECONDS, LOG_LEVEL, MAX_CONCURRENT_ACCOUNTS, TEST_EMAIL
from duvo.infra import http_client, tracing
from duvo.infra.logging_setup import configure_logging, get_logger
from duvo.models import Company, RunResult
from duvo.reporting.reporter import generate_report
from duvo.shared_agentic_tools import exa_tool
```

(b) Replace the `_process_account` signature and body (lines 31-73) with:

```python
async def _process_account(
    company: Company,
    dry_run: bool,
    test_email: str,
    semaphore: asyncio.Semaphore,
    run_id: str,
    run_date: str,
) -> RunResult | None:
    """Run the full pipeline for a single account inside a semaphore slot.

    Each account is fully isolated: any exception is logged and ``None`` is
    returned so that a single bad account does not abort the whole batch. The
    account is the root of one Langfuse trace (grouped into the batch session).

    Args:
        company:    The account to process.
        dry_run:    When True, write-back tools simulate side-effects only.
        test_email: Outreach recipient override used in dry-run / test mode.
        semaphore:  Bounds the number of accounts processed concurrently.
        run_id:     Batch run id — the Langfuse session all accounts share.
        run_date:   ISO date of the run, recorded in trace metadata.

    Returns:
        A populated :class:`~models.RunResult` on success, or ``None`` on error.
    """
    log = get_logger(__name__)
    async with semaphore:
        tags = ["duvo-signal-loop", f"model:{config.LLM_MODEL}", f"env:{config.APP_ENV}"]
        if dry_run:
            tags.append("dry-run")
        metadata = {
            "domain": company.domain,
            "country": company.country,
            "run_date": run_date,
            "batch_run_id": run_id,
            "dry_run": dry_run,
        }
        with tracing.trace_context(session_id=run_id, tags=tags, metadata=metadata):
            with tracing.span(
                name="🎯 account-run",
                as_type="chain",
                input={"company": company.name, "domain": company.domain},
            ) as root:
                try:
                    async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
                        agent_log: list[str] = []
                        signals = await scout_all(company, agent_log)
                        score = await run_analyst(company, signals, agent_log)
                        rr = RunResult(score=score, signals=signals)
                        await run_router(rr, dry_run, test_email, agent_log)
                        rr.agent_log = agent_log
                        if root is not None:
                            root.update(
                                output={
                                    "score": score.score,
                                    "tier": score.tier,
                                    "confidence": score.confidence,
                                }
                            )
                        log.info(
                            "%d/10 %s conf=%s human=%s (%d signals, %d tool calls)",
                            score.score,
                            score.tier,
                            score.confidence,
                            score.needs_human_research,
                            len(signals),
                            len(agent_log),
                        )
                        return rr
                except Exception as exc:
                    if root is not None:
                        root.update(level="ERROR", status_message=str(exc))
                    log.error("account %s failed: %s", company.name, exc)
                    return None
```

(c) In `run()`, add `tracing.init_tracing()` right after `configure_logging(log_level)` and create the batch identifiers. Replace lines 107-108:

```python
    configure_logging(log_level)
    tracing.init_tracing()
    log = get_logger(__name__)

    run_id = uuid4().hex
    run_date = datetime.now(timezone.utc).date().isoformat()
```

(d) Update the `asyncio.gather` call to pass `run_id` and `run_date` (the `raw_results = await asyncio.gather(...)` block, lines 126-128):

```python
        raw_results = await asyncio.gather(
            *[_process_account(c, dry_run, test_email, sem, run_id, run_date) for c in companies]
        )
```

(e) Add `tracing.flush()` to the `finally` block (after the two `aclose()` calls, line 131):

```python
    finally:
        await http_client.aclose()
        await exa_tool.aclose()
        tracing.flush()
```

- [ ] **Step 4: Run the new test and the full orchestrator suite**

Run: `uv run pytest tests/test_main.py -q`
Expected: PASS (all existing tests + `test_tracing_init_and_flush_invoked`). Existing tests don't enable tracing, so the root span / trace context are `nullcontext` no-ops and the pipeline behaves exactly as before.

- [ ] **Step 5: Commit**

```bash
git add duvo/orchestrator.py tests/test_main.py
git commit -m "feat(tracing): per-account root trace, batch session, tags, init+flush"
```

---

## Task 8: Docs + full verification

**Files:**
- Modify: `.claude/rules/architecture.md`

- [ ] **Step 1: Update the architecture rule**

In `.claude/rules/architecture.md`, in the "Where code goes" table, change the Cross-cutting row to mention tracing:

```markdown
| Cross-cutting | `duvo/infra/` | `logging_setup.py`, `http_client.py`, `retry.py`, `tracing.py` |
```

And add one bullet to the "Non-negotiables" list:

```markdown
- **One tracing boundary.** Only `duvo/infra/tracing.py` may import `langfuse`, and it imports it lazily — tracing is OFF unless `LANGFUSE_ENABLED=true` with keys, so `--dry-run` and the offline tests never import it or hit the network. Instrument through `tracing.span()` / `tracing.trace_context()`; never import `langfuse` elsewhere.
```

- [ ] **Step 2: Run ruff (lint + format)**

Run: `uv run ruff check duvo tests && uv run ruff format duvo tests`
Expected: `All checks passed!` and ruff reports files left unchanged or reformats whitespace only. If `ruff check` reports issues, fix them and re-run until clean.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -q`
Expected: PASS — the previous 295 tests plus the new ones (~18 added across tasks 1-7), all green, no network calls. Confirm the count went up and nothing is skipped/failed.

- [ ] **Step 4: Smoke-test that disabled tracing changes nothing at runtime**

Run: `uv run python -c "from duvo.infra import tracing; print('enabled:', tracing.enabled()); import contextlib; assert isinstance(tracing.span(name='x', as_type='span'), contextlib.nullcontext); print('OK: span is nullcontext when disabled')"`
Expected: prints `enabled: False` and `OK: span is nullcontext when disabled`.

- [ ] **Step 5: Commit**

```bash
git add .claude/rules/architecture.md
git commit -m "docs(tracing): note the langfuse boundary in architecture rules"
```

---

## Manual verification (optional, requires real Langfuse keys)

Not part of the automated suite — do this once to confirm the tree renders as designed:

1. In `.env`: set `LANGFUSE_ENABLED=true`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` (and `EXA_API_KEY` + `ANTHROPIC_API_KEY` for a live run).
2. Run: `uv run python main.py --dry-run --limit 1`
3. Open Langfuse → the session (`run_id`) should contain one `🎯 account-run` trace with nested `🔎 scout:*` (agent), `🔍 exa_search` (retriever), `💬 turn-N` (generation, with tokens + $cost), `🧠 analyst` + `🛡️ apply_guards` (guardrail), and `🚦 router` + its tool spans. Confirm email addresses appear as `[email]`.

---

## Self-Review notes

- **Spec coverage:** every spec section maps to a task — config/dep (T1), `LLMResponse` usage/cost (T2), provider extraction (T3), `infra/tracing.py` + mask + `_TOOL_OBS` (T4), generation/tool spans (T5), agent + guardrail spans + per-scout isolation (T6), orchestrator root trace/tags/session/flush/error-level (T7), docs + offline guarantee + full suite (T8).
- **Deviation from spec (documented):** agent spans live in the agent modules, not via a `run_agent` `name` param — required to keep existing `run_agent` test fakes valid. Trace tree is identical.
- **Type consistency:** `obs_for` returns `(as_type, emoji)` everywhere; `span()`/`trace_context()` yield `None` when disabled and every call site guards with `if … is not None`; `LLMResponse.usage` is `dict|None`, `cost_usd` is `float|None`, consumed as `usage_details=` / `cost_details={"total": …}`.
- **Out of scope (unchanged from spec):** prompt-version linking, sampling, Langfuse datasets — deferred to finding #2 (evals).
```
