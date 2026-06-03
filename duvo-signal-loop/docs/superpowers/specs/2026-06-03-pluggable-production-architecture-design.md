# Pluggable, production-ready architecture — design

**Date:** 2026-06-03
**Status:** Approved (design); pending implementation plan
**Scope:** MVP → production-ready, with fully pluggable LLM / CRM / outreach seams **plus reliability hardening** (retries, backoff, rate-limit handling, graceful degradation). Persistence, observability, CI/CD, and containerization are **out of scope** (see §7 for what is explicitly deferred and why).

---

## 1. Goal & guiding principle

Convert the MVP into a production-ready app whose **model provider, CRM, and outreach provider are easily swappable** — point at OpenAI, an open-source model (Ollama / vLLM), Anthropic, or a different CRM/outreach vendor by configuration, not by editing dispatch code.

The codebase's spine stays intact:

- **One runtime** — every agent still runs on `agent_core.run_agent` (`duvo/agent_core.py:33`).
- **Bounded agency** — scoring/tiering/sending guarantees stay in deterministic code (`analyst.apply_guards`, the router Tier-1 guard), never in prompts.
- **Async-first** — `asyncio.gather` fan-out, `Semaphore` bound, pooled `httpx` client, timeouts at every layer.
- **Per-unit isolation** — a failing beat/account/write-back degrades to `[]` / `None` / `failed: …`, never aborts the batch.
- **Fully-offline tests** — no API keys, no network. Preserved by mocking at the new boundaries.

We add three **typed, registry-backed seams** (LLM, CRM, outreach) behind the existing boundaries, plus a reliability layer. We do **not** reorganize the project into new top-level packages (`domain/`, `runtime/`, `pipeline/`, `integrations/`), introduce typed settings, or add persistence in this step — that would fight the repo's anti-bloat rule and force a large test migration before the core abstraction is proven.

**One consciously-bent house rule:** LiteLLM becomes a sanctioned dependency at the LLM boundary. Nothing outside `duvo/llm/` imports it. A one-line note is added to `.claude/rules/` recording this.

---

## 2. The LLM seam (provider-neutral)

### Problem

`agent_core.run_agent` is welded to Anthropic — `AsyncAnthropic`, `resp.content` blocks, `tool_use` / `tool_result` shapes (`duvo/agent_core.py:5`, `:76`, `:85`, `:110`) — and the model is a constant `CLAUDE_MODEL = "claude-sonnet-4-6"` (`duvo/config.py:21`). The three agents also hand-write Anthropic-shaped tool dicts with `input_schema` (`scouts.py:25`, `analyst.py:19`, `router.py:_tool_schema`).

### New concern — `duvo/llm/`

```
duvo/llm/
  base.py             # LLMProvider Protocol + NEUTRAL types:
                      #   Message, ToolSpec, ToolCall, LLMRequest, LLMResponse
                      #   + tool_schema(name, desc, params) -> ToolSpec
  registry.py         # @register_llm("litellm") + get_llm_provider(name) — convention-based lazy import
  litellm_provider.py # default LLMProvider; translates neutral types <-> OpenAI/LiteLLM wire format INTERNALLY
```

### Neutral types are the project-wide contract — not OpenAI format

The internal representation is a small set of frozen dataclasses in `duvo/llm/base.py`, **not** OpenAI/LiteLLM wire format. Making OpenAI format the internal contract would leak LiteLLM's shape into `agent_core` and the agents, defeating the seam (swapping LiteLLM for a hand-rolled provider would mean unwinding OpenAI-isms everywhere).

Proposed shapes (final field names settled during implementation):

- `Message` — `role: Literal["system","user","assistant","tool"]`, `content: str`, plus optional fields to carry assistant tool-call requests and tool-result linkage (`tool_calls`, `tool_call_id`).
- `ToolSpec` — `name: str`, `description: str`, `parameters: dict` (JSON-Schema object). Built via the `tool_schema()` helper so agents never hand-write wire format.
- `ToolCall` — `id: str`, `name: str`, `arguments: dict`.
- `LLMRequest` — `system: str`, `messages: list[Message]`, `tools: list[ToolSpec]`, `max_tokens: int`, `model: str`.
- `LLMResponse` — `message: Message` (the assistant turn), `tool_calls: list[ToolCall]`, `stop_reason: str`.

