#!/usr/bin/env bash
# Set up the full local e2e environment by running both setup scripts:
#   1. setup_clickhouse.sh — download + start a local ClickHouse (left running)
#   2. setup_browser.sh    — install the Playwright browser, build the SPA,
#                            serve it, run e2e
#
# Usage:
#   scripts/setup.sh
#
# All environment overrides documented in the two scripts apply here too
# (CLICKHOUSE_PORT, BACKEND_PORT, EXPECT_CLICKHOUSE_OK, ...).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/setup_clickhouse.sh"
"$ROOT/scripts/setup_browser.sh"
