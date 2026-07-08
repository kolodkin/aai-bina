# Remote control (MCP push to a live session)

An MCP client (e.g. an AI agent) can push a SQL query into a **live** QueryView
browser session. The targeted browser fills its query panel and auto-runs the
query — the browser is the consumer; the agent does not get results back.

## Arming a session

Remote control is **opt-in**, per browser session, and off by default. Once a
database is selected, an **agent icon** sits next to the connection status pill.
Click it and toggle **Allow remote control**. The popover then shows this
session's **id** and a copyable command, e.g.:

> Use the queryview mcp to connect to session "a1b2c3"

Turning the toggle off (or closing the tab) disarms the session immediately —
pushes to its id are then reported as not delivered.

## MCP tools

The backend mounts a FastMCP server (Streamable HTTP) at `/mcp` exposing four
tools:

- `push_query(session_id, query, limit?=100, offset?=0, order_by?, fields?, cell_view?, name?)` —
  push a query. `order_by` is `[{name, dir}]`; `fields` are the columns to show
  (omit for all); `cell_view` is raw YAML for this push only (not persisted);
  `name` selects a saved query. Returns `{ok, message}` — `ok:false` for an
  unknown id, a held lock (`"blocked, user editing"`), or malformed
  `order_by`/`fields` (`"invalid …"`).
- `list_queries(conn_type?="clickhouse")` — list saved queries:
  `{queries: [{query_name, query, cell_view, order_by, fields}]}`. Pass a
  `query_name` back as `push_query`'s `name`.
- `run_query(query, connection?="clickhouse")` — run a read-only query and return
  rows to the agent (not the browser): `{ok, columns, rows}`, capped at 1000
  rows. For schema discovery / data inspection before building a dashboard.
- `upsert_dashboard(session_id, name, connection, html, queries)` — push a
  dashboard **draft** to the session, which navigates to it and renders it.
  Does **not** persist — only the user's **Save** button in the dashboard view
  writes it to the store (mirrors `push_query`). Returns `{ok, pushed, message}`.
  See [dashboard.md](./dashboard.md).

The pushed query runs through the normal `POST /api/db/query`, so all of
that path's pagination and order-by safety applies; the push layer never talks
to ClickHouse directly.

## How it works

When armed, the browser opens an SSE stream (`GET /api/remote/events`); the
backend registers an in-memory channel keyed by a random public id (never the
`qv_session` cookie). `push_query` (and the test-only `POST /api/remote/push`)
enqueue onto that channel; the SSE stream delivers the payload and the panel
fills `query` / `limit` / `offset` / `order_by` / selected `fields` and runs.

State is in-memory and per-process (like the active-connection session map): a
backend restart drops channels; the browser reconnects while armed and gets a
new id.

## Related docs

- [api.md](./api.md) — backend JSON API.
- [queryview.md](./queryview.md) — the single-prompt page concept.
- [query.md](./query.md) — running queries: pagination, fields, order-by, CSV.