`LLMProvider` is a `typing.Protocol` with a single async method, roughly:

```python
class LLMProvider(Protocol):
    async def complete(self, request: LLMRequest) -> LLMResponse: ...
```

### Rewritten loop

`agent_core.run_agent` keeps its exact public signature and behavior (turn loop, `final_tools`, `log` capture, sync/async `impls` via `inspect.isawaitable`, unknown-tool and tool-error → result string), but speaks **neutral types only**:

1. Build `LLMRequest` (system, `list[Message]`, `list[ToolSpec]`, `max_tokens`, `model`).
2. `response = await provider.complete(request)`.
3. Append `response.message` to the transcript.
4. Dispatch each `response.tool_calls` entry through `impls` exactly as today; append tool results as neutral `Message`s (`role="tool"`).
5. Terminate on a `final_tools` hit, no tool calls, or `max_turns` — unchanged.

OpenAI/LiteLLM wire format lives entirely inside `litellm_provider.py` (neutral → `litellm.acompletion` kwargs; LiteLLM response → neutral). Nothing else imports LiteLLM or knows its shape exists.

### Provider selection

By model string (LiteLLM convention), read from config:

- `LLM_MODEL` — e.g. `"anthropic/claude-sonnet-4-6"`, `"openai/gpt-4o"`, `"ollama/llama3.1"`.
- `LLM_BASE_URL` — optional, for OpenAI-compatible servers (vLLM, local gateways).
- `LLM_PROVIDER` — selects the registered `LLMProvider` implementation; defaults to `"litellm"`. The Protocol is the swap point; LiteLLM is the one default impl. A hand-rolled provider can `@register_llm("custom")` later with **no consumer changes**.

### Registry (convention-based lazy import)

`get_llm_provider(name)` lazily imports `duvo.llm.<provider>_provider` (which self-registers on import via `@register_llm(name)`), then returns the registered factory. Unknown/failed import → `log.warning(...)` + fall back to the default (`litellm`), mirroring the current dispatcher behavior.

### Tool-schema migration

The three agents' tool dicts migrate to the `tool_schema()` helper, emitting `ToolSpec`:

- `scouts.SUBMIT_TOOL` (`scouts.py:25`)
- `analyst.RECORD_TOOL` (`analyst.py:19`)
- `router._tool_schema(...)` (`router.py:52`) and the shared `EXA_SEARCH_TOOL` (`tools/exa_tool.py:29`).

Mechanical, one-time; the JSON-Schema bodies are unchanged.

### Day-one validation

Anthropic (existing behavior preserved), OpenAI, and one OpenAI-compatible local server (Ollama or vLLM) — proving the seam works across hosted and open-source through a single boundary.

---

## 3. The write-back seams (CRM + outreach) — registry + Protocol

### Problem

`crm.upsert_account` (`crm.py:21`) and `outreach.queue_lead` (`outreach.py:22`) are `if/elif` chains — adding a provider means editing the dispatcher (not open-closed).

### New

```
duvo/writeback/
  base.py        # CRMProvider / OutreachProvider Protocols (the contract each adapter satisfies)
  registry.py    # @register_crm("attio") / @register_outreach("brevo") + get_crm(name) / get_outreach(name)
```

- **Protocols** make "what an adapter must implement" explicit and mypy-checkable:
  - `CRMProvider` → `async def upsert_account(score: ICPScore) -> str`
  - `OutreachProvider` → `async def queue_lead(score: ICPScore, test_email: str) -> str`
  - (Return type stays `str` for now; tightens to `WritebackResult` in deferred phase 3.5 — see §7.)
- **Convention-based lazy registration:** `get_crm("hubspot")` imports `duvo.writeback.hubspot` (which `@register_crm`s itself on import), then returns the registered callable. This preserves the current **lazy-import-for-dry-run / offline-test** benefit *and* makes adding a provider a pure drop-in: add `writeback/<name>.py`, decorate it, done — no dispatcher edit.
- **Unknown provider** → `log.warning("unknown … — defaulting to <default>")` then fall back to the default adapter — identical to today's behavior (`crm.py:28-29`, `outreach.py:29-30`). Never raises on a bad provider value.
- The four existing adapters (`attio`, `hubspot`, `brevo`, `lemlist`) gain a one-line decorator and are type-checked against their Protocol. Dispatchers shrink to a registry lookup.
- Provider read **at call time** from `config.<X>_PROVIDER` (unchanged) so tests can monkeypatch it.

