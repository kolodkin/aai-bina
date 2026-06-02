# Remote control (MCP push to a live session)

An MCP client (e.g. an AI agent) can push a SQL query into a **live** QueryView
browser session. The targeted browser fills its query panel and auto-runs the
query — the browser is the consumer; the agent does not get results back.

## Arming a session

Remote control is **opt-in**, per browser session, and off by default. Once a
database is selected, an **agent icon** sits next to the connection status pill.
Click it and toggle **Allow remote control**. The popover then shows this
session's **id** and a copyable command, e.g.:

> Use the queryview MCP to push queries to QueryView session "a1b2c3".

Paste that into your agent. Turning the toggle off (or closing the tab) disarms
the session immediately — pushes to its id are then reported as not delivered.

## The MCP tool

The backend mounts a FastMCP server (Streamable HTTP) at `/mcp` exposing two
tools:

- `push_query(session_id, query, limit?=100, offset?=0, order_by?, fields?)` —
  push a query to the session. `order_by` is `[{name, dir}]`; `fields` is the
  list of column names to display (omit to show all). Returns
  `{ok, message}`; an unknown/disarmed id returns `{ok: false}`.
- `upsert_dashboard(session_id, name, connection, html, queries)` — persist a
  dashboard and push it to the session, which navigates to it and renders it.
  Returns `{ok, persisted, pushed, message}`. See [dashboard.md](./dashboard.md).

The pushed query runs through the normal `POST /api/clickhouse/query`, so all of
that path's pagination and order-by safety applies; the push layer never talks
to ClickHouse directly.

## How it works

The browser opens an SSE stream (`GET /api/remote/events`) when armed; the
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
