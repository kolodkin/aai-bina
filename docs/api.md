# API

The FastAPI backend exposes a small JSON API under `/api/*`. ClickHouse queries
run over the HTTP interface with HTTP Basic auth and a 5s timeout. All
connection-config bodies validate `host` (non-empty) and `port` (integer
`1..65535`); validation errors return `400`.

**Sessions:** the active connection is per session, keyed by an `HttpOnly`
`qv_session` cookie (set on the first request). `/session`, `/connect`, `/open`,
and `/database` all act on the cookie's session, so different browsers connect
independently. Saved connections themselves are shared (SQLite).

| Method | Path                        | Body                                   | Description |
| ------ | --------------------------- | -------------------------------------- | ----------- |
| GET    | `/api/health`               | —                                      | Service health check. |
| GET    | `/api/session`              | —                                      | This session's state `{connected, name?, type?, databases?, database?}`. For an unseen cookie, auto-connects the latest active connection. |
| POST   | `/api/clickhouse/test`      | `{host,port,username,password}`        | Test a connection (test only — no save, no activation). `{ok, message}`. |
| POST   | `/api/clickhouse/connect`   | `{name,host,port,username,password}`   | Create: open + save + activate for this session; lists databases (`new <type>` form). `{ok, name, databases}` \| `{ok:false, message}`. |
| POST   | `/api/clickhouse/open`      | `{name}`                               | Open a saved connection by name for this session; lists databases (`connect <name>`). `{ok, name, databases}` \| `{ok:false, message}`. |
| POST   | `/api/clickhouse/database`  | `{database}`                           | Select this session's active connection's database. `{ok}`. |
| POST   | `/api/clickhouse/query`     | `{query, limit?, offset?, format?, order_by?}` | Run SQL against this session's selected database, paginated by `limit`/`offset` (defaults 100/0). `format:"csv"` returns CSV. `order_by` is `[{name, dir}]` (`dir` ASC/DESC, names backtick-quoted) sorting the pagination wrapper. `{ok, output}` (raw text) \| `{ok:false, message}`. Empty query → `400`; no session → `409`. |
| POST   | `/api/clickhouse/describe`  | `{query}`                              | Describe the query's output columns via ClickHouse `DESCRIBE` (no data scanned). `{ok, fields:[{name, type}]}` \| `{ok:false, message}`. Empty query → `400`; no session / no database → `409`. |
| GET    | `/api/predefined-queries`   | `?type=<connType>`                     | Global predefined queries for a connection type. `{queries:[{query_name, query, cell_view}]}`. `cell_view` is raw YAML text (or `null`) — see [query.md](./query.md#cell-views). |
| POST   | `/api/predefined-queries`   | `{query_name, type, query, cell_view?}` | Upsert a global predefined query. `cell_view` is optional raw YAML text; empty/missing clears it. `{ok}`; missing required fields → `400`. |
| GET    | `/api/remote/events`        | —                                      | SSE stream a browser opens when "remote control" is armed. Emits a `ready` event (`{id}`) then `query` and `dashboard` events with pushed payloads (each emitted under the SSE event named by the payload's `type`). |
| POST   | `/api/remote/push`          | `{session_id, query, limit?, offset?, order_by?, fields?}` | Push a query to a live session (the surface `push_query` and the e2e suite use). `{ok}` \| `{ok:false, message}` (unknown session). Empty `query`/`session_id` → `400`. |
| POST   | `/api/runqueries`           | `{connection, queries:{name:SQL}}`     | Run a dashboard's named queries against a saved connection (by name), using its stored database. Fail-fast: `{ok, results:{name:{col:[…]}}}` (column-oriented) on full success; on any failure an HTTP error with `{ok:false, message}` — `404` unknown connection, `400` bad body / no selected database / a failing query (message prefixed with the panel name). See [dashboard.md](./dashboard.md). |
| POST   | `/api/dashboards`           | `{name, connection, html, queries, session_id?}` | Upsert a dashboard by name (REST mirror of the `upsert_dashboard` MCP tool); with `session_id`, also pushes it to that live session. `{ok, persisted, pushed, message}`. Missing `name`/`connection`/`html` → `400`. |
| GET    | `/api/dashboards`           | —                                      | List saved dashboards (no payload): `{dashboards:[{name, connection, updated_at}]}`, ordered by name. |
| GET    | `/api/dashboards/{name}`    | —                                      | A saved dashboard `{name, connection, html, queries}` (`queries` parsed to a dict), or `404 {error:"not found"}`. |

**MCP:** a FastMCP server is mounted at `/mcp` (Streamable HTTP) exposing
`push_query` (push SQL to a session's query panel) and `upsert_dashboard`
(persist a dashboard and push it to a session). Both delegate to the in-process
hubs the matching REST endpoints call. See [remote.md](./remote.md) and
[dashboard.md](./dashboard.md).

## Persistence

Connections are stored in SQLite (`backend/queryview.db`, override with
`DB_PATH`) via SQLModel. See [connect.md](./connect.md) for the schema and the
session / auto-connect model.

## Related docs

- [queryview.md](./queryview.md) — the single-prompt page concept.
- [connect.md](./connect.md) — connecting (`new <type>` / `connect <name>`), storage, sessions.
- [query.md](./query.md) — running queries: pagination, predefined queries, CSV.
- [remote.md](./remote.md) — pushing queries to a live session over MCP.
- [dashboard.md](./dashboard.md) — the dashboard page, `upsert_dashboard`, and the `window.queries` contract.