### Notification seam — documented, not built

Slack (`writeback/slack.py`) stays concrete. A `NotificationProvider` Protocol + registry following the **same pattern** is documented as the next drop-in extension point (so Teams / Discord / email become additions later), but is **not built now** (YAGNI — not selected for day-one scope).

---

## 4. Reliability hardening

Two retry paths, deliberately **not** unified — model retries and HTTP-adapter retries have different failure shapes.

### LLM calls

Lean on **LiteLLM's built-in retry** (`num_retries`) and rate-limit awareness for transient / 429 errors, configured from `config.LLM_MAX_RETRIES`. On final failure, the existing conservative-default path already catches it: `analyst.run_analyst` returns `_conservative_default(company)` when the agent produces nothing usable (`analyst.py:105`, `:55`), and `_process_account` isolates any account-level exception to `None` (`orchestrator.py:69`). Degradation preserved.

### Write-back HTTP

New `duvo/infra/retry.py` — a small async helper:

```python
async def with_retries(coro_factory, *, max_attempts, ...): ...
```

- Capped **exponential backoff + jitter**.
- Retries only on **transient** errors: HTTP 429, 5xx, `httpx.TimeoutException`, connection errors.
- **Honors `Retry-After`** when present.
- Bounded by `config.HTTP_MAX_RETRIES`.
- A small transient-vs-permanent classifier function decides what's retryable — **no new error-class framework** (house anti-bloat).

Adapters wrap their `client.post(...)` call with `with_retries(...)`. Permanent 4xx still fails fast → the adapter's `raise_for_status()` propagates → the router records `failed: …` as today (`router.py:131`, `:160`). The router's `try/except → "failed: …"` and the per-account isolation already cover graceful degradation around these.

---

## 5. Config — keep flat (minimal churn)

`config.py` stays a flat module of module-level globals + `require()`. We **do not** adopt `pydantic-settings`: the entire offline suite monkeypatches `config.X` (per `.claude/rules/tests.md`), so typed settings would force rewriting those tests for no functional gain, and a "config layer" is what the anti-bloat rule warns against.

New keys added to `config.py` (with sane defaults; `require()` only for keys that are always needed):

| Key | Default | Purpose |
|---|---|---|
| `LLM_MODEL` | `"anthropic/claude-sonnet-4-6"` | LiteLLM model string (provider/model) |
| `LLM_PROVIDER` | `"litellm"` | Which registered `LLMProvider` impl to use |
| `LLM_BASE_URL` | `""` | Optional base URL for OpenAI-compatible servers (vLLM/Ollama) |
| `OPENAI_API_KEY` | `""` | Validated lazily when an OpenAI model is selected |
| `LLM_MAX_RETRIES` | `2` | Passed to LiteLLM `num_retries` |
| `HTTP_MAX_RETRIES` | `3` | Bound for `infra/retry.with_retries` |

`require()` semantics retained: Anthropic/Exa stay strictly required; per-provider keys (OpenAI, write-backs) stay lazy so `--dry-run` and offline tests run without a full `.env`. The existing `CLAUDE_MODEL` constant is superseded by `LLM_MODEL` (Anthropic default preserves current behavior).

`.env.example` and `README` gain the new keys and a "swap the model / CRM / outreach provider" section.

---

## 6. Change map

| Area | Change |
|---|---|
| `duvo/llm/` | **new** — `base.py` (Protocol + neutral types + `tool_schema()`), `registry.py`, `litellm_provider.py` |
| `duvo/agent_core.py` | rewritten loop: provider-driven, neutral types internal; same public signature & behavior |
| `agents/scouts.py`, `analyst.py`, `router.py` | tool dicts migrate to `tool_schema()` → `ToolSpec` |
| `tools/exa_tool.py` | `EXA_SEARCH_TOOL` migrates to `tool_schema()` |
| `duvo/writeback/base.py`, `registry.py` | **new** — Protocols + decorator registry |
| `writeback/crm.py`, `outreach.py` | dispatchers → registry lookup (provider still read at call time) |
| `writeback/attio.py`, `hubspot.py`, `brevo.py`, `lemlist.py` | + `@register_*` decorator, + `with_retries` wrap, type-checked vs Protocol |
| `duvo/infra/retry.py` | **new** — `with_retries` (exp backoff + jitter, transient classifier, Retry-After) |
| `config.py`, `.env.example`, `README.md` | new keys + pluggability docs; `CLAUDE_MODEL` → `LLM_MODEL` |
| `.claude/rules/` | one-line note: LiteLLM sanctioned at the LLM boundary; AGENTS.md regenerated |
| `writeback/slack.py` | **unchanged** (notification seam documented, not built) |
| `tests/` | `test_agent_core` re-pointed to the LLM boundary; new tests for `llm/` (mock `litellm.acompletion`), `infra/retry`, `writeback/registry`; agent/adapter tests largely survive (they patch `run_agent` / `get_client` / `config` provider) |

