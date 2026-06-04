# Langfuse tracing — design spec

**Date:** 2026-06-04
**Status:** Approved (brainstorm), pending implementation plan
**Finding addressed:** #1 (Observability / tracing) from `docs/opus_next_prod_steps.md`. Partially addresses #8 (failure visibility) via error-level spans.

---

## Goal

Add per-account, replayable Langfuse tracing to the `scouts → analyst → router` pipeline:
accurate token usage + cost per model call, a clean per-agent trace tree with distinct
icons, trace-level tags/metadata, batch-run grouping — without breaking the offline test
suite, `--dry-run`, or the codebase's seam rules.

## Research baseline (verified June 2026)

- Langfuse Python SDK is **v4**, **OpenTelemetry-native** (`uv add langfuse`).
- Core primitives used: `Langfuse(...)` client, `langfuse.start_as_current_observation(name=, as_type=, input=, output=, metadata=, model=, model_parameters=, usage_details=, cost_details=, level=, status_message=)` (context manager, auto parent/child via OTel contextvars), `.update(...)`, `propagate_attributes(session_id=, tags=, metadata=, user_id=, trace_name=)`, `set_current_trace_io(...)`, `langfuse.flush()`, `mask=` callback on the client.
- v4 observation types (each renders a distinct icon): `span, generation, event, agent, tool, chain, retriever, embedding, evaluator, guardrail`.
- `asyncio.gather` snapshots the OTel contextvar at task creation → concurrent scouts nest correctly under the account root span.

## Key decisions (locked in brainstorm)

1. **Integration strategy:** manual spans behind the two existing chokepoints (`agent_core.run_agent`, `orchestrator`). Provider-neutral. **No** litellm native callback. Honors "only `litellm_provider.py` imports litellm" and "only `tracing.py` imports langfuse".
2. **Session semantics:** `session_id = run_id` (one uuid per batch run). `domain` also goes in trace metadata so account-over-time can be filtered for free.
3. **On/off gating:** a thin `duvo/infra/tracing.py` switch (init / enabled / span / trace_context / flush). `langfuse` is **lazy-imported inside `init_tracing()`** so disabled runs never import it. Call sites use the SDK directly under the switch (`span()` returns `nullcontext()` when disabled).
4. **Content capture:** full model input/output + tool I/O, with a `mask` callback that redacts email addresses (PII). Best for debugging and as a future eval corpus.
5. **Per-scout isolation:** each scout is its own `agent` subtree (own turns, queries, signals, usage). Distinct sibling branches under the account root.
6. **Typed observations + emoji** for a readable tree (see mapping below).

## Architecture

Instrumentation lives in exactly two chokepoints plus one thin infra module:

- `duvo/infra/tracing.py` (**new**, ~50 lines) — the only file that imports `langfuse` (lazily).
- `duvo/agent_core.py` — wraps the agent loop, each model call, and each tool dispatch.
- `duvo/orchestrator.py` — initializes tracing, creates the batch `run_id`, opens the per-account root trace, applies tags/metadata, flushes on shutdown, marks failed accounts.

The neutral LLM contract carries usage/cost so langfuse never leaks into `agent_core`.

### `duvo/infra/tracing.py` — public API

```python
def init_tracing() -> None
    # If LANGFUSE_ENABLED and keys present: lazily import langfuse, create
    # Langfuse(public_key, secret_key, host, environment=APP_ENV, mask=_mask),
    # cache module-level singleton. Wrapped in try/except → warn, never raise.

def enabled() -> bool

def span(**kwargs)
    # enabled → langfuse.start_as_current_observation(**kwargs)
    # disabled → contextlib.nullcontext()  (yields None)

def trace_context(*, session_id, tags, metadata)
    # enabled → propagate_attributes(session_id=, tags=, metadata=, ...)
    # disabled → contextlib.nullcontext()

def flush() -> None
    # enabled → client.flush(); wrapped in try/except → warn, never raise.

def obs_for(tool_name: str) -> tuple[str, str]
    # returns (as_type, emoji) for a tool name from _TOOL_OBS dict; default ("tool", "🛠️")
```

`nullcontext()` yields `None`, so call sites use `sp and sp.update(...)`. This is a 50-line
on/off switch, **not** a re-abstraction of spans — span calls use the langfuse SDK directly.

### Observation-type + emoji map (dict in `tracing.py`)

```python
_TOOL_OBS = {
    "exa_search":        ("retriever", "🔍"),
    "submit_signals":    ("tool",      "📥"),
    "record_assessment": ("tool",      "📥"),
    "crm_upsert":        ("tool",      "🗂️"),
    "slack_alert":       ("tool",      "💬"),
    "outreach_queue":    ("tool",      "✉️"),
    "finish":            ("tool",      "🏁"),
}
# agents:     as_type="agent"      🔎 scout:<beat> · 🧠 analyst · 🚦 router
# model call: as_type="generation" 💬 <name>:turn-N
# guards:     as_type="guardrail"  🛡️ apply_guards
# root:       as_type="chain"      🎯 account-run
```

### Trace tree (per account)

