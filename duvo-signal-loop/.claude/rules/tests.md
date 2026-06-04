---
paths:
  - "tests/**"
---

# Testing conventions

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
