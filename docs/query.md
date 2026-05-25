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
│ [ Predefined queries… ▾ ]   [ name ] [ Save ]              │
│                                          [S] [M] [L] [XL]  │
│ ┌───────────────────────────────────────────────────────┐ │
│ │ SELECT …                                              │ │  ← SQL textarea
│ └───────────────────────────────────────────────────────┘ │
│ [Execute]  Limit [100]  Offset [0]  [← Previous] [Next →]  │
│                                          [Download CSV]    │
│ ┌───────────────────────────────────────────────────────┐ │
│ │ name | …                                              │ │  ← results table
│ │ alpha| …                                              │ │     (scrollable)
│ └───────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
```

- **SQL textarea** — the query to run. The **S / M / L / XL** toggles change its
  height.
- **Execute** — runs the query at the current offset.
- **Limit / Offset** — page size and starting row (defaults `100` / `0`).
- **Previous / Next** — step the offset by ±limit and re-run. Previous is
  disabled at offset `0`. There is no total-row count, so Next can page past the
  last row into an empty result.
- **Download CSV** — downloads the **current page** as `query.csv`.
- **Results table** — the rows for the current page, in a scrollable table.

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
  choosing one loads its SQL into the textarea.
- **Save** stores the textarea's SQL under the typed name and refreshes the
  selector. Saving an existing name **upserts** (overwrites) it.

Renaming and deleting predefined queries are not yet supported — see
[future.md](./future.md).

## Results & CSV

Results come back from ClickHouse as `TabSeparatedWithNames` and render as an
HTML table (first row = column names). The table scrolls within the panel; wide
results scroll horizontally. **Download CSV** re-runs the current page asking for
`CSVWithNames` and saves it as `query.csv` — it exports the current page, not the
full result set.

## API

| Method | Path                        | Body                                | Result |
| ------ | --------------------------- | ----------------------------------- | ------ |
| POST   | `/api/clickhouse/query`     | `{query, limit?, offset?, format?}` | `{ok, output}` (raw text) \| `{ok:false, message}`. `format:"csv"` returns CSV. Empty query → `400`; no session → `409`. |
| GET    | `/api/predefined-queries`   | `?type=<connType>`                  | `{queries:[{query_name, query}]}` for that connection type. |
| POST   | `/api/predefined-queries`   | `{query_name, type, query}`         | `{ok}`; upserts a predefined query. Missing fields → `400`. |

Queries run over the ClickHouse HTTP interface (HTTP Basic auth, 5s timeout),
scoped to the session's selected database.

## Related docs

- [queryview.md](./queryview.md) — the single-prompt page concept.
- [connect.md](./connect.md) — connecting, storage, sessions.
- [api.md](./api.md) — the full backend JSON API.
