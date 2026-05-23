#!/usr/bin/env bash
# Run the Astral browser e2e suite locally against a real ClickHouse, the same
# way CI does: ensure a browser, ensure ClickHouse (via setup_clickhouse.sh),
# build the SPA, serve it from the FastAPI backend, and drive a real Chrome.
#
# Usage:
#   scripts/setup_browser.sh
#
# Environment overrides:
#   BACKEND_PORT      backend / BASE_URL port            (default 8000)
#   CLICKHOUSE_PORT   ClickHouse HTTP port               (default 8123)
#   CHROME_PATH       path to a Chrome/Chromium binary   (auto-detected)
#   EXPECT_CLICKHOUSE_OK  assert the connection succeeds (default 1)
#   INSTALL_CHROME=1  apt-install google-chrome if none is found (needs root)
#
# The ClickHouse server is owned by setup_clickhouse.sh and left running; the
# backend this script starts is stopped on exit.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

BACKEND_PORT="${BACKEND_PORT:-8000}"
CLICKHOUSE_PORT="${CLICKHOUSE_PORT:-8123}"
EXPECT_CLICKHOUSE_OK="${EXPECT_CLICKHOUSE_OK:-1}"
CACHE="$ROOT/.cache"
mkdir -p "$CACHE"

BACKEND_PID=""
cleanup() { [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true; }
trap cleanup EXIT

log() { printf '\033[36m[browser]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[browser] error:\033[0m %s\n' "$*" >&2; exit 1; }

wait_for() { # url label
  for _ in $(seq 1 60); do
    curl -sf "$1" >/dev/null 2>&1 && { log "$2 is up"; return 0; }
    sleep 1
  done
  die "$2 did not come up at $1"
}

command -v deno >/dev/null 2>&1 || die "deno not found — install from https://deno.com"
command -v uv >/dev/null 2>&1 || die "uv not found — install from https://docs.astral.sh/uv/"
command -v npm >/dev/null 2>&1 || die "npm not found — install Node.js from https://nodejs.org"

# --- Chrome --------------------------------------------------------------
if [ -z "${CHROME_PATH:-}" ]; then
  for c in google-chrome google-chrome-stable chromium chromium-browser \
           /usr/bin/google-chrome /usr/bin/chromium; do
    if command -v "$c" >/dev/null 2>&1; then CHROME_PATH="$(command -v "$c")"; break; fi
    if [ -x "$c" ]; then CHROME_PATH="$c"; break; fi
  done
fi
if [ -z "${CHROME_PATH:-}" ]; then
  if [ "${INSTALL_CHROME:-}" = "1" ] && [ "$(id -u)" = "0" ] && command -v apt-get >/dev/null 2>&1; then
    log "installing google-chrome-stable via apt"
    curl -sSL -o "$CACHE/chrome.deb" \
      https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
    apt-get update -qq && apt-get install -y -q "$CACHE/chrome.deb"
    CHROME_PATH="/usr/bin/google-chrome"
  else
    die "no Chrome/Chromium found. Set CHROME_PATH=... or run with INSTALL_CHROME=1 (root)."
  fi
fi
log "using Chrome: $CHROME_PATH"

# --- ClickHouse (delegated) ---------------------------------------------
CLICKHOUSE_PORT="$CLICKHOUSE_PORT" "$ROOT/scripts/setup_clickhouse.sh"

# --- Frontend build + backend -------------------------------------------
log "installing frontend deps"
( cd frontend && npm ci )
log "building SPA"
( cd frontend && npm run build )
log "installing backend deps"
uv sync --project "$ROOT/backend" --frozen
log "starting backend (serving built SPA) on :$BACKEND_PORT"
SERVE_STATIC=1 PORT="$BACKEND_PORT" DB_PATH="${DB_PATH:-$CACHE/queryview.db}" \
  uv run --project "$ROOT/backend" --frozen queryview-backend \
  > "$CACHE/backend.log" 2>&1 &
BACKEND_PID=$!
wait_for "http://localhost:$BACKEND_PORT/api/health" "backend"

# --- e2e -----------------------------------------------------------------
log "running Astral e2e tests"
BASE_URL="http://localhost:$BACKEND_PORT" \
CHROME_PATH="$CHROME_PATH" \
EXPECT_CLICKHOUSE_OK="$EXPECT_CLICKHOUSE_OK" \
SCREENSHOT_DIR="${SCREENSHOT_DIR:-$CACHE/screenshots}" \
  deno test -A e2e/

log "done. screenshots in ${SCREENSHOT_DIR:-$CACHE/screenshots}"
