<!-- GENERATED from .claude/rules/ by scripts/build_agents_md.py — do not edit by hand; re-run after changing a rule. -->

# AGENTS.md — duvo-signal-loop house rules

Coding conventions for this repo, generated from `.claude/rules/` so Codex and Claude Code share one source of truth. Edit the rule files, not this one.

# Architecture & house rules

`duvo-signal-loop` is a multi-agent GTM pipeline. Per account:

```
companies.csv → scout_all (4 scouts ∥) → run_analyst (+apply_guards) → run_router → HTML report
```

Orchestrated in `duvo/orchestrator.py`. Read it first when in doubt.

## Where code goes

The package mirrors the `duvo.*` logger tree — one folder per concern:

| Layer | Path | Holds |
|---|---|---|
| Kernel | `duvo/config.py`, `models.py`, `agent_core.py` | env/constants, the Pydantic contract, the one agent runtime |
| Orchestration | `duvo/orchestrator.py` | semaphore-bounded `gather` → report |
| Cross-cutting | `duvo/infra/` | `logging_setup.py`, `http_client.py` |
| Shared agentic tools | `duvo/shared_agentic_tools/` | cross-agent tools such as `exa_tool.py` |
| Agents | `duvo/agents/` | scouts, analyst, router packages with local `prompts/` and `tools/` |
| Write-backs | `duvo/writeback/` | pluggable CRM / Slack / outreach adapters + dispatchers |
| Reporting | `duvo/reporting/` | sync HTML audit report |

Put new code in the folder that owns its concern. Extend an existing module before adding a new one. `main.py` is a thin shim → `duvo.orchestrator:main`; keep it thin.

## Non-negotiables

- **Async-first end-to-end.** Use `asyncio`, fan out with `asyncio.gather`, bound concurrency with `asyncio.Semaphore`, and prefer a library's native async client (Exa via `AsyncExa`, HTTP via `httpx.AsyncClient`) — never block the event loop. For a sync-only lib with no async client, offload via `asyncio.to_thread`. Reporting stays sync (pure CPU/file I/O).
- **One runtime.** Every agent runs on `agent_core.run_agent`. Do not hand-roll a model loop, call an LLM provider outside `duvo/llm/`, or call the Exa client outside its owning module. See the agent conventions.
- **One LLM boundary.** `duvo/llm/` owns all model-provider concerns: `agent_core` and the agents speak only the neutral types in `duvo/llm/base.py`. LiteLLM is a sanctioned dependency, but **only `duvo/llm/litellm_provider.py` may import it** — no wire format (OpenAI/Anthropic) leaks past that file. Add a model provider by dropping `duvo/llm/<name>_provider.py` and `@register_llm("<name>")`.
- **Config via env with defaults** in `config.py`. Guard a key with `require()` only when it is *always* needed before a live network call (Exa; provider-specific LLM keys are validated by the provider); leave write-back keys lazy so `--dry-run` and the offline tests work without a full `.env`.
- **Per-task isolation.** A failing unit (account / scout beat / write-back tool) is logged and degrades to `None` / `[]` / a `failed: …` status — it never aborts the batch. See `_process_account` and `agents/scouts/isolation.py:run_scout_safe`.
- **Bounded agency.** Guarantees live in deterministic code, not prompts (see the agent conventions).

> These rules are guidance Claude reads, like CLAUDE.md — not enforcement. Guaranteed behavior belongs in code, guards, and tests.

# Python style

> Applies when working in `**/*.py`.

Write code that reads like the file next to it — senior, plain, no speculative abstraction.

## Tooling

- **uv only** for dependencies: `uv add 'pkg>=x'`, `uv add --dev …`, `uv lock`, `uv sync`, `uv run …`. Never `pip`.
- Lint & format with **ruff** (config in `pyproject.toml`): line length 100, target `py311`, lints `E,W,F,I,N,UP`. `duvo` is first-party for import sorting. Run `uv run ruff check` / `ruff format` before claiming done.

