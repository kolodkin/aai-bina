#!/usr/bin/env bash
# Clone-and-go dev ramp-up: install backend + frontend deps, then run both
# dev servers together.
#
#   1. uv sync      — backend Python deps (idempotent; fast when unchanged)
#   2. npm install  — frontend workspace deps (idempotent)
#   3. npm run dev  — uvicorn :8000 (--reload) + Vite :5173, color-labeled
#
# Usage:
#   scripts/dev.sh
#
# Skip the install steps (just run the servers) with:
#   SKIP_INSTALL=1 scripts/dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${SKIP_INSTALL:-}" != "1" ]]; then
  echo "==> uv sync (backend deps)"
  uv sync
  echo "==> npm install (frontend deps)"
  npm install
fi

echo "==> starting backend (:8000) + frontend (:5173)"
exec npm run dev
