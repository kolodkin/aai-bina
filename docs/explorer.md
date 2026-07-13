# Explorer — the classical table navigator

The third top-level page (`/explorer`, next to [queries](./queryview.md) and
[dashboards](./dashboard.md)): a sidebar lists the active database's tables;
clicking one browses its rows. No SQL is typed — the page generates
`SELECT * FROM "<table>"` and drives it with the same field select and
order-by select the query panel uses.

## Layout

```
┌───────────────────────────────────────────────────┐
│ 🟢 connected - test        Queries Explorer Dashboard │
│                                                     │
│ ┌────────┐ ┌─────────────────────────────────────┐ │
│ │ Tables │ │ items      Limit [100] ← Prev Next → │ │
│ │ events │ │ ┌─────────────────────────────────┐ │ │
│ │ items ◀│ │ │ Select fields  [id] [name]      │ │ │
│ │ users  │ │ │ Order by       [id] [name]      │ │ │
│ │        │ │ └─────────────────────────────────┘ │ │
│ │        │ │  id │ name                          │ │
│ │        │ │  …  │ …                             │ │
│ └────────┘ └─────────────────────────────────────┘ │
└───────────────────────────────────────────────────┘
```

- **Sidebar** — the tables of the session's selected database, from
  `GET /api/db/tables`. It refreshes when the active database changes (via the
  connection pill); a selected table that no longer exists is deselected.
- **Rows panel** — the selected table's rows. The selection lives in the URL
  (`/explorer?table=<name>`), so reloads and links land on the same table.
- Without a ready connection the page shows a hint to connect on the Queries
  page first; connection state is shared app-wide (see
  [queryview.md](./queryview.md)).

## Browsing behavior

Selecting a table describes `SELECT * FROM "<table>"` to populate the pickers,
then loads the first page. The pickers are the query panel's, with the same
semantics:

- **Select fields** — client-side column visibility only; no re-run.
- **Order by** — server-side `ORDER BY`; toggling a column or flipping its
  ASC/DESC direction re-runs the query immediately (browsing wants instant
  feedback, so unlike the query panel there is no separate Run button).
- **Pagination** — Limit (applies on blur) plus Previous/Next, mapping to the
  query API's `limit`/`offset`.

The generated SQL always selects `*`; the table name is double-quote-escaped,
the identifier quoting every driver accepts.

## Table listing per driver

`GET /api/db/tables` lists via the driver's `list_tables`:

| Driver     | Source                                                     |
| ---------- | ---------------------------------------------------------- |
| ClickHouse | `SHOW TABLES` in the selected database                      |
| Postgres   | `information_schema.tables` for the `public` schema (tables and views — what an unqualified name resolves to under the default `search_path`; other schemas need explicit SQL on the query page) |
| DuckDB     | `SHOW TABLES` against the file (no database picker)         |

Like `/api/db/query` and `/api/db/describe`, the endpoint is session-scoped and
gated: no active connection or (for drivers with a picker) no selected database
is a 409.
