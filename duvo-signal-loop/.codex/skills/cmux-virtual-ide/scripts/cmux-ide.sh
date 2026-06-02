#!/usr/bin/env bash
#
# cmux-ide.sh — start / reopen / stop a repo-local virtual IDE (code-server)
# so the printed URL can be pasted into a CMUX browser pane.
#
# A single global code-server instance on 127.0.0.1:8787 can open ANY folder via
# the ?folder= query param, so "reopen" just means: ensure the instance is up,
# then print the URL pointing at the folder you want.
#
# Usage:
#   cmux-ide.sh --start [SUBPATH]   Start (or reopen) the IDE; print the URL.
#   cmux-ide.sh --stop              Stop the IDE instance on this port.
#   cmux-ide.sh --status            Report whether the IDE is reachable.
#   cmux-ide.sh --help              Show this help.
#
# SUBPATH (optional) is resolved relative to the repository root, so the URL
# opens that subdirectory instead of the whole repo.

set -euo pipefail

HOST="127.0.0.1"
PORT="8787"
BIND="${HOST}:${PORT}"
HEALTH_URL="http://${BIND}/healthz"

# Derive the repo root from this script's own location so nothing is hardcoded:
# .codex/skills/cmux-virtual-ide/scripts/cmux-ide.sh  ->  four levels up = repo root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"

log()  { printf '%s\n' "$*" >&2; }
die()  { log "error: $*"; exit 1; }

is_up() {
  # Reachable if /healthz answers; fall back to a plain root probe.
  curl -fsS -m 2 -o /dev/null "${HEALTH_URL}" 2>/dev/null \
    || curl -fsS -m 2 -o /dev/null "http://${BIND}/" 2>/dev/null
}

resolve_folder() {
  local subpath="${1:-}"
  if [[ -z "${subpath}" ]]; then
    printf '%s' "${REPO_ROOT}"
    return
  fi
  # Allow either an absolute path inside the repo or a path relative to the root.
  local candidate
  if [[ "${subpath}" = /* ]]; then
    candidate="${subpath}"
  else
    candidate="${REPO_ROOT}/${subpath}"
  fi
  [[ -d "${candidate}" ]] || die "subpath not found: ${candidate}"
  ( cd "${candidate}" && pwd )
}

print_url() {
  local folder="$1"
  printf 'IDE URL: http://%s/?folder=%s\n' "${BIND}" "${folder}"
}

start_ide() {
  local folder
  folder="$(resolve_folder "${1:-}")"

  if is_up; then
    log "code-server already running on ${BIND} — reopening folder."
    print_url "${folder}"
    return 0
  fi

  command -v code-server >/dev/null 2>&1 \
    || die "code-server is not installed. Install it (e.g. 'brew install code-server' or https://coder.com/docs/code-server) then re-run."

  log "starting code-server on ${BIND} (serving ${folder}) ..."
  # Explicit flags make this work even without a global config file; an existing
  # ~/.config/code-server/config.yaml is still respected for anything not set here.
  nohup code-server --bind-addr "${BIND}" --auth none "${folder}" \
    >/tmp/cmux-code-server.log 2>&1 &

  # Wait for it to come up (up to ~15s).
  local i
  for i in $(seq 1 30); do
    if is_up; then
      log "code-server is up."
      print_url "${folder}"
      return 0
    fi
    sleep 0.5
  done

  die "code-server did not become reachable on ${BIND}. See /tmp/cmux-code-server.log"
}

stop_ide() {
  if ! is_up && ! lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
    log "code-server is not running on ${BIND} — nothing to stop."
    return 0
  fi
  local pids
  pids="$(lsof -ti "tcp:${PORT}" 2>/dev/null || true)"
  if [[ -z "${pids}" ]]; then
    log "no process is bound to ${PORT}; it may be managed elsewhere."
    return 0
  fi
  log "stopping code-server (pids: ${pids//$'\n'/ }) ..."
  # shellcheck disable=SC2086
  kill ${pids} 2>/dev/null || true
  sleep 1
  if is_up; then
    log "process still reachable; sending SIGKILL."
    # shellcheck disable=SC2086
    kill -9 ${pids} 2>/dev/null || true
  fi
  log "code-server stopped."
}

status_ide() {
  if is_up; then
    log "code-server is UP on ${BIND}."
    print_url "${REPO_ROOT}"
  else
    log "code-server is DOWN on ${BIND}."
    return 1
  fi
}

usage() {
  sed -n '3,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

main() {
  local mode="${1:---start}"
  case "${mode}" in
    --start) shift || true; start_ide "${1:-}" ;;
    --stop)  stop_ide ;;
    --status) status_ide ;;
    -h|--help) usage ;;
    *) die "unknown argument: ${mode} (try --help)" ;;
  esac
}

main "$@"
