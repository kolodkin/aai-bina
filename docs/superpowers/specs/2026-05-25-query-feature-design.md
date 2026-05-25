# Query feature (pagination, predefined queries, CSV) + seeded `test` DB e2e — design

## Goal

Add a SQL query feature to the QueryView single-prompt UI: run a query against
the session's selected database with LIMIT/OFFSET pagination (Previous/Next),
load/save reusable **predefined queries** (stored globally per connection type),
and download the current page as CSV. Cover it with an e2e test that queries a
ClickHouse database named `test` seeded by a module-scoped fixture. Remove the
`EXPECT_CLICKHOUSE_OK` env var so the e2e suite always runs against a real
ClickHouse.

The panel takes structural inspiration from a typical SQL console (predefined-
query selector, sized SQL box, Execute, Limit/Offset, Prev/Next, Download CSV).

## UX

Typing `query` (after a database is selected) reveals a query panel below the
prompt (mirroring how `new clickhouse` reveals the connection form). Typing
`query` with no database selected shows a hint instead. The panel has:

- **Predefined queries** `<select>` — lists saved queries for the active
  connection's type; choosing one loads its SQL into the textarea.
- **Save** — an inline name `<input>` + Save button that stores the current SQL
  as a predefined query (upsert by name+type), then refreshes the selector.
- **SQL textarea** with **S / M / L / XL** toggles that change its row count
  (cosmetic).
- **Execute** — runs the query at offset 0 (resets paging).
- **Limit** (default 100) and **Offset** (default 0) numeric inputs.
- **Previous** (disabled at offset 0) / **Next** — page by ±limit and re-run.
- **Download CSV** — downloads the current page as `query.csv`.
- **Output** — raw text in a `<pre>` (TabSeparatedWithNames, so column headers
  show); an inline error line on failure.

## Backend

### `clickhouse.py`
- `ch_query(c, query, database=None, fmt=None)`: send `database` as an HTTP param
  when given; when `fmt` is given, append `\nFORMAT <fmt>` to the query so the
  same path serves display and CSV. Existing callers unchanged.

### `connect.py` — add a connection `type` and `run_query`
- Add `type: str = Field(default="clickhouse", index=True)` to the `Connection`
  model (stored like the other metadata). Thread it through: `StoredConnection`,
  `_SessionState`, `_row_to_stored`, `_build_session(..., conn_type)`,
  `_save_active_connection(name, c, conn_type="clickhouse")`, and include it in
  the `get_session` / `connect_new` / `open_saved` results. `connect_new` (the
  `/api/clickhouse/*` path) saves type `"clickhouse"`.
- Add `run_query(sid, sql, limit, offset, fmt)`:
  - `await _ensure_session(sid)`, then look up the session entry.
  - no session → `{ok:False, message:"not connected", reason:"no-session"}`
  - no database → `{ok:False, message:"select a database first", reason:"no-database"}`
  - paginate by wrapping: `paginated = f"SELECT * FROM (\n{sql_no_trailing_semicolon}\n) LIMIT {int(limit)} OFFSET {int(offset)}"` (limit/offset are validated ints — no injection).
  - `r = await ch_query(config, paginated, database=selected, fmt=fmt)`; on failure `{ok:False, message}`, else `{ok:True, output}`.

### `queries.py` (new) — predefined-query store
- `PredefinedQuery` SQLModel (`__tablename__ = "predefined_queries"`): `id` (pk),
  `query_name` (indexed), `type` (indexed), `query`; unique on `(type, query_name)`.
- Reuses `connect.py`'s SQLite engine via `from .connect import _engine_for_db, _ensure_schema` (the model registers in the shared `SQLModel.metadata`, so `_ensure_schema`'s `create_all` creates the table).
- `list_predefined_queries(conn_type) -> list[{query_name, query}]` (ordered by name).
- `save_predefined_query(query_name, conn_type, query)` — upsert by `(type, query_name)`.

### `main.py` — endpoints
- `POST /api/clickhouse/query` body `{query, limit?, offset?, format?}`:
  - blank query → `400 {ok:False, message:"query required"}`.
  - `limit`: int, default 100, clamped to `1..1000`; `offset`: int, default 0, `>= 0`. Invalid values fall back to defaults.
  - `format`: `"csv"` → fmt `CSVWithNames`; anything else → fmt `TabSeparatedWithNames`.
  - calls `run_query`; no session → `409`; other failure → `200 {ok:False, message}`; success → `{ok:True, output}`.
- `GET /api/predefined-queries?type=clickhouse` → `{queries: [{query_name, query}]}` (type defaults to `clickhouse`).
- `POST /api/predefined-queries` body `{query_name, type, query}` → validates all three non-empty (else `400`), upserts, returns `{ok:True}`.

