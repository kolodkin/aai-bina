# Query feature + seeded `test` DB e2e test — design

## Goal

Add the ability to run a SQL query from the QueryView single-prompt UI and see
its output, then cover it with an e2e test that runs a query against a ClickHouse
database named `test` seeded by the test suite. As part of this, remove the
`EXPECT_CLICKHOUSE_OK` env var so the e2e suite unconditionally exercises a real
ClickHouse.

## UX

After a database is selected, typing `query` reveals a query panel below the
prompt (mirroring how `new clickhouse` reveals the connection form):

- a multi-line `<textarea>` for SQL,
- a **Run** button,
- a results area showing the raw query output as text,
- an inline error line when the query fails.

Typing `query` without a selected database shows a hint instead of the panel.

## Backend

### `clickhouse.py`
- Extend `ch_query(c, query, database=None)` to send `database` as a ClickHouse
  HTTP request param when provided. Existing callers (`SELECT 1`,
  `SHOW DATABASES`) pass no database and are unchanged. SELECTs are read-only, so
  the existing GET path is fine.

### `connect.py`
- Add `run_query(sid, sql)`:
  - no session → `{ok: False, message: "not connected", reason: "no-session"}`
  - no database selected → `{ok: False, message: "select a database first", reason: "no-database"}`
  - otherwise run `ch_query(config, sql, database=selected)`; on failure return
    `{ok: False, message}`, on success `{ok: True, output}` where `output` is the
    raw ClickHouse text.

### `main.py`
- Add `POST /api/clickhouse/query`, body `{query}`:
  - empty/blank query → `400 {ok: False, message: "query required"}`
  - no session → `409`
  - otherwise `{ok: True, output}` or `{ok: False, message}` (status `200`).

## Frontend (`App.tsx`)

- Recognize the `query` command in `submitPrompt`: if `connection?.database` is
  set, `setShowQuery(true)`; otherwise show a hint
  (`Select a database first`). Other commands reset `showQuery`; selecting a
  database resets it too.
- New `QueryPanel` component, rendered when `showQuery` is true:
  - `<textarea data-testid="query-input">`
  - `<button data-testid="query-run">Run</button>`
  - results `<pre data-testid="query-output">` (shown when output present)
  - error `<p data-testid="query-error">` (shown when the request fails)
  - POSTs `{query}` to `/api/clickhouse/query`; renders `output` or `message`.

## Test infrastructure

### `e2e/conftest.py` — `seeded_test_db` fixture (module-scoped)
- Module-level setup/teardown ("module level"):
  - **seed** via ClickHouse HTTP POST:
    - `CREATE DATABASE IF NOT EXISTS test`
    - `CREATE TABLE IF NOT EXISTS test.items (id UInt32, name String) ENGINE = MergeTree ORDER BY id`
    - `INSERT INTO test.items VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')`
  - `yield`
  - **teardown**: `DROP DATABASE IF EXISTS test`
- ClickHouse coordinates from env, defaulting to the form defaults the suite
  already uses: `CLICKHOUSE_HOST` (`localhost`), `CLICKHOUSE_PORT` (`8123`),
  `CLICKHOUSE_USER` (`default`), `CLICKHOUSE_PASSWORD` (empty). Uses a synchronous
  `httpx.Client` (httpx is already a backend dependency).

### `e2e/test_query.py`
- Uses `seeded_test_db`. Flow:
  1. `goto("/")`, type `new clickhouse`, fill the form with the defaults, Connect.
  2. Pick the `test` database in the picker; assert `connected - test`.
  3. Type `query` → assert the query panel is visible.
  4. Fill `query-input` with `SELECT name FROM items ORDER BY id`, click Run.
  5. Assert `query-output` contains `alpha`, `beta`, and `gamma`.

## Removing `EXPECT_CLICKHOUSE_OK`

The env var is removed everywhere; the e2e suite now always requires a real
ClickHouse:

- `e2e/test_app.py` — drop the constant and both conditional blocks; the test
  always runs the full connect → pick database → indicator flow.
- `.github/workflows/ci.yml` — remove the `EXPECT_CLICKHOUSE_OK: "1"` env line
  (keep `BASE_URL`).
- `scripts/setup_browser.sh` — remove the doc-comment line, the default
  assignment, and the line passing it to pytest.
- `scripts/setup.sh` — remove it from the env-overrides comment.
- `docs/connect.md` — reword the CI paragraph so it no longer references the var.

## Out of scope

- The `table <name>` command (the other half of the placeholder) is not part of
  this work.
- Structured/tabular result rendering — output is raw text in a `<pre>` block.
- Query history, multiple result tabs, pagination, write-query support from the
  UI (the endpoint runs over GET, suited to read queries).

## Verification

`scripts/setup_clickhouse.sh` can download and run a local ClickHouse, so the
full e2e flow can be verified locally (build SPA, serve via backend, run pytest)
in addition to backend-only checks and the frontend build/lint.
