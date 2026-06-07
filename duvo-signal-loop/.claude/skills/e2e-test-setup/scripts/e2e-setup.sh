#!/usr/bin/env bash
#
# e2e-setup.sh — bring the duvo-signal-loop full-stack app to the "ready to
# manually test" state in one shot: ensure .env, build the SPA, start the
# single-process FastAPI server, and mint a verified dev session token.
#
# It is the deterministic source of truth for the E2E test setup. The cookie is
# actually planted in the browser by Claude via the Chrome DevTools MCP (a bash
# script can't reach the browser); this script prints the TOKEN + BASE_URL it
# needs, plus a copy-paste CONSOLE_SNIPPET fallback for a human's own browser.
#
# Usage:
#   e2e-setup.sh                 Setup (default): env + build + server + token, verified.
#   e2e-setup.sh --status        Report server reachability + whether a fresh token verifies.
#   e2e-setup.sh --stop          Stop the server this script started.
#   e2e-setup.sh --restart       Force a clean server restart, then setup.
#   e2e-setup.sh --token         Print only a fresh verified-against-secret token.
#   e2e-setup.sh --help          Show this help.
#
# Flags (combine with the default setup action):
#   --port N        Use port N (default: reuse a running duvo server on 8000/8001, else 8000).
#   --email ADDR    Mint the token for ADDR (default: first AUTH_ALLOWED_EMAILS, else git email).
#   --fresh-db      Wipe state/duvo.db* for an empty run history (implies a restart).
#   --build         Force a SPA rebuild even if frontend/dist exists.
#   --no-restart    Never restart a running server, even on a secret mismatch (just warn).
#
# Idempotent: if a server is already up and a freshly-minted token verifies
# against it, setup reuses it untouched and just reprints the block.

set -euo pipefail

# --------------------------------------------------------------------------- #
# Locate the repo. scripts/ -> e2e-test-setup -> skills -> .claude -> repo root.
# Deriving from $BASH_SOURCE means the script works from any CWD, no cd needed.
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
PIDFILE="${REPO_ROOT}/state/.duvo-e2e-server.pid"

HOST="127.0.0.1"
ACTION="setup"
PORT=""
EMAIL=""
FRESH_DB=0
FORCE_BUILD=0
NO_RESTART=0
FORCE_RESTART=0

log()  { printf '%s\n' "$*" >&2; }
die()  { log "error: $*"; exit 1; }

# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
while [ $# -gt 0 ]; do
  case "$1" in
    --status)     ACTION="status" ;;
    --stop)       ACTION="stop" ;;
    --restart)    FORCE_RESTART=1 ;;
    --token)      ACTION="token" ;;
    --port)       shift; PORT="${1:-}" ;;
    --email)      shift; EMAIL="${1:-}" ;;
    --fresh-db)   FRESH_DB=1 ;;
    --build)      FORCE_BUILD=1 ;;
    --no-restart) NO_RESTART=1 ;;
    --help|-h)
      sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
  shift
done

# --------------------------------------------------------------------------- #
# Probes — talk to a candidate port over /me (the auth endpoint).
#   * A duvo server answers /me with 401 (no cookie) or 200 (valid cookie).
#   * A *verifying* token round-trips to 200 with the email echoed back.
# --------------------------------------------------------------------------- #
http_code() {  # http_code PORT PATH [cookie]
  local port="$1" path="$2" cookie="${3:-}"
  if [ -n "$cookie" ]; then
    curl -s -m 5 -o /dev/null -w '%{http_code}' --cookie "duvo_session=${cookie}" \
      "http://${HOST}:${port}${path}" 2>/dev/null || echo "000"
  else
    curl -s -m 3 -o /dev/null -w '%{http_code}' "http://${HOST}:${port}${path}" 2>/dev/null || echo "000"
  fi
}

is_duvo_server() {  # is_duvo_server PORT  — 401/200 on /me means our app is there
  local code; code="$(http_code "$1" /me)"
  [ "$code" = "401" ] || [ "$code" = "200" ]
}

