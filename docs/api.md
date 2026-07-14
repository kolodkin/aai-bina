# API

The FastAPI backend exposes a small JSON API under `/api/*`. ClickHouse queries
run over the HTTP interface with HTTP Basic auth and a 5s timeout. All
connection-config bodies validate `host` (non-empty) and `port` (integer
`1..65535`); validation errors return `400`.

**Sessions:** the active connection is per session, keyed by an `HttpOnly`
`qv_session` cookie (set on the first request). `/session`, `/connect`, `/open`,
and `/database` act on the cookie's session, so different browsers connect
independently. Saved connections are shared (SQLite).

| Method | Path                        | Body                                   | Description |
| ------ | --------------------------- | -------------------------------------- | ----------- |
| GET    | `/api/health`               | —                                      | Service health check. |
| GET    | `/api/session`              | —                                      | This session's state `{connected, name?, type?, databases?, database?}`. For an unseen cookie, auto-connects the latest active connection. |
| POST   | `/api/db/test`      | `{host,port,username,password}`        | Test a connection (test only — no save, no activation). `{ok, message}`. |
| POST   | `/api/db/connect`   | `{name,host,port,username,password}`   | Create: open + save + activate for this session; lists databases (`new <type>` form). `{ok, name, databases}` \| `{ok:false, message}`. |
| POST   | `/api/db/open`      | `{name}`                               | Open a saved connection by name for this session; lists databases (`connect <name>`). `{ok, name, databases}` \| `{ok:false, message}`. |
| POST   | `/api/db/database`  | `{database}`                           | Select this session's active connection's database. `{ok}`. |
| POST   | `/api/db/query`     | `{query, limit?, offset?, format?, order_by?}` | Run SQL against this session's selected database, paginated by `limit`/`offset` (defaults 100/0). `format:"csv"` returns CSV. `order_by` is `[{name, dir}]` (`dir` ASC/DESC, names backtick-quoted) sorting the pagination wrapper. `{ok, output}` (raw text) \| `{ok:false, message}`. Empty query → `400`; no session → `409`. |
| POST   | `/api/db/describe`  | `{query}`                              | Describe the query's output columns via ClickHouse `DESCRIBE` (no data scanned). `{ok, fields:[{name, type}]}` \| `{ok:false, message}`. Empty query → `400`; no session / no database → `409`. |
| GET    | `/api/db/tables`    | —                                      | Tables of this session's selected database (the Explorer sidebar). `{ok, tables:[{name, rows, bytes}]}` — rows/bytes are engine estimates, null when untracked — \| `{ok:false, message}`. No session / no database → `409`. |
| GET    | `/api/predefined-queries`   | `?type=<connType>&workspace=`          | A workspace's predefined queries for a connection type (`workspace` defaults to `default`). `{queries:[{query_name, query, cell_view}]}`. `cell_view` is raw YAML text (or `null`) — see [query.md](./query.md#cell-views). |
| POST   | `/api/predefined-queries`   | `{query_name, type, query, cell_view?, workspace?}` | Upsert a predefined query in a workspace. `cell_view` is optional raw YAML text; empty/missing clears it. `{ok}`; missing required fields → `400`. |
| GET    | `/api/remote/events`        | —                                      | SSE stream a browser opens when "remote control" is armed. Emits a `ready` event (`{id}`) then `query` and `dashboard` events with pushed payloads (each emitted under the SSE event named by the payload's `type`). |
| POST   | `/api/remote/push`          | `{session_id, query, limit?, offset?, order_by?, fields?}` | Push a query to a live session (the surface `push_query` and the e2e suite use). `{ok}` \| `{ok:false, message}` (unknown session). Empty `query`/`session_id` → `400`. |
| POST   | `/api/runqueries`           | `{connection, queries:{name:SQL}}`     | Run a dashboard's named queries against a saved connection (by name), using its stored database. Fail-fast: `{ok, results:{name:{col:[…]}}}` (column-oriented) on full success; on any failure an HTTP error with `{ok:false, message}` — `404` unknown connection, `400` bad body / no selected database / a failing query (message prefixed with the panel name). See [dashboard.md](./dashboard.md). |
| POST   | `/api/dashboards`           | `{name, connection, html, queries, session_id?, workspace?}` | Upsert a dashboard by name within a workspace; with `session_id`, also pushes it to that live session. `{ok, persisted, pushed, message}`. Missing `name`/`connection`/`html` → `400`. |
| GET    | `/api/dashboards`           | `?workspace=`                          | List a workspace's dashboards (no payload): `{dashboards:[{name, connection, updated_at}]}`, ordered by name. |
| GET    | `/api/dashboards/{name}`    | `?workspace=`                          | A saved dashboard `{name, connection, html, queries}` (`queries` parsed to a dict), or `404 {error:"not found"}`. |

**MCP:** a FastMCP server is mounted at `/mcp` (Streamable HTTP) exposing
`push_query` (push SQL to a session's query panel) and `upsert_dashboard`
(persist a dashboard and push it to a session). Both delegate to the in-process
hubs the matching REST endpoints call. See [remote.md](./remote.md) and
[dashboard.md](./dashboard.md).

## Persistence

Connections are stored in SQLite via SQLModel. See [connect.md](./connect.md)
for the schema and the session / auto-connect model.

## Workspaces

Predefined queries and dashboards belong to a workspace; each workspace syncs
to its own git remote. The remote URL may embed a token — it is encrypted at
rest and never returned by the API. See [workspace.md](./workspace.md).

| Method | Path                      | Body                          | Description |
| ------ | ------------------------- | ----------------------------- | ----------- |
| GET    | `/api/workspaces`         | —                             | List workspaces: `{workspaces:[{name, branch, configured}]}` (never the remote URL). |
| POST   | `/api/workspaces`         | `{name, remote?, branch?}`    | Create a workspace. `{ok}`; empty/`/`-containing name → `400`, duplicate → `409`. |
| PATCH  | `/api/workspaces/{name}`  | `{name?, remote?, branch?}`   | Rename/reconfigure. A null `remote` clears it; an absent key leaves it unchanged. `{ok}`. |
| DELETE | `/api/workspaces/{name}`  | —                             | Delete an empty workspace. `{ok}`; unknown → `404`, still owns entities → `409`. |

## Git sync

Predefined queries and dashboards can be backed up to (and restored from) each
workspace's git repository, per entity. Connections are never written to the
repo. See [gitsync.md](./gitsync.md) for configuration, repository layout, and
the UI.

| Method | Path              | Body                                            | Description |
| ------ | ----------------- | ------------------------------------------------ | ----------- |
| GET    | `/api/git/status`  | `?workspace=`                                    | Whether the workspace has a git remote configured. `{configured}`. |
| POST   | `/api/git/store`   | `{kind, name, conn_type?, message?, workspace?}` | Commit the entity's saved DB state and push. `{ok, committed, sha, message}`. |
| GET    | `/api/git/history` | `?kind=&name=&conn_type=&before=&limit=10&workspace=` | The entity's revisions, newest first. `{ok, revisions: [{sha, date, message}], has_more}`. |
| POST   | `/api/git/restore` | `{kind, name, conn_type?, ref?, workspace?}`     | Overwrite the local DB row with the entity's content at `ref`. `{ok, restored, sha}`. |

`kind` is `"query"` or `"dashboard"`; `conn_type` is required for queries;
`workspace` defaults to `default`. MCP tools `git_store`, `git_history`,
`git_restore` mirror the same surface, resolving the workspace from an
optional `session_id` (see [workspace.md](./workspace.md)).

## Related docs

- [queryview.md](./queryview.md) — the single-prompt page concept.
- [connect.md](./connect.md) — connecting (`new <type>` / `connect <name>`), storage, sessions.
- [query.md](./query.md) — running queries: pagination, predefined queries, CSV.
- [remote.md](./remote.md) — pushing queries to a live session over MCP.
- [dashboard.md](./dashboard.md) — the dashboard page, `upsert_dashboard`, and the `window.queries` contract.
- [gitsync.md](./gitsync.md) — backing up and restoring queries and dashboards via git.
- [workspace.md](./workspace.md) — workspaces: entity ownership and per-workspace remotes.
