---
name: cmux-virtual-ide
description: >-
  Start, reopen, verify, or stop the repo-local virtual IDE (code-server) for the
  duvo-signal-loop repository so the user can paste the URL into a CMUX browser pane.
  Use whenever the user asks for the CMUX IDE, the "virtual IDE", a code-server
  browser pane, opening this repo / a subfolder in CMUX, checking if the IDE is up,
  or shutting it down — including the Czech/Slovak phrasings "spusti virtual IDE",
  "zapni IDE", "otvor IDE", "otvor repo v CMUX", "vypni IDE", "zastav IDE", or the
  invocation $cmux-virtual-ide. Trigger even if they don't say the word "skill":
  any request to launch/open/stop an in-repo browser IDE or get its URL belongs here.
  Runs the bundled scripts/cmux-ide.sh (--start by default) and relays the printed URL.
---

# cmux-virtual-ide

Give CMUX the ability to run a virtual IDE for this repo. This opens the
`duvo-signal-loop` checkout in the user's global local `code-server` instance and
hands back a `http://127.0.0.1:8787/?folder=…` URL they paste into a CMUX browser
pane. A single local code-server instance can open any folder via the `?folder=`
query param, so "start" and "reopen" are the same action: ensure it's up, then
print the URL for the folder they want.

## Procedure

The helper lives next to this skill at
`.claude/skills/cmux-virtual-ide/scripts/cmux-ide.sh`. It derives the repo root from
its own location, so you can run it from anywhere — no `cd` required. It is the
single source of truth for ports, health checks, and process management; prefer it
over launching `code-server` by hand so behavior stays consistent.

1. **Read the intent from `$ARGUMENTS`** (and the surrounding request):
   - No path → target the repo root.
   - A named subdirectory (e.g. `writeback`, `tests`) → pass it after `--start`; the
     URL will open that subfolder.
   - An explicit "stop / shut down / vypni / zastav" → use `--stop`.
   - A "is it running / check / status" → use `--status`.

2. **Start or reopen** (the default action):

   ```bash
   .claude/skills/cmux-virtual-ide/scripts/cmux-ide.sh --start
   ```

   For a subpath:

   ```bash
   .claude/skills/cmux-virtual-ide/scripts/cmux-ide.sh --start writeback
   ```

3. **Relay the printed `IDE URL:` line to the user verbatim.** With no subpath it is:

   ```text
   http://127.0.0.1:8787/?folder=/Users/phantomghost/Desktop/GTM/duvo-signal-loop
   ```

   This is what they paste into the CMUX browser pane.

4. **Stop** — only when explicitly asked:

   ```bash
   .claude/skills/cmux-virtual-ide/scripts/cmux-ide.sh --stop
   ```

5. **Status / verify** — when they just want to know if it's up:

   ```bash
   .claude/skills/cmux-virtual-ide/scripts/cmux-ide.sh --status
   ```

## Notes

- **Don't install or reconfigure `code-server` unless the user explicitly asks.** The
  script passes explicit `--bind-addr 127.0.0.1:8787 --auth none` flags so it works
  even without a config file; an existing `~/.config/code-server/config.yaml` is still
  respected for anything those flags don't set.
- `127.0.0.1` is local-only on the user's Mac, so `auth: none` is acceptable here — the
  IDE is never exposed beyond the machine.
- If the helper reports that `code-server` is **not installed**, relay its exact message
  (it suggests `brew install code-server`) and stop — do not silently install it.
- If it reports that the server **didn't become reachable**, point the user at the log
  the script names (`/tmp/cmux-code-server.log`) rather than guessing.
- The script is idempotent: re-running `--start` when the IDE is already up just
  reprints the URL, so it's safe to call repeatedly.
