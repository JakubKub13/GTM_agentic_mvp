---
paths:
  - "duvo/writeback/**"
---

# Write-back adapters

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