### Sequencing (each phase independently reviewable + green tests)

1. **LLM seam** — `duvo/llm/` (base + registry + LiteLLM provider), `agent_core` rewrite, tool-schema migration, config keys; validate Anthropic + OpenAI + one local OSS server.
2. **Write-back registry/Protocol** — `writeback/base.py` + `registry.py`; refactor 4 adapters + 2 dispatchers.
3. **Reliability** — `infra/retry.py`, wire LiteLLM `num_retries`, wrap write-back adapters; Retry-After / transient classification.
4. **Config & docs** — finalize `.env.example`, README pluggability section, `.claude/rules/` note.

---

## 7. Deferred phases — recorded, not built now

Out of scope for this MVP→pluggable+reliability lift, but named so the boundary is a deliberate decision rather than an oversight.

### Phase 3.5 — `WritebackResult` instead of bare status strings

Adapters today return human strings like `"contact queued in Brevo review list 7 (not sent — rep reviews & sends)"` (`brevo.py:74`, `attio.py:108`, `hubspot.py:167`, `lemlist.py`). Fine for the HTML report and offline tests; weak for production — no `external_id`, machine-readable `status`, structured `error`, or metadata to act on.

We deliberately **keep strings now** to avoid churning the router (`router.py`) and reporter (`reporter.py`) consumers during the pluggability work. Promote to a typed `WritebackResult` (`status: Literal[...]`, `external_id`, `error`, `meta`) **the first time a real workflow needs the IDs** — e.g. CRM dedup / upsert-on-domain or score-diff alerting. Because the `CRMProvider` / `OutreachProvider` Protocols are introduced now (§3), this becomes a localized change to the Protocol return type + its consumers, not a cross-cutting rewrite.

### Phase 5 — Durable run state, run IDs, idempotency (deploy-time)

