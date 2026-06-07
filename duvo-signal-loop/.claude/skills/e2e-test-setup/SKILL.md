---
name: e2e-test-setup
description: >-
  Bring the duvo-signal-loop full-stack app to the "ready to manually test" state and
  sign the user in via the local dev-login bypass, leaving an authenticated Chrome window
  parked on the New run form. Use whenever the user wants to manually test / try / play with
  the app, "set up the e2e test", "get me to the testing stage", "log me in / sign me in",
  "start the app and authenticate me", "set up everything like before", is stuck on the
  Sign-in-with-Google screen or the cookie/auth dance, or invokes $e2e-test-setup — even if
  they never say the word "skill". It ensures .env, builds the SPA, starts the single-process
  FastAPI server, mints a verified session cookie, and plants it in the browser so the user
  lands on New run ready to click Start run. Also handles status, stop, restart, and fresh-DB.
---

# e2e-test-setup

One command to land the user where manual end-to-end testing begins: the **New run**
form, signed in, with a live server behind it. It automates the whole chain the
`docs/fullstack_e2e_test.md` plan walks through by hand — `.env` → SPA build → server →
dev-login cookie — so the user never has to mint tokens or fiddle with DevTools cookies again.

**Why this exists:** the app gates every route behind a Google-SSO session cookie. Locally
there is no Google, so testing requires a *dev-login bypass* — a session token signed with
the server's `SESSION_SECRET`, planted as the `duvo_session` cookie. The brittle part the
user keeps hitting is the **secret mismatch** (token signed with a secret the running server
didn't load) and the **wrong-window** problem (cookies set in a browser the server can't
see). This skill removes both: the script always mints against the live server's secret and
verifies the round-trip, and Claude plants the cookie in the **Chrome DevTools MCP** window —
the one Claude can actually drive and hand back.

## How the work splits

- **`scripts/e2e-setup.sh`** owns everything deterministic: ensuring `.env`, building the
  SPA, owning the server lifecycle, minting the token, and **verifying it against the live
  `/me`**. It is the single source of truth — prefer it over running `uvicorn`/`openssl`/
  `itsdangerous` by hand so behavior stays consistent. It prints a parseable block.
- **This skill (Claude)** plants the cookie in the browser via the Chrome DevTools MCP — a
  bash script cannot reach the browser — then verifies and hands the window to the user.

## Procedure

### 1. Run the setup script

From anywhere (it finds the repo from its own location):

```bash
.claude/skills/e2e-test-setup/scripts/e2e-setup.sh
```

It streams progress to stderr (`• server: up`, `• token: verified …`) and ends with a block
on stdout. Parse it — you need `BASE_URL` and `TOKEN`:

```text
E2E_SETUP_OK
BASE_URL: http://127.0.0.1:8001
PORT: 8001
VERIFY: 200
TOKEN: .eyJ…
CONSOLE_SNIPPET: document.cookie="duvo_session=…; path=/"; document.cookie="duvo_csrf=dev; path=/"; location.reload();
```

`VERIFY: 200` means the token already round-trips to the live server — auth is guaranteed to
work once the cookie is planted. (`VERIFY: MISMATCH` only appears under `--no-restart`; re-run
without it and the script restarts the server against the current `.env`.)

Map the user's request to flags before running:

| User says | Run |
|---|---|
| "set me up", "let me test", "log me in" (default) | `e2e-setup.sh` |
| "is it up?", "am I signed in?" | `e2e-setup.sh --status` |
| "stop it", "shut the test server down" | `e2e-setup.sh --stop` |
| "restart", "it's acting weird / stale" | `e2e-setup.sh --restart` |
| "fresh / empty run history", "wipe the DB" | `e2e-setup.sh --fresh-db` |
| "use port 8000" | `e2e-setup.sh --port 8000` |
| "rebuild the frontend" | `e2e-setup.sh --build` |

### 2. Plant the cookie in the Chrome DevTools MCP browser