Both new routes go before the `/api/{rest:path}` catch-all.

## Frontend (`App.tsx`)

- Extend the `Connection` type with `type: string`; read it from session/connect/
  open responses (default `'clickhouse'`).
- `query` command in `submitPrompt`: if `connection?.database`, `setShowQuery(true)`; else hint `Select a database first.` Other commands and selecting a database reset `showQuery`.
- `QueryPanel` (props: `connectionType: string`), rendered when `showQuery && connection?.database`:
  - On mount and after a save, `GET /api/predefined-queries?type=<connectionType>`; render options.
  - State: `sql`, `limit` (100), `offset` (0), `output`, `error`, `busy`, `rows` (textarea size), `saveName`.
  - `run(nextOffset)`: POST `{query: sql, limit, offset: nextOffset, format: 'text'}`; set output/error; set `offset = nextOffset`.
  - Execute → `run(0)`. Next → `run(offset + limit)`. Previous → `run(Math.max(0, offset - limit))` (button disabled when `offset === 0`).
  - Download CSV → POST `{query: sql, limit, offset, format: 'csv'}`; build a `Blob`, trigger a download named `query.csv`.
  - Save → POST `/api/predefined-queries` `{query_name: saveName, type: connectionType, query: sql}`; on success refresh the selector.
  - Selecting a predefined option sets `sql` to that query's text.
  - Size toggles set `rows` to S=4 / M=8 / L=16 / XL=28.
  - test-ids: `query-panel`, `query-predefined-select`, `query-save-name`, `query-save`, `query-input`, `query-size-s|m|l|xl`, `query-run`, `query-limit`, `query-offset`, `query-prev`, `query-next`, `query-csv`, `query-output`, `query-error`.

## Test infrastructure

### `e2e/conftest.py` — `seeded_test_db` (module-scoped)
- `import httpx`; CH coordinates from env defaulting to the form defaults
  (`CLICKHOUSE_HOST`=`localhost`, `CLICKHOUSE_PORT`=`8123`, `CLICKHOUSE_USER`=`default`, `CLICKHOUSE_PASSWORD`=empty).
- `_ch_exec(sql)` POSTs the statement (writes need POST).
- Fixture: `CREATE DATABASE IF NOT EXISTS test`; `CREATE TABLE IF NOT EXISTS test.items (id UInt32, name String) ENGINE = MergeTree ORDER BY id`; `INSERT (1,'alpha'),(2,'beta'),(3,'gamma')`; `yield`; `DROP DATABASE IF EXISTS test`.

### `e2e/test_query.py`
Uses `seeded_test_db`. One test:
1. Connect (`new clickhouse` → Connect with defaults), select the `test` database (`connected - test`).
2. `query` → panel visible. Fill `query-input` with `SELECT name FROM items ORDER BY id`.
3. Predefined round-trip: fill `query-save-name` = `all items`, click Save; the option `all items` appears in `query-predefined-select`. Clear the textarea, select `all items`, assert the textarea value is restored.
4. Pagination: set `query-limit` = `2`, Execute → output contains `alpha` and `beta`, not `gamma`.
5. CSV: click Download CSV (capture via `page.expect_download`); the file contains `name` (header) and `alpha`.
6. Next → output contains `gamma`, not `alpha`.

## Removing `EXPECT_CLICKHOUSE_OK`

Removed everywhere; the e2e suite always requires a real ClickHouse:
- `e2e/test_app.py` — drop the constant + both conditionals; always run the full flow.
- `.github/workflows/ci.yml` — remove the `EXPECT_CLICKHOUSE_OK: "1"` env line.
- `scripts/setup_browser.sh` — remove the doc-comment, default, and pass-through lines.
- `scripts/setup.sh` — remove from the env-overrides comment.
- `docs/connect.md` — reword the CI paragraph.

## Docs

- `docs/api.md` and `docs/connect.md`: add rows for `/api/clickhouse/query`,
  `GET /api/predefined-queries`, `POST /api/predefined-queries`; note the new
  connection `type`.

## Out of scope

- The `table <name>` command (the other half of the placeholder).
- Structured/tabular HTML rendering (output stays raw text in a `<pre>`).
- Editing/deleting predefined queries; per-user scoping (they are global).
- Stable pagination without an `ORDER BY` (caller's responsibility — the test query orders by id).
- DB migrations for the new `type` column (fresh DBs get it; the dev DB is gitignored).

## Verification

`scripts/setup_clickhouse.sh` can download/run a local ClickHouse, so the full
e2e can run locally (build SPA, serve via backend, run pytest) alongside
backend `TestClient` checks, the predefined-query store check, and frontend
build/lint. If the network policy blocks the ClickHouse/Chromium downloads,
record that the full e2e ran in CI instead.