```
session = run_id (one batch run)
└─ 🎯 account-run                       (chain)     [tags, metadata{domain,country,run_date,dry_run}]
   ├─ 🔎 scout:hiring                    (agent)     metadata{beat, company}
   │  ├─ 💬 scout:hiring:turn-1          (generation) model, input=messages, output, usage_details, cost_details
   │  ├─ 🔍 exa_search  input=query      (retriever)  output=results
   │  └─ 📥 submit_signals               (tool)
   ├─ 🔎 scout:erp_migration             (agent)     ← separate sibling branch (×4 concurrent)
   ├─ 🔎 scout:ma_leadership             (agent)
   ├─ 🔎 scout:pain                      (agent)
   ├─ 🧠 analyst                         (agent)
   │  ├─ 🔍 exa_search                   (retriever)
   │  ├─ 💬 analyst:turn-1               (generation)
   │  ├─ 📥 record_assessment            (tool)
   │  └─ 🛡️ apply_guards                 (guardrail)  status_message = applied rule(s)
   └─ 🚦 router                          (agent)
      ├─ 💬 router:turn-1                (generation)
      ├─ 🗂️ crm_upsert                   (tool)       output=status
      └─ 🏁 finish                       (tool)
```

## Data flow — usage & cost (fixes the discarded `resp.usage`)

- `duvo/llm/base.py`: `LLMResponse` gains `usage: dict[str, int] | None = None` and
  `cost_usd: float | None = None` (provider-neutral).
- `litellm_provider._from_wire_response`: map `resp.usage` →
  `{"input": prompt_tokens, "output": completion_tokens, "total": total_tokens}`; read cost
  from `resp._hidden_params.get("response_cost")`, fallback `litellm.completion_cost(completion_response=resp)`
  inside try/except → `None`. Populate `LLMResponse.usage` / `cost_usd`.
- `agent_core`, after each model call: `gen and gen.update(output=…, usage_details=resp.usage, cost_details={"total": resp.cost_usd})`. Langfuse aggregates cost/tokens per trace & session.

## Changes by file

| File | Change |
|---|---|
| `duvo/infra/tracing.py` | **NEW** — switch + `_mask` (email redaction) + `_TOOL_OBS`/`obs_for`. Lazy `langfuse` import. |
| `duvo/llm/base.py` | `LLMResponse.usage`, `LLMResponse.cost_usd`. |
| `duvo/llm/litellm_provider.py` | Populate usage + cost from `resp` (no langfuse import). |
| `duvo/agent_core.py` | New param `name: str = "agent"`; wrap loop in `agent` span; per-turn `generation` span (set usage/cost); per-tool span typed via `obs_for(tc.name)`; tool error → `level="ERROR"`, `status_message`. |
| `duvo/orchestrator.py` | `init_tracing()` in `run()`; `run_id = uuid4().hex`; `_process_account` opens `trace_context(session_id=run_id, tags, metadata)` + root `span("🎯 account-run", as_type="chain")`; set root I/O (score/tier); failed account → root span `level="ERROR"`; `tracing.flush()` in `run()` `finally` beside `aclose()`. |
| `duvo/agents/scouts/scouts.py` | pass `name="🔎 scout:<beat>"` to `run_agent`; add `metadata` (beat, company) on the scout span (via `run_agent` name; metadata handled inside loop wrapper). |
| `duvo/agents/analyst/analyst.py` | pass `name="🧠 analyst"`; wrap `apply_guards(...)` in a `guardrail` span recording the resulting tier/confidence change. |
| `duvo/agents/router/router.py` | pass `name="🚦 router"`. |
| `duvo/config.py` | `LANGFUSE_ENABLED` (default `false`), `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (default `https://cloud.langfuse.com`), `APP_ENV` (default `dev`). |
| `pyproject.toml` | `uv add langfuse`. |
| `.env.example` | New Langfuse section (sourced, like the rest). |
| `.claude/rules/architecture.md` | Add `tracing.py` to the `infra/` row + a one-line "only tracing.py imports langfuse" boundary note. |

### Tags & metadata

- **tags:** `["duvo-signal-loop", f"model:{LLM_MODEL}", f"env:{APP_ENV}"]` + `"dry-run"` when dry-run.
- **session_id:** `run_id`.
- **trace metadata:** `{domain, country, run_date (today ISO), batch_run_id=run_id, dry_run}`.

## Error handling & resilience

- Tracing must **never** abort a run. `init_tracing` and `flush` are wrapped in try/except → warn.
- A failed account (existing try/except → `None` in `_process_account`) additionally marks its
  root span `level="ERROR"` with the exception message — partially addresses finding #8.
- Tool errors in `agent_core` (already caught → tool-result string) also set the tool span
  `level="ERROR"`.

## Offline / dry-run guarantee

Default `LANGFUSE_ENABLED=false` → `enabled()` is `False` → `span()`/`trace_context()` return
`nullcontext()` and `langfuse` is never imported. The **295 existing tests and `--dry-run` run
unchanged, with no network and no Langfuse keys.**

## Testing

- `tests/test_tracing.py` (**new**): `enabled()` is `False` without env; `span()`/`trace_context()`
  return `nullcontext` when disabled; `init_tracing()` is a no-op without keys; `_mask` redacts
  an email; `obs_for` returns the right `(as_type, emoji)` and a sane default.
- Extend `tests/test_litellm_provider.py`: a mock `resp` with `.usage` and
  `._hidden_params["response_cost"]` yields the correct `LLMResponse.usage` / `cost_usd`; missing
  cost → `None` (no raise).
- Extend `tests/test_agent_core.py`: `run_agent(name=…)` works with tracing disabled; one test
  patches `tracing` so a `MagicMock` client asserts `start_as_current_observation` is called with
  the expected `name`/`as_type` per turn and per tool.

## Out of scope (YAGNI)

- Linking generations to Langfuse-managed prompts (our prompts are Markdown files) — leave a
  metadata hook; revisit with finding #2 (evals).
- Sampling, Langfuse datasets/scores — belong to the evals finding, not here.
- Extending `ToolSpec` with `obs_type`/`emoji` fields — a dict in `tracing.py` is enough; do not
  touch the neutral `llm/base.py` seam for cosmetics.
```