## Conventions

- **Typing:** built-in generics (`list[str]`, `dict`, `X | None`); `from __future__ import annotations` where it removes quoting. Type every public signature.
- **Docstrings:** Google-style with `Args:` / `Returns:` / `Raises:` on public functions. Match the density already in `config.py:require` and `agent_core.run_agent` — terse private helpers can use a one-liner.
- **Logging:** `from duvo.infra.logging_setup import get_logger` then `_log = get_logger(__name__)`. Use lazy `%`-style args (`_log.info("x=%s", x)`), not f-strings. **Never log secrets** — API keys, tokens, webhook URLs. Logging company/domain/email is fine.

## Idioms to reuse (don't reinvent)

- **Lazy module-level singleton + `require()` at call time** for any external client, so the module imports without secrets and the key is validated only on real use. Canonical: `infra/http_client.py:get_client`, `shared_agentic_tools/exa_tool._get_exa`.
- **Defensive coercion over trust.** Inputs from the LLM or JSON are clamped/coerced before use, with a conservative fallback — never trusted to be in range. Canonical: `agents/analyst/guards.py:score_from_assessment_payload` (score clamp to 1–10, `_conservative_default`).

## Anti-bloat

Prefer extending an existing helper/module over adding a new one. No frameworks, no config layers, no abstractions for a single caller. If it isn't used today, don't build it.

# Agent conventions

> Applies when working in `duvo/agents/**`.

Every agent is the same runtime with a different prompt + toolset. Copy the shape of `scouts.py` / `analyst.py` / `router.py`.

## The runtime

- Drive the agent with `await run_agent(system, user, tools, impls, max_turns=…, final_tools={…}, log=…, max_tokens=…)`. Nothing in `agents/` calls an LLM provider directly — only `agent_core` does, and it speaks the neutral types in `duvo/llm/base.py` (never a wire format).
- **Tools** are `ToolSpec` objects built with `tool_schema(name, description, parameters)` from `duvo.llm.base` (provider-neutral; the provider translates them to wire format). **`impls`** maps tool name → callable (sync or async — `run_agent` auto-awaits awaitables). Reuse `EXA_SEARCH_TOOL` / `exa_search` from `shared_agentic_tools/exa_tool.py` for search.
- Name the terminating tool(s) in `final_tools` (e.g. `submit_signals`, `record_assessment`, `finish`). Capture its payload into a closure dict and read it back after the loop returns — the loop returns the transcript, not the result.
- Set `max_tokens` high enough for large terminal payloads (see `analyst.ANALYST_MAX_TOKENS = 4096`) so the final tool JSON isn't truncated.
- Pass the shared `log` list through so tool calls land in the audit report.

## Prompts are externalized

System prompts live with the agent that owns them: `duvo/agents/<agent>/prompts/<name>.md`, loaded by that package's local prompt loader with `{placeholder}` substitution. **Never inline a multi-line prompt string.** Keep the section layout: `# Role`, `## Objective`, `## Tools`, `## Guidelines`, `## When done`. To add an agent: drop its prompt under the new agent package's `prompts/` folder and expose a small local loader.

## Isolation

Wrap fan-out units so one failure returns `[]` and siblings survive — see `run_scout_safe`. Tolerate sloppy model calls (e.g. `submit_signals()` with no args → treat as "found nothing"), don't raise.

## Determinism over the model

Validation, score clamping, tiering, and safety gates are **plain code, not prompt instructions**:

- `analyst.apply_guards` — undated/thin evidence can't yield a confident high score; tier is derived from the score, overriding the model.
- score clamp to 1–10 + `_conservative_default` when the agent produces nothing usable.
- `router` Tier-1 guard fires **before** the lazy `import` of the send module — a non-confident account can't even load it.

Add any new bounded-agency rule the same way: as code the model cannot talk its way past.

