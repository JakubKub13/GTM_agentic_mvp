# Full-stack E2E test

Run the whole app locally (FastAPI + SQLite + Vite/React SPA in **one process**) and do a
single end-to-end test: sign in → start a run → watch it live → drill into an account.

---

## 1. One-time setup

```bash
uv sync                              # Python deps + venv
(cd frontend && pnpm install)        # SPA deps
```

## 2. Configure `.env`

Append to the repo-root `.env` (auto-loaded by `config.py`):

```bash
SESSION_SECRET=$(openssl rand -hex 32)   # signs the session/CSRF cookies (required)
AUTH_ALLOWED_EMAILS=you@example.com      # who may sign in
AUTH_COOKIE_INSECURE=true                # allow cookies over plain-HTTP localhost
EXA_API_KEY=...                          # real scores: scouts search via Exa
ANTHROPIC_API_KEY=...                    # real scores: the LLM the agents run on
```

> No `EXA`/`ANTHROPIC` keys? The test still works end-to-end — each account just ends
> `failed` instead of scored, and you still verify sign-in, launch, live SSE, and the DB.

## 3. Build the SPA + start the server

```bash
(cd frontend && pnpm build)          # → frontend/dist, served by FastAPI (same-origin)
rm -f state/duvo.db*                  # optional: start from an empty DB (auto-created on boot)
uv run uvicorn duvo.api.app:create_app --factory --port 8000 --workers 1
```

App is now at **http://localhost:8000** (API + SPA). SQLite lives at `state/duvo.db`.

## 4. Sign in (local dev bypass — no Google needed)

Mint a session cookie, then paste it into the browser:

```bash
uv run python -c "import os; from dotenv import load_dotenv; load_dotenv(); \
from itsdangerous import URLSafeTimedSerializer as S; \
print(S(os.environ['SESSION_SECRET'], salt='duvo-session-v1').dumps({'email':'you@example.com','name':'Dev','role':'admin'}))"
```

1. Open `http://localhost:8000` → you get the **Login** screen.
2. DevTools → **Application → Cookies → `http://localhost:8000`**, add two cookies:
   - `duvo_session` = *(the token printed above)*
   - `duvo_csrf` = `dev`
3. **Reload** → you land in the app shell (top nav + user menu).

---

## 5. The E2E test (step by step)

| # | Action | Expected result |
|---|--------|-----------------|
| 1 | On **New Run**, leave **Dry run** ON, leave the file empty (uses repo `companies.csv`), set **Concurrency** = 2, click **Start run**. | Redirects to the live view `/run/<id>`; `201` in the server log. |
| 2 | Watch the **Live Run** view. | Accounts flip `pending → running → done` (or `failed` w/o keys); **ProgressBar** + **ElapsedTimer** advance; the **Tool-call feed** streams `account · agent:beat · tool(args)` lines live. |
| 3 | **Refresh** the page mid-run. | View reloads from the snapshot (statuses + finished accounts' logs), then resumes streaming live. |
| 4 | When a row reaches `done`, **click it**. | Drawer opens: **ScoreCard** (why_fit/why_not), **SignalList**, **OutreachDraftPreview**, **DiffBadge**, **AgentLogViewer**. |
| 5 | Wait for the run to finish. | Header status badge → `done`; the SSE stream closes (no more feed lines). |
| 6 | Verify persistence — in a new terminal: `curl -s -b "duvo_session=<token>" http://localhost:8000/runs \| python3 -m json.tool` | The run appears in `{"runs":[…]}` with `accounts_total`, `accounts_succeeded/failed`. |

**Pass criteria:** the run launches from the UI, accounts update **live** over SSE, the
drill-down shows persisted data, the run reaches a terminal status, and `GET /runs` reflects
it. (Optional: toggle **Dry run OFF** and Start → a **confirm dialog** must appear before the
run launches — the real-run gate.)

## 6. Teardown

```bash
# Ctrl-C the uvicorn process
rm -f state/duvo.db*                  # wipe run history for a fresh start
```

---

### Notes
- **One process, same-origin** — the SPA is served by FastAPI, so cookies + SSE just work.
  (For SPA hot-reload instead: `cd frontend && pnpm dev` on `:5173`, which proxies API calls
  to `:8000`; set the same two cookies on `http://localhost:5173`.)
- **Single API process only** (`--workers 1`): the run registry + live event bus are
  in-process.
- **Automated suites** (separate from this manual test): `uv run pytest -q` and
  `cd frontend && pnpm test`.