token_verifies() {  # token_verifies PORT TOKEN — 200 means the secret matches
  [ "$(http_code "$1" /me "$2")" = "200" ]
}

# --------------------------------------------------------------------------- #
# .env — ensure the keys the dev-login bypass needs. Generated once, never logged.
# --------------------------------------------------------------------------- #
ensure_env() {
  [ -f "$ENV_FILE" ] || : > "$ENV_FILE"
  # Guarantee the file ends in a newline so an append starts a fresh line — a
  # missing trailing newline would otherwise glue the new key onto the last value.
  if [ -s "$ENV_FILE" ] && [ "$(tail -c1 "$ENV_FILE")" != "" ]; then
    printf '\n' >> "$ENV_FILE"
  fi
  if ! grep -q '^SESSION_SECRET=' "$ENV_FILE"; then
    printf 'SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" >> "$ENV_FILE"
    log "• .env: generated SESSION_SECRET"
    SECRET_WAS_NEW=1
  fi
  if ! grep -q '^AUTH_ALLOWED_EMAILS=' "$ENV_FILE"; then
    local g; g="$(git -C "$REPO_ROOT" config user.email 2>/dev/null || true)"
    printf 'AUTH_ALLOWED_EMAILS=%s\n' "${g:-dev@example.com}" >> "$ENV_FILE"
    log "• .env: set AUTH_ALLOWED_EMAILS=${g:-dev@example.com}"
  fi
  if ! grep -q '^AUTH_COOKIE_INSECURE=' "$ENV_FILE"; then
    printf 'AUTH_COOKIE_INSECURE=true\n' >> "$ENV_FILE"
    log "• .env: set AUTH_COOKIE_INSECURE=true (plain-HTTP localhost cookies)"
  fi
}

# --------------------------------------------------------------------------- #
# SPA build — FastAPI serves frontend/dist same-origin, so it must exist.
# --------------------------------------------------------------------------- #
ensure_build() {
  local dist="${REPO_ROOT}/frontend/dist/index.html"
  if [ "$FORCE_BUILD" = "0" ] && [ -f "$dist" ]; then return 0; fi
  command -v pnpm >/dev/null 2>&1 || die "pnpm not found — install it, then re-run (the SPA must be built)."
  if [ ! -d "${REPO_ROOT}/frontend/node_modules" ]; then
    log "• frontend: installing deps (pnpm install)…"
    ( cd "${REPO_ROOT}/frontend" && pnpm install ) >&2
  fi
  log "• frontend: building SPA (pnpm build)…"
  ( cd "${REPO_ROOT}/frontend" && pnpm build ) >&2
}

# --------------------------------------------------------------------------- #
# Token — reuse the real code path (duvo.api.auth) so the format never drifts.
# --------------------------------------------------------------------------- #
mint_token() {
  E2E_EMAIL="${EMAIL}" uv run --directory "$REPO_ROOT" python - <<'PY'
import os
from dotenv import load_dotenv; load_dotenv(os.path.join(os.getcwd(), ".env"))
import duvo.api.auth as a
emails = [e.strip() for e in os.environ.get("AUTH_ALLOWED_EMAILS", "").split(",") if e.strip()]
email = os.environ.get("E2E_EMAIL") or (emails[0] if emails else "dev@example.com")
print(a._issue_session(a.User(email=email, name="Dev (e2e)", role="admin")))
PY
}

# --------------------------------------------------------------------------- #
# Server lifecycle
# --------------------------------------------------------------------------- #
pick_port() {
  if [ -n "$PORT" ]; then echo "$PORT"; return; fi
  local p
  for p in 8000 8001; do
    if is_duvo_server "$p"; then echo "$p"; return; fi
  done
  echo "8000"
}

stop_server() {
  local stopped=0
  if [ -f "$PIDFILE" ]; then
    local pid; pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true; stopped=1
    fi
    rm -f "$PIDFILE"
  fi
  # Backstop: kill any uvicorn we recognize for this app.
  pkill -f "uvicorn duvo.api.app:create_app" 2>/dev/null && stopped=1 || true
  [ "$stopped" = "1" ] && log "• server: stopped" || log "• server: nothing to stop"
}