# Write-back adapters

> Applies when working in `duvo/writeback/**`.

One interface, swappable providers. Copy `attio.py` / `brevo.py` for an adapter, `crm.py` / `outreach.py` for a dispatcher.

## Dispatcher pattern (`crm.py`, `outreach.py`)

- Read the provider from `config.<X>_PROVIDER` **at call time** (not import time) so tests can monkeypatch it.
- **Lazy-import** the chosen adapter inside the function: `from duvo.writeback import hubspot`.
- Unknown provider → `log.warning("unknown … — defaulting to <default>")` then fall back to the default adapter. Never raise on a bad provider value.
- Return the adapter's status string unchanged.

## Adapter pattern

- **Share the pooled client**: `client = http_client.get_client()` — one `httpx.AsyncClient`, one connection pool, central `HTTP_TIMEOUT_SECONDS`. Never construct an `AsyncClient` ad hoc.
- **Validate keys at call time** via a `_headers()` (or equivalent) helper using `require("KEY_NAME", config.KEY)`. Never validate at import — the module must import cleanly for `--dry-run` and offline tests.
- Build a payload **dict**, then `await client.post(url, json=payload, headers=_headers())`. Keep provider-specific shaping (Block Kit, Basic auth, search-then-upsert) inside that adapter.
- **Return a short human-readable status string** on success (e.g. `"contact queued in Brevo review list … (not sent — rep reviews & sends)"`).
- **On failure**: `log.error(…)` with company/domain context, then `raise_for_status()` / re-raise. The router catches it and records `failed: …`. Don't swallow.
- **Never log secrets** — keys/tokens go only into headers.

## Boundaries

- `dry_run` is handled at the **router** layer (`agents/router/router.py`), not inside adapters — adapters always do the real call.
- Outreach **queues for review** (Brevo list / paused lemlist campaign); it never auto-sends. Preserve that in any new outreach adapter.

# Testing conventions

> Applies when working in `tests/**`.

The suite is **fully offline** — no API keys, no network, no real model calls. Keep it that way. Test-first for new behavior.

## Layout & config

- Pytest with `asyncio_mode = "auto"` (in `pyproject.toml`): write plain `async def test_…`, **no `@pytest.mark.asyncio`**.
- One `test_<module>.py` per source module (`analyst.py` ↔ `test_analyst.py`).

## Mock at the boundaries

- **Model loop**: patch `duvo.agents.<mod>.run_agent` with an async fake that calls `impls[...]` directly to simulate the model's tool choices — never hit the configured LLM provider. (See the fakes in `test_scouts.py` / `test_router.py`.)
- **Search**: patch `duvo.shared_agentic_tools.exa_tool._get_exa`.
- **HTTP**: patch `duvo.infra.http_client.get_client` with `conftest.make_fake_async_client(...)`; assert on `client.post.call_args_list`.
- **Config**: swap providers/keys with `monkeypatch.setattr(config, "CRM_PROVIDER", …)` — never read real env.

## Reuse conftest factories

`make_score(**kw)`, `make_fake_async_client(post=…, get=…, patch=…)`, `_fake_response(status, json, …)`. Add module-local `_make_company` / `_make_signal` builders the way existing tests do.

## Cover the contract, not just the happy path

- **Safety guards don't mutate state**: a refused `slack_alert` / `outreach_queue` returns a `"refused …"` string *and* leaves `rr.slack_status == "skipped"`.
- **Guard fires before the lazy import** (no `ImportError` when the send module is absent).
- **Failures surface, don't crash**: adapter exception → status `startswith("failed:")`; one failing scout beat / account doesn't sink `scout_all` / the batch.
- **Malformed/partial LLM output** → conservative fallback (score coerced into 1–10, empty `OutreachDraft`, `_conservative_default`).
- **Pure functions** (`apply_guards`) get direct unit tests — no mocks.

Run `uv run pytest -q` and confirm green before claiming done.
