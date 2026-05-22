# API

The Deno backend exposes a small JSON API under `/api/*`. ClickHouse queries run
over the HTTP interface with HTTP Basic auth and a 5s timeout. All
connection-config bodies validate `host` (non-empty) and `port` (integer
`1..65535`); validation errors return `400`.

**Sessions:** the active connection is per session, keyed by an `HttpOnly`
`qv_session` cookie (set on the first request). `/session`, `/connect`, `/open`,
and `/database` all act on the cookie's session, so different browsers connect
independently. Saved connections themselves are shared (SQLite).

| Method | Path                        | Body                                   | Description |
| ------ | --------------------------- | -------------------------------------- | ----------- |
| GET    | `/api/health`               | —                                      | Service health check. |
| GET    | `/api/session`              | —                                      | This session's state `{connected, name?, databases?, database?}`. For an unseen cookie, auto-connects the latest active connection. |
| POST   | `/api/clickhouse/test`      | `{host,port,username,password}`        | Test a connection (test only — no save, no activation). `{ok, message}`. |
| POST   | `/api/clickhouse/connect`   | `{name,host,port,username,password}`   | Create: open + save + activate for this session; lists databases (`new <type>` form). `{ok, name, databases}` \| `{ok:false, message}`. |
| POST   | `/api/clickhouse/open`      | `{name}`                               | Open a saved connection by name for this session; lists databases (`connect <name>`). `{ok, name, databases}` \| `{ok:false, message}`. |
| POST   | `/api/clickhouse/database`  | `{database}`                           | Select this session's active connection's database. `{ok}`. |

## Persistence

Connections are stored in SQLite (`backend/queryview.db`, override with
`DB_PATH`); the backend runs with `--allow-write`. See
[connect.md](./connect.md) for the schema and the session / auto-connect model.

## Related docs

- [queryview.md](./queryview.md) — the single-prompt page concept.
- [connect.md](./connect.md) — connecting (`new <type>` / `connect <name>`), storage, sessions.