start_server() {  # start_server PORT
  local port="$1" log_file="/tmp/duvo-e2e-server-${1}.log"
  [ "$FRESH_DB" = "1" ] && { rm -f "${REPO_ROOT}/state/"duvo.db* 2>/dev/null || true; log "• db: wiped state/duvo.db*"; }
  mkdir -p "${REPO_ROOT}/state"
  log "• server: starting on ${HOST}:${port} (log: ${log_file})"
  ( cd "$REPO_ROOT" && nohup uv run uvicorn duvo.api.app:create_app --factory \
      --host "$HOST" --port "$port" --workers 1 > "$log_file" 2>&1 & echo $! > "$PIDFILE" )
  # Wait for reachability — first `uv run` may sync deps, so be patient.
  local i
  for i in $(seq 1 60); do
    is_duvo_server "$port" && { log "• server: up"; return 0; }
    sleep 1
  done
  die "server did not become reachable on ${HOST}:${port} — see ${log_file}"
}

# --------------------------------------------------------------------------- #
# Output — a delimited block SKILL.md parses for BASE_URL + TOKEN.
# --------------------------------------------------------------------------- #
emit_block() {  # emit_block PORT TOKEN VERIFY
  local port="$1" token="$2" verify="$3" base="http://${HOST}:$1"
  cat <<EOF
E2E_SETUP_OK
BASE_URL: ${base}
PORT: ${port}
VERIFY: ${verify}
TOKEN: ${token}
CONSOLE_SNIPPET: document.cookie="duvo_session=${token}; path=/"; document.cookie="duvo_csrf=dev; path=/"; location.reload();
EOF
}

# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #
SECRET_WAS_NEW=0

do_setup() {
  ensure_env
  ensure_build
  local port; port="$(pick_port)"

  local running=0
  is_duvo_server "$port" && running=1

  # A brand-new secret invalidates any already-running server's sessions.
  if [ "$running" = "1" ] && { [ "$FORCE_RESTART" = "1" ] || [ "$FRESH_DB" = "1" ] || [ "$SECRET_WAS_NEW" = "1" ]; }; then
    [ "$NO_RESTART" = "1" ] || { stop_server; running=0; }
  fi

  [ "$running" = "1" ] || start_server "$port"

  local token; token="$(mint_token)"

  if ! token_verifies "$port" "$token"; then
    if [ "$NO_RESTART" = "1" ]; then
      log "! token does NOT verify — the running server loaded a different SESSION_SECRET."
      log "  Re-run without --no-restart (or with --restart) to restart it against the current .env."
      emit_block "$port" "$token" "MISMATCH"; return 0
    fi
    log "• secret mismatch with the running server — restarting it against the current .env…"
    stop_server; start_server "$port"
    token="$(mint_token)"
    token_verifies "$port" "$token" || die "token still does not verify after restart — inspect /tmp/duvo-e2e-server-${port}.log"
  fi

  log "• token: verified against ${HOST}:${port}/me (200)"
  emit_block "$port" "$token" "200"
}

do_status() {
  local port; port="$(pick_port)"
  if ! is_duvo_server "$port"; then
    log "server: NOT running on ${HOST}:${port}"; echo "E2E_STATUS: down PORT: ${port}"; return 0
  fi
  ensure_env >/dev/null 2>&1 || true
  local token; token="$(mint_token)"
  if token_verifies "$port" "$token"; then
    log "server: up on ${HOST}:${port}; a fresh token verifies (secret matches)."
    echo "E2E_STATUS: ready PORT: ${port}"
  else
    log "server: up on ${HOST}:${port}, but a fresh token does NOT verify (secret mismatch — run setup to restart)."
    echo "E2E_STATUS: mismatch PORT: ${port}"
  fi
}

case "$ACTION" in
  stop)   stop_server ;;
  status) do_status ;;
  token)  ensure_env >/dev/null 2>&1 || true; mint_token ;;
  setup)  do_setup ;;
esac
