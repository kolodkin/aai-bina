---
name: e2e-screenshot-report
description: Use when you want a shareable visual record of QueryView's e2e UI flows — e.g. screenshots of the query panel, field pickers, order-by, or pagination as rendered in a real browser, to review locally or attach to a PR. Triggers on "e2e screenshots", "screenshot report", "show me the UI flows", "index.html report".
---

# e2e-screenshot-report

## Overview

Runs QueryView's real pytest-playwright suite with screenshots enabled, then
bundles the captured PNGs into a single **self-contained `index.html`** (images
embedded as base64, so the file stands alone — no sidecar folder needed to view
or share it).

Two sources of screenshots get picked up automatically:

- **`shot(label)` fixture** (in `e2e/conftest.py`) — call this inside any test
  at points worth picturing. Files land in pytest-playwright's per-test
  `output_path` numbered in call order.
- **`--screenshot=on`** — pytest-playwright's auto end-of-test shot.

`report.py` just walks the `--output` directory; the tests are the source of
truth, so the report can't drift from what's actually tested.

## When to use

- You want to see/share how the query panel renders across a flow.
- You're reviewing a UI change and want before/after visuals.
- You want screenshots to attach to a PR or design review.

Not for: asserting correctness (that's the assertions in the e2e suite).

## Runbook

Run from the repo root. The app must be built and served, and a ClickHouse
server must be reachable (the `seeded_test_db` fixture creates a `test`
database itself).

```bash
# 1. Build the SPA (the suite drives the *built* app served by the backend)
npm ci && npm run build -w frontend

# 2. Ensure ClickHouse is up (downloads a standalone binary the first time)
bash scripts/setup_clickhouse.sh

# 3. Install backend + test deps and the Playwright browser (first run only)
uv sync --frozen --group test
uv run --frozen --group test playwright install chromium

# 4. Start the backend serving the built SPA (background)
SERVE_STATIC=1 PORT=8000 DB_PATH=/tmp/qv-shots.db uv run --frozen queryview-backend &

# 5. Run the e2e suite with screenshots into a known output dir
BASE_URL=http://localhost:8000 uv run --frozen --group test \
    pytest e2e --screenshot=on --output=/tmp/qv-test-results

# 6. Bundle the PNGs into one self-contained HTML
uv run --frozen python .claude/skills/e2e-screenshot-report/report.py \
    --in /tmp/qv-test-results --out /tmp/qv-e2e-report/index.html
```

Output: `/tmp/qv-e2e-report/index.html` (open it, or send it to the user).

Flags: `--in` (pytest-playwright `--output` dir, required) and `--out` (default
`/tmp/qv-e2e-report/index.html`). Run `report.py --help`.

If serving the Vite dev server instead, point `BASE_URL=http://localhost:5173`.

## Output shape

One self-contained HTML: a **Contents** nav grouped by test, then one
full-height "page" per screenshot, each labeled with the test name, the
screenshot label, and its sequence number. Screenshots from the `shot(label)`
fixture come first (in call order); the `--screenshot=on` auto-shot lands last.

## Adding new screenshots

Inside any e2e test, request the `shot` fixture and call it:

```python
def test_my_flow(page, shot):
    page.goto("/")
    shot("landing")
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    shot("query panel open")
```

That's it — `report.py` picks up the new PNGs on the next run.

## Common mistakes

- **`app not reachable`** — the backend/dev server isn't up at `BASE_URL`, or
  the SPA wasn't rebuilt after a change (you'll screenshot stale UI).
- **`unknown database` / empty results** — ClickHouse isn't running, so the
  `seeded_test_db` fixture fails. Run `scripts/setup_clickhouse.sh` first.
- **`no PNGs found`** — pytest ran but neither `shot(...)` nor
  `--screenshot=on` produced anything. Check that `--output` matches `--in`.
- **Browser launch fails** — `playwright install chromium` wasn't run in the
  uv test group.