`_process_account` (`orchestrator.py:52`) is in-memory only: a failed account becomes `None` and vanishes (`orchestrator.py:69-71`), and re-runs create duplicate CRM records (the README's known limit — no upsert-on-domain). This is a legitimate production gap, but it belongs to **actual deployment**, not the pluggability step.

Recorded as: assign a `run_id` per batch + a stable per-account key; persist per-account outcomes (queued / failed / skipped) to durable storage; make write-backs **idempotent** (upsert-on-domain) so re-runs and score-diff alerting become possible. Explicitly **"phase 5, when we deploy" — not "never."**

---

## 8. Testing strategy (stay fully offline)

- **LLM boundary:** mock `litellm.acompletion` inside `litellm_provider` tests; assert neutral ↔ wire translation both directions. For agent_core, patch the `LLMProvider` (or `get_llm_provider`) with a fake returning canned `LLMResponse`s — never hit a real model.
- **Agents:** existing tests patch `duvo.agents.<mod>.run_agent` with an async fake that calls `impls[...]` directly (`test_router.py:31`, `test_scouts.py`) — these survive the rewrite since they don't touch wire format.
- **Write-back registry:** test drop-in registration, unknown-provider fallback (no `ImportError` leak), and call-time provider read via `monkeypatch.setattr(config, "CRM_PROVIDER", …)`.
- **Retry:** unit-test `with_retries` directly — transient retried with backoff, permanent fails fast, `Retry-After` honored, attempt cap respected. Use a fake clock / patched sleep so tests stay fast and offline.
- **Adapters:** keep `make_fake_async_client(...)` from `conftest`; assert on `client.post.call_args_list`; adapter exception → status `startswith("failed:")`.
- Preserve the existing invariants: refused `slack_alert` / `outreach_queue` leaves status `"skipped"` and the safety guard fires **before** the lazy import; `apply_guards` gets direct unit tests.

Run `uv run pytest -q` and `uv run ruff check` / `ruff format`; confirm green before claiming done.

---

## Appendix A — Verified dependency versions & API contracts (researched 2026-06-03)

Versions and APIs below were confirmed against current documentation on 2026-06-03. The implementation plan builds on these.

### Dependency decisions

| Library | Latest stable (2026-06-03) | Decision for this work |
|---|---|---|
| **LiteLLM** | stable line ~`1.83.x` (`1.88.0.dev1` exists but is a dev build) | **add** `litellm>=1.83,<2`; run `uv add 'litellm>=1.83'` + `uv lock` to pin the exact current stable |
| **anthropic** SDK | — | **drop** the direct dependency — LiteLLM calls the Anthropic REST API via `httpx` and does **not** require the `anthropic` package. Removing it eliminates the only direct Anthropic coupling once `agent_core` is provider-driven |
| pydantic | `2.13.4` | keep `>=2.0` (already compatible; v2 API unchanged for our models) |
| httpx | `0.28.1` | keep `>=0.27` |
| pytest-asyncio | `1.4.0` | keep `>=0.23`; `asyncio_mode = "auto"` remains valid in the 1.x line |
| jinja2 / python-dotenv / exa-py | unchanged | no change |

### LiteLLM call contract (what `litellm_provider.py` translates to/from)

- Import: `from litellm import acompletion`.
- Call: `await acompletion(model, messages, tools=..., tool_choice="auto", max_tokens=..., num_retries=config.LLM_MAX_RETRIES, timeout=config.ANTHROPIC_TIMEOUT_SECONDS, api_base=config.LLM_BASE_URL or None, api_key=<provider key or None>, drop_params=True)`.
  - `drop_params=True` is **required** — open-source / Ollama models reject unsupported OpenAI params otherwise.
  - **System prompt is a `{"role":"system","content":...}` message**, not a separate kwarg (unlike the Anthropic SDK's `system=`).
- Tools wire shape (OpenAI function format): `{"type":"function","function":{"name","description","parameters":<JSON-Schema object>}}`. The neutral `ToolSpec` → this dict happens inside the provider.
- Response shape: `resp.choices[0].message` (assistant turn); `resp.choices[0].finish_reason` (e.g. `"tool_calls"`); `message.tool_calls[i]` → `.id`, `.type == "function"`, `.function.name`, `.function.arguments` (**a JSON string**).
  - `function.arguments` must be parsed with `json.loads` **defensively** — LiteLLM docs warn the JSON may be invalid; on parse failure, degrade (treat as `{}` / surface a tool-error result) rather than raising, consistent with the codebase's "coerce, don't trust" rule.
- Round-trip: re-append the assistant message **including its `tool_calls`**, then one `{"role":"tool","tool_call_id":...,"name":...,"content":str}` per result. The neutral `Message` carries `tool_calls` / `tool_call_id` so the provider can serialize this correctly; `agent_core` only passes neutral `Message`s.

### Model-string routing (provider selection by `LLM_MODEL`)

| Target | `LLM_MODEL` | `LLM_BASE_URL` | Key |
|---|---|---|---|
| Anthropic (default, preserves current behavior) | `anthropic/claude-sonnet-4-6` | — | `ANTHROPIC_API_KEY` |
| OpenAI | `openai/gpt-4o` (or current) | — | `OPENAI_API_KEY` |
| vLLM / OpenAI-compatible server | `openai/<served-model-name>` | `http://host:8000/v1` | dummy/none |
| Ollama (local OSS) | `ollama_chat/llama3.1` | `http://localhost:11434` | none |

### Risk to verify during implementation

- **`num_retries` on the async path:** the input-params docs confirm `num_retries` for `completion()` but don't *explicitly* confirm it for `acompletion()`, and there is prior bug history (BerriAI/litellm#12830). **Plan must include a test that asserts LLM-level retries actually fire** against a mocked `acompletion` that raises a transient error then succeeds. If `num_retries` proves unreliable on the pinned version, fall back to wrapping the provider call in our own retry (reuse the transient classifier from `infra/retry.py`) or a LiteLLM `Router`. This keeps reliability guaranteed in our code, not assumed from the dependency — consistent with the bounded-agency philosophy.
