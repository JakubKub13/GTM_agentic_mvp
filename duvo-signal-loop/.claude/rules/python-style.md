---
paths:
  - "**/*.py"
---

# Python style

Write code that reads like the file next to it — senior, plain, no speculative abstraction.

## Tooling

- **uv only** for dependencies: `uv add 'pkg>=x'`, `uv add --dev …`, `uv lock`, `uv sync`, `uv run …`. Never `pip`.
- Lint & format with **ruff** (config in `pyproject.toml`): line length 100, target `py311`, lints `E,W,F,I,N,UP`. `duvo` is first-party for import sorting. Run `uv run ruff check` / `ruff format` before claiming done.

## Conventions

- **Typing:** built-in generics (`list[str]`, `dict`, `X | None`); `from __future__ import annotations` where it removes quoting. Type every public signature.
- **Docstrings:** Google-style with `Args:` / `Returns:` / `Raises:` on public functions. Match the density already in `config.py:require` and `agent_core.run_agent` — terse private helpers can use a one-liner.
- **Logging:** `from duvo.infra.logging_setup import get_logger` then `_log = get_logger(__name__)`. Use lazy `%`-style args (`_log.info("x=%s", x)`), not f-strings. **Never log secrets** — API keys, tokens, webhook URLs. Logging company/domain/email is fine.

## Idioms to reuse (don't reinvent)

- **Lazy module-level singleton + `require()` at call time** for any external client, so the module imports without secrets and the key is validated only on real use. Canonical: `infra/http_client.py:get_client`, `agent_core._get_client`, `tools/exa_tool._get_exa`.
- **Defensive coercion over trust.** Inputs from the LLM or JSON are clamped/coerced before use, with a conservative fallback — never trusted to be in range. Canonical: `agents/analyst.py:run_analyst` (score clamp to 1–10, `_conservative_default`).

## Anti-bloat

Prefer extending an existing helper/module over adding a new one. No frameworks, no config layers, no abstractions for a single caller. If it isn't used today, don't build it.
