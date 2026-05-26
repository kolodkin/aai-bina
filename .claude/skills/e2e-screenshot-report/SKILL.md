---
name: e2e-screenshot-report
description: Use when you want a shareable visual record of QueryView's e2e UI flows — e.g. screenshots of the query panel, field pickers, order-by, or pagination as rendered in a real browser, to review locally or attach to a PR. Triggers on "e2e screenshots", "screenshot report", "show me the UI flows", "index.html report".
---

# e2e-screenshot-report

## Overview

Drives QueryView's e2e UI flows in a headless browser, captures labeled
screenshots, and bundles them into a single **self-contained `index.html`**
(images embedded as base64, so the file stands alone — no sidecar folder needed
to view or share it).

`capture.py` lives next to this file. Its flows mirror `e2e/test_query.py`.

## When to use

- You want to see/share how the query panel renders across a flow.
- You're reviewing a UI change and want before/after visuals.
- You want screenshots to attach to a PR or design review.

Not for: asserting correctness (that's the Playwright suite — `pytest e2e`).

## Runbook

Run every step from the repo root. The app must be built and served, and a
ClickHouse server must be reachable (the script seeds a `test` database itself).

```bash
# 1. Build the SPA (the script screenshots the *built* app served by the backend)
npm ci && npm run build -w frontend

# 2. Ensure ClickHouse is up (downloads a standalone binary the first time)
bash scripts/setup_clickhouse.sh

# 3. Install backend + test deps and the Playwright browser (first run only)
uv sync --frozen --group test
uv run --frozen --group test playwright install chromium

# 4. Start the backend serving the built SPA (background)
SERVE_STATIC=1 PORT=8000 DB_PATH=/tmp/qv-shots.db uv run --frozen queryview-backend &

# 5. Capture + build the report
uv run --frozen --group test python .claude/skills/e2e-screenshot-report/capture.py
```

Output: `/tmp/qv-e2e-report/index.html` (open it, or send it to the user).

Flags: `--base-url` (default `http://localhost:8000`) and `--out` (default
`/tmp/qv-e2e-report`). ClickHouse coords come from `CLICKHOUSE_HOST/PORT/USER/
PASSWORD` (defaults match the connection-form defaults). Run `capture.py --help`.

If serving the Vite dev server instead, point `--base-url http://localhost:5173`.

## Output shape

One self-contained HTML: a sticky **Contents** nav grouped by flow, then one
full-height "page" per screenshot, each labeled with the flow (test) name, the
screenshot name, and its sequence number.

## Keeping it current

The capture flows duplicate the steps in `e2e/test_query.py`. When you add or
change a `data-testid` or a flow there, update `capture.py` to match — otherwise
the report drifts from the real suite. Add a `shot(page, t, "label")` call at any
new step worth picturing.

## Common mistakes

- **`app not reachable`** — the backend/dev server isn't up at `--base-url`, or
  the SPA wasn't rebuilt after a change (you'll screenshot stale UI).
- **`unknown database` / empty results** — ClickHouse isn't running, so seeding
  failed. Run `scripts/setup_clickhouse.sh` first.
- **Browser launch fails** — `playwright install chromium` wasn't run in the uv
  test group.
