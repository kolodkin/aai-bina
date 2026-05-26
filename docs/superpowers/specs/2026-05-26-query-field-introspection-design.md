# Query field introspection + select/order-by pickers

## Goal

In query mode, let the user discover a query's output columns server-side and
use them two ways:

- **Select fields** — choose which columns the results table shows
  (client-side visibility filter).
- **Order by** — choose one or more columns (each ASC/DESC) to sort the result
  server-side.

Both lists are populated from the **same** field list: the output columns of
the current query, obtained without scanning data.

## Scope

- Introspection target: the **final output columns** of the whole query (the
  outermost SELECT). Not per-CTE, not per-subquery.
- Each field entry carries **name + ClickHouse type**.
- Backend describe endpoint **and** frontend pickers are both in scope.

## Mechanism

Use ClickHouse `DESCRIBE (<query>)`. ClickHouse analyzes the query and returns
one row per output column (`name`, `type`, plus columns we ignore) **without
scanning data**. We wrap the user's SQL exactly like the existing pagination
wrapper in `connect.py:346`:

```sql
DESCRIBE (
<your query>
)
```

Rejected alternatives:
- `SELECT * FROM (<query>) LIMIT 0 FORMAT TabSeparatedWithNamesAndTypes` —
  reuses the wrapper but goes through query planning/execution and is fiddlier
  to parse.
- `EXPLAIN` variants — overkill, awkward to extract clean column names.

## Backend

### `clickhouse.py`

New `describe_query(c: ChConfig, query: str, database: str | None) ->
tuple[bool, list[dict] | str]`:

- Strip a trailing `;` from `query` (like `run_query`).
- Run `DESCRIBE (\n{query}\n)` via the existing `ch_query()` (Basic auth, 5s
  timeout) with `fmt="TabSeparated"`.
- On failure, return `(False, message)`.
- On success, parse each non-empty line; take column 0 = `name`, column 1 =
  `type`. Return `(True, [{"name": ..., "type": ...}, ...])`.

### `connect.py`

New session-aware `describe_query(sid: str, sql: str) -> dict` mirroring
`run_query`'s guards:

- `_ensure_session` / `_get_session_entry`; no session →
  `{"ok": False, "reason": "no-session"}`.
- No selected database → `{"ok": False, "reason": "no-database"}`.
- Delegate to `clickhouse.describe_query(s.config, sql, s.database)`.
- Return `{"ok": True, "fields": [...]}` or `{"ok": False, "message": ...}`.

Extend `run_query` to accept an optional `order_by: list[dict] | None`
parameter (`[{"name", "dir"}]`):

- Build an `ORDER BY` clause appended **inside** the existing wrapper:
  `SELECT * FROM (\n{inner}\n) {order_clause} LIMIT {limit} OFFSET {offset}`.
- Each `name` is backtick-quoted; each `dir` is whitelisted to `ASC`/`DESC`
  (default `ASC`). Names originate from the describe list, so they are
  known-good; quoting + the direction whitelist guard against malformed SQL.
- Empty/absent `order_by` → no `ORDER BY` clause (current behavior unchanged).

### `main.py`

New route `POST /api/clickhouse/describe`:

- Body `{query}`. Empty query → `400`. Mirrors the existing
  `/api/clickhouse/query` handler style.
- Maps `reason: "no-session"`/`"no-database"` → `409`, like the query route.
- Success → `{"ok": True, "fields": [{"name", "type"}, ...]}`.

Extend `POST /api/clickhouse/query` to read optional `order_by` from the body
and pass it through to `run_query`. `format: "csv"` is unaffected and exports
**all** columns.

## Frontend (`App.tsx`, `QueryPanel`)

New state:
- `fields: {name, type}[]` — the describe result.
- `visibleCols: Set<string>` — which columns the table renders (default: all).
- `orderBy: {name, dir}[]` — ordered list of sort columns.

UI additions (reuse the `DatabasePicker` toggle-button pattern and existing
`inputClass` styling):

- A **Fields** action near Execute calls `POST /api/clickhouse/describe` with
  the current SQL. On success it stores `fields`, initializes `visibleCols` to
  all names, and reveals the two pickers. On error, show the message inline.
- **Select fields** picker — a toggle button per column name (on = visible).
  Includes **Clear all** and **Select all** buttons. Purely controls
  `visibleCols`; no server round-trip.
- **Order by** picker — add columns in sequence, each with an ASC/DESC toggle,
  producing `ORDER BY a ASC, b DESC, …`. Stored in `orderBy`.

Wiring:
- `run()` and Previous/Next include `order_by: orderBy` in the `/query` body.
- The results table filters its columns by `visibleCols` (header + cells).
- **Download CSV** is unchanged: it re-fetches server-side and exports **all**
  columns regardless of `visibleCols`.

## Edge cases

- Invalid/unparseable SQL → `DESCRIBE` errors; surface ClickHouse's message via
  the `{ok: false, message}` path, same as a failed query.
- Query changes after describe: `fields`/pickers reflect the last describe call.
  Re-running **Fields** refreshes them. (No auto-refresh on every keystroke.)
- Order-by column hidden in the view: still valid — visibility is client-side,
  ordering is server-side; they're independent.
- Empty result / zero columns: pickers render empty; table behaves as today.

## Testing

- Backend unit tests for `clickhouse.describe_query` parsing (names + types,
  trailing `;`, error passthrough) and `run_query` order-by clause building
  (quoting, direction whitelist, empty order_by = unchanged SQL).
- Backend route tests for `/api/clickhouse/describe` (200 shape, 400 empty
  query, 409 no session / no database) and `/query` with `order_by`.
- Frontend/e2e: describe populates both pickers; Clear all / Select all toggle
  visibility; order-by re-runs server-side and changes row order; CSV still
  exports all columns.

## Docs

Update `docs/query.md`: document the Fields action, the two pickers, the
client-side vs server-side split, and CSV exporting all columns. Add the
`POST /api/clickhouse/describe` row and the `order_by` field of
`POST /api/clickhouse/query` to the API table.
