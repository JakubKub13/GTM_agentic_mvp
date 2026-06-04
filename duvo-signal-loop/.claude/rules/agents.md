---
paths:
  - "duvo/agents/**"
---

# Agent conventions

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