Using the `mcp__chrome-devtools__*` tools, with `BASE_URL` and `TOKEN` from step 1:

1. **Navigate** to `BASE_URL` (`navigate_page`, `type: url`). This is the browser window
   Claude controls and will hand back — *not* the user's personal Chrome.
2. **Set both cookies and verify in one `evaluate_script`** so you confirm auth before
   reloading:

   ```js
   async () => {
     const tok = "<TOKEN>";                       // from the script block
     document.cookie = "duvo_session=" + tok + "; path=/; SameSite=Lax";
     document.cookie = "duvo_csrf=dev; path=/; SameSite=Lax";
     const r = await fetch("/me", { credentials: "same-origin" });
     return { status: r.status, body: await r.json().catch(() => null) };
   }
   ```

   Expect `{ status: 200, body: { email, role: "admin", can_real_run: true } }`. If it is
   `401`, the cookie didn't take or the secret drifted — re-run the script (it self-heals via
   restart) and retry; don't paste a hand-made token.
3. **Reload** (`navigate_page`, `type: reload`) so the SPA re-reads the session.
4. **Confirm the shell rendered** (not the login screen) with a small `evaluate_script`:

   ```js
   () => ({
     loginGone: !document.querySelector('a[href="/auth/login"]'),
     onNewRun: /new run|start run|dry run|concurrency/i.test(document.body.innerText),
   })
   ```

   Both `true` → success.

> Why plant via `document.cookie` even though the real session cookie is `httpOnly`?
> `httpOnly` only stops JS from *reading* it; the server merely needs the value *sent* on
> requests, and a JS-set cookie is sent. This is a dev-only bypass on `127.0.0.1`.

### 3. Hand the window to the user

Tell them plainly: the Chrome window the DevTools MCP opened is authenticated and sitting on
**New run** — switch to it (it shows "Chrome is being controlled by automated test software")
and run the test. Give the first steps so they can go immediately:

- Leave **Dry run ON**, **Concurrency = 2**, leave the CSV empty (uses repo `companies.csv`) →
  click **Start run** → it redirects to `/run/<id>` and accounts stream `pending → running →
  done/failed` live.
- The full matrix (refresh-mid-run, drawer drill-down, persistence check) is in
  `docs/fullstack_e2e_test.md` — point them there rather than restating it.

Leave the webpage to the user for manual testing — don't drive the run yourself unless asked.

## Fallback: no Chrome DevTools MCP available

If the `mcp__chrome-devtools__*` tools aren't connected, don't block — give the user the
self-serve path the script already prepared:

1. Relay `BASE_URL` and tell them to open it in their own browser.
2. Have them open DevTools → **Console**, and paste the `CONSOLE_SNIPPET` line verbatim (it
   sets both cookies and reloads). If Chrome warns about pasting, they type `allow pasting`,
   Enter, then paste again.
3. Verify by visiting `BASE_URL/me` → JSON with `can_real_run` means signed in; `401` means
   the cookie didn't take.

## Notes

- **Idempotent.** Re-running setup when a verifying server is already up just reprints the
  block and re-plants the cookie — safe to call repeatedly.
- **The script may restart the server.** It does so only on a secret mismatch, `--restart`,
  or `--fresh-db` (a new `SESSION_SECRET` invalidates existing sessions). That can interrupt
  an in-flight run; mention it if one was running.
- **Single worker only** (`--workers 1`): the run registry and live SSE event bus are
  in-process, so a second worker wouldn't see them. Don't add workers.
- **The DB is preserved by default.** Only `--fresh-db` wipes `state/duvo.db*`.
- **Never log or paste the `SESSION_SECRET`.** The token embeds an email + role, not the
  secret; treat the secret as sensitive (house rule: never log secrets).
- **Real (non-dry) runs** need an `admin`/`operator` role *and* an explicit confirm dialog;
  the minted token is `admin`, so toggling Dry run OFF will surface the confirm gate (#14).
