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
| Agent tools | `duvo/tools/` | e.g. `exa_tool.py` |
| Agents | `duvo/agents/` (+ `agent_prompts/`) | scouts, analyst, router; prompts as Markdown |
| Write-backs | `duvo/writeback/` | pluggable CRM / Slack / outreach adapters + dispatchers |
| Reporting | `duvo/reporting/` | sync HTML audit report |

Put new code in the folder that owns its concern. Extend an existing module before adding a new one. `main.py` is a thin shim → `duvo.orchestrator:main`; keep it thin.

## Non-negotiables

- **Async-first end-to-end.** Use `asyncio`, fan out with `asyncio.gather`, bound concurrency with `asyncio.Semaphore`, and offload sync-only libs (e.g. `exa-py`) via `asyncio.to_thread` — never block the event loop. Reporting stays sync (pure CPU/file I/O).
- **One runtime.** Every agent runs on `agent_core.run_agent`. Do not hand-roll a model loop or call the Anthropic/Exa client outside its owning module. See the agent conventions.
- **One LLM boundary.** `duvo/llm/` owns all model-provider concerns: `agent_core` and the agents speak only the neutral types in `duvo/llm/base.py`. LiteLLM is a sanctioned dependency, but **only `duvo/llm/litellm_provider.py` may import it** — no wire format (OpenAI/Anthropic) leaks past that file. Add a model provider by dropping `duvo/llm/<name>_provider.py` and `@register_llm("<name>")`.
- **Config via env with defaults** in `config.py`. Guard a key with `require()` only when it is *always* needed (Exa, Anthropic); leave write-back keys lazy so `--dry-run` and the offline tests work without a full `.env`.
- **Per-task isolation.** A failing unit (account / scout beat / write-back tool) is logged and degrades to `None` / `[]` / a `failed: …` status — it never aborts the batch. See `_process_account` and `scouts._run_scout_safe`.
- **Bounded agency.** Guarantees live in deterministic code, not prompts (see the agent conventions).

> These rules are guidance Claude reads, like CLAUDE.md — not enforcement. Guaranteed behavior belongs in code, guards, and tests.
