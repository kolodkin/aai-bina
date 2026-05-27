# Querying

Once a database is selected on the active connection, typing `query` opens the
**query panel** below the prompt. The panel runs SQL against the session's
selected database, pages through results, saves/loads reusable queries, and
exports the current page as CSV.

Typing `query` before a database is selected shows the hint
`Select a database first.`

## Panel

```
┌───────────────────────────────────────────────────────────┐
│ [ query ]  [ Predefined queries… ▾ ]            [ Save ]   │
│                                    [Min] [S] [M] [L] [XL]  │
│ ┌───────────────────────────────────────────────────────┐ │
│ │ SELECT …                                              │ │  ← SQL textarea
│ └───────────────────────────────────────────────────────┘ │
│ [Execute] [Fields] Limit [100] Offset [0] [← Prev] [Next →]│
│                                          [Download CSV]    │
│ ┌───────────────────────────────────────────────────────┐ │
│ │ name | …                                              │ │  ← results table
│ │ alpha| …                                              │ │     (scrollable)
│ └───────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

In query mode the command **prompt** moves onto the panel's top row, next to the
predefined-query controls, to save vertical space.

- **SQL textarea** — the query to run. The **Min / S / M / L / XL** toggles change
  its height; **Min** collapses it to nothing to maximize room for results.
- **Execute** — runs the query at the current offset.
- **Fields** — introspects the query's output columns (see
  [Fields, selection & ordering](#fields-selection--ordering)).
- **Limit / Offset** — page size and starting row (defaults `100` / `0`).
- **Previous / Next** — step the offset by ±limit and re-run. Previous is
  disabled at offset `0`. There is no total-row count, so Next can page past the
  last row into an empty result.
- **Download CSV** — downloads the **current page** as `query.csv`, always with
  **all** columns (the field selection below is view-only).
- **Results table** — the rows for the current page, in a scrollable table.

## Fields, selection & ordering

**Fields** asks the backend to describe the current query's output columns —
their names and ClickHouse types — without scanning data (ClickHouse
`DESCRIBE (<query>)`). It populates two pickers from that one list:

- **Select fields** — a toggle per column controlling which columns the results
  table shows. This is **client-side and immediate**: toggling shows/hides the
  column on button press with no re-query, and **Download CSV** ignores it (CSV
  always exports every column). **Select all** / **Clear all** flip every toggle
  at once. New columns from a query edited since the last **Fields** call always
  show, so a stale list can't blank the table.
- **Order by** — pick one or more columns, each **ASC** (default) or **DESC**.
  This is **server-side**, so it only takes effect when the query re-runs: on
  **Execute** / **Previous** / **Next**, on **Download CSV**, or via the **Run**
  button in the order-by section. **Run** re-runs the whole query (same as
  Execute), so it applies the current limit/offset too — not just the ordering.
  Column names are backtick-quoted and directions are whitelisted, so the picker
  can't inject SQL.

Editing the SQL doesn't auto-refresh the pickers — click **Fields** again to
re-describe.

## Pagination

The backend paginates by wrapping the query:

```sql
SELECT * FROM (
<your query>
) LIMIT <limit> OFFSET <offset>
```

So pages are stable only if the query defines its own order — include an
`ORDER BY` for predictable `Previous`/`Next` boundaries.

## Predefined queries

Predefined queries are reusable SQL **shared globally** (not per session),
keyed by **connection type** (e.g. `clickhouse`). They are stored in the
`predefined_queries` SQLite table:

```sql
CREATE TABLE predefined_queries (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  query_name TEXT NOT NULL,
  type       TEXT NOT NULL,   -- connection type (clickhouse, …)
  query      TEXT NOT NULL,
  UNIQUE (type, query_name)
);
```

- The **selector** lists saved queries for the active connection's type;
  choosing one loads its SQL into the textarea and makes it the active name. Its
  **+ New name…** item prompts for a fresh name (no separate name field).
- **Save** stores the textarea's SQL under the **currently selected name** and
  refreshes the selector. Saving an existing name **upserts** (overwrites) it.

Renaming and deleting predefined queries are not yet supported — see
[future.md](./future.md).

## Results & CSV

Results come back from ClickHouse as `TabSeparatedWithNames` and render as an
HTML table (first row = column names). The table scrolls within the panel; wide
results scroll horizontally. **Download CSV** re-runs the current page asking for
`CSVWithNames` and saves it as `query.csv` — it exports the current page (not the
full result set) and always includes every column, regardless of the **Select
fields** view.

## API

| Method | Path                        | Body                                          | Result |
| ------ | --------------------------- | --------------------------------------------- | ------ |
| POST   | `/api/clickhouse/query`     | `{query, limit?, offset?, format?, order_by?}` | `{ok, output}` (raw text) \| `{ok:false, message}`. `format:"csv"` returns CSV. `order_by` is `[{name, dir}]` (`dir` ASC/DESC). Empty query → `400`; no session → `409`. |
| POST   | `/api/clickhouse/describe`  | `{query}`                                     | `{ok, fields:[{name, type}]}` — the query's output columns, via `DESCRIBE`, no data scanned. \| `{ok:false, message}`. Empty query → `400`; no session / no database → `409`. |
| GET    | `/api/predefined-queries`   | `?type=<connType>`                            | `{queries:[{query_name, query}]}` for that connection type. |
| POST   | `/api/predefined-queries`   | `{query_name, type, query}`                   | `{ok}`; upserts a predefined query. Missing fields → `400`. |

Queries run over the ClickHouse HTTP interface (HTTP Basic auth, 5s timeout),
scoped to the session's selected database.

## Related docs

- [queryview.md](./queryview.md) — the single-prompt page concept.
- [connect.md](./connect.md) — connecting, storage, sessions.
- [api.md](./api.md) — the full backend JSON API.
