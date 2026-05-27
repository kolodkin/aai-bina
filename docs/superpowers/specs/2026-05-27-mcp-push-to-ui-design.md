# MCP push-to-UI layer

## Goal

Let an external MCP client (e.g. an AI agent) push a SQL query into a **live**
QueryView browser session. The targeted browser fills its query panel with the
pushed query and parameters and auto-runs it, rendering results in real time.
The browser is the consumer of the push; the agent does not receive query
results back.

The layer sits *above* the existing per-session connections: it never talks to
ClickHouse itself — a pushed query is executed by the browser through the
existing `POST /api/clickhouse/query`, reusing all of that path's connection,
pagination, and order-by safety.

## Decisions

These were settled during brainstorming:

1. **Direction** — push a query *to* a live browser session; the browser is the
   consumer (not "expose connections as agent tools").
2. **Targeting** — the browser surfaces its **own** session id with a copyable
   agent command. There is no `list_sessions` enumeration.
3. **Push effect** — fill the SQL box **and** `limit`/`offset`/`order_by` **and**
   the selected (visible) fields, then auto-run and render results.
4. **Opt-in** — an "Allow remote control" toggle, **off by default**. A session
   is only reachable while armed.
5. **MCP transport** — FastMCP mounted on the existing FastAPI app
   (Streamable HTTP) at `/mcp`; the agent connects to a single URL.
6. **Push transport (server → browser)** — Server-Sent Events (SSE). One-way is
   sufficient; arming, state, and query execution already use plain requests.
7. **Discovery UI** — a small **agent icon next to the connection status pill**
   opens a popover containing the toggle and, once armed, the session id + a
   copyable command.

## Architecture & data flow

```
Agent ──(MCP, Streamable HTTP /mcp)──▶ push_query tool ─┐
                                                        ├─▶ remote.py hub ──(SSE /api/remote/events)──▶ browser
Test/debug ──(REST POST /api/remote/push)───────────────┘        (in-memory)                              │
                                                                                                          ▼
                                                                          QueryPanel fills SQL + params, auto-runs
                                                                                                          │
                                                                                                          ▼
                                                                              existing POST /api/clickhouse/query
```

The MCP tool and the REST endpoint are both thin wrappers over the same
in-process `remote.py` hub. The REST `push` exists so the e2e suite can drive
the flow with a plain `httpx` POST instead of standing up an MCP client.

## Backend

### New module `backend/queryview/remote.py` (hub; no HTTP concerns)

In-memory registry of armed channels, keyed by a random **public** `remote_id`
(distinct from the `HttpOnly` `qv_session` cookie sid, so the session secret is
never exposed). The id is short and URL-safe, e.g. `secrets.token_hex(4)`.

Each entry holds `{queue: asyncio.Queue, created_at, last_seen}`. Functions:

- `register() -> remote_id` — mint an id, create a queue, store the entry.
- `unregister(remote_id) -> None` — drop the entry (idempotent).
- `push(remote_id, payload) -> tuple[bool, str]` — enqueue `payload` for the
  channel; `(False, "unknown or inactive session")` if no such channel.
- `event_stream(remote_id, request) -> AsyncIterator[bytes]` — the SSE
  generator: yields an initial `ready` event carrying `remote_id`, then `query`
  events drained from the queue, plus a ~15s heartbeat comment (`: ping`).
  Breaks the loop when `await request.is_disconnected()` is true, then
  `unregister`s.

The hub is module-level state, matching the existing in-memory `_sessions` map
in `connect.py`. No metadata lookup into `connect.py` is needed (we don't
surface connection/database in the popover or to the agent), so **no new
accessor is added to `connect.py`**.

### Endpoints (`backend/queryview/main.py`)

- `GET /api/remote/events` — SSE. `register()`s a channel and returns a
  `StreamingResponse(event_stream(...), media_type="text/event-stream")` with
  `Cache-Control: no-cache`. The browser opens this when it arms; closing the
  `EventSource` disconnects it, which `unregister`s server-side.
- `POST /api/remote/push` — body `{session_id, query, limit?, offset?,
  order_by?, fields?}`. Validates `session_id` and `query` are non-empty
  strings; coerces `limit`/`offset` like the existing `/query` handler; passes
  `order_by` through as a list; passes `fields` through as a list of column-name
  strings (the visible-column selection). Calls `remote.push(session_id,
  payload)`. Returns `{ok: true}` or `{ok: false, message}` (HTTP 200 either
  way; an unknown session is a normal not-delivered result, not a server
  error). Empty `query`/`session_id` → `400`.

The `payload` enqueued and later delivered to the browser is
`{"type": "query", "query", "limit", "offset", "order_by", "fields"}`. `fields`
is optional: omitted/empty ⇒ show all columns.

### MCP mount (`backend/queryview/mcp_server.py` + `main.py`)

`mcp_server.py` creates a FastMCP instance and defines the single tool, both
delegating to `remote.py`:

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("queryview", stateless_http=True)

@mcp.tool()
async def push_query(session_id: str, query: str,
                     limit: int = 100, offset: int = 0,
                     order_by: list[dict] | None = None,
                     fields: list[str] | None = None) -> dict:
    """Push a SQL query to a live QueryView browser session (by session id).

    fields: optional list of column names to display; omit to show all.
    """
    ok, message = remote.push(session_id, {...})
    return {"ok": ok, "message": message}
```

`main.py` mounts it and wires its lifespan (the one integration step a mounted
FastMCP app needs — a mounted Starlette sub-app's lifespan is not run by the
parent automatically):

```python
@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield

app = FastAPI(title="queryview-backend", lifespan=lifespan)
app.mount("/mcp", mcp.streamable_http_app())
```

The mount is registered **before** the SPA catch-all `GET /{full_path:path}` so
`/mcp` is not shadowed. The existing `/api/{rest:path}` not-found route only
matches `/api/*`, so it does not affect `/mcp` or `/api/remote/*` (which are
declared before it).

### Dependency

Add `mcp` to `pyproject.toml` `[project.dependencies]` (provides FastMCP;
requires Python ≥3.10, satisfied by the project's ≥3.11). Refresh `uv.lock`.

## Frontend (`frontend/src/App.tsx`)

Remote-control state lifts to `App`, because the trigger lives next to the
connection pill (which `App` renders):

New `App` state: `armed: boolean` (default `false`), `remoteId: string | null`,
`pushed: PushPayload | null`, and `showRemotePanel: boolean` (popover open).

- A small **agent icon button** renders alongside the connection status pill
  (shown whenever `connection?.database` is set) — a compact inline SVG (e.g. a
  small robot/agent glyph), no new icon dependency. Clicking toggles the
  popover.
- **Popover** contains:
  - the "Allow remote control" toggle bound to `armed`;
  - when `armed && remoteId`: the session id (monospace) and a copyable command
    line — default text:
    `Use the queryview MCP to push queries to QueryView session "<id>".` —
    with a Copy button (`navigator.clipboard.writeText`).
- A `useEffect` keyed on `armed`: when `true`, open
  `new EventSource('/api/remote/events')`, set `remoteId` from the `ready`
  event, and set `pushed` from each `query` event; cleanup closes the
  `EventSource` (turning the toggle off, or unmount, disarms → server
  `unregister`). Arming also sets `showQuery = true` so the `QueryPanel` is
  mounted and a push always has somewhere to land.
- `pushed` is passed to `QueryPanel` as a prop.

### `QueryPanel` changes

- New optional prop `pushed: PushPayload | null` (carries `query`, `limit`,
  `offset`, `order_by`, optional `fields`).
- Refactor `run(nextOffset)` to delegate to a new
  `runWith(query, limit, offset, orderBy, selectFields?)` that builds the
  `/api/clickhouse/query` body from its **arguments** (not component state), so a
  push runs the pushed values without waiting for React state to settle. The
  existing buttons call `runWith(sql, limit, offset, orderBy)` (no
  `selectFields`, leaving visibility untouched as today).
- A `useEffect` keyed on `pushed`: when it changes, apply it —
  `setSql(query)`, `setLimit`, `setOffset`, `setOrderBy` (defaults when a field
  is absent) — and call `runWith(query, limit, offset, orderBy, fields)`.

### Selected fields from a push

`runWith` receiving `selectFields` makes the pushed selection **authoritative**
(a push fully specifies the view, with none of the "manually-edited SQL" staleness
the existing guard protects against). On success it sets, from the returned
result:

- `setFields(columns.map((name) => ({ name, type: '' })))` — the described-field
  list synthesized from the actual result columns (types unknown for a push), so
  the "Select fields" / "Order by" pickers render those columns and the user can
  adjust afterward;
- `setVisibleCols(selectFields ∩ columns)` when `selectFields` is non-empty, else
  all columns (show all).

Because `fields` then covers every returned column, the existing `shownIdx`
filter (`!fieldNames.has(col) || visible.has(col)`) reduces to "show exactly
`visibleCols`", so the table renders only the pushed selection. Manual runs pass
no `selectFields` and leave `fields`/`visibleCols` as-is, so today's behavior is
unchanged.

## SSE protocol

Text `event-stream`, UTF-8. Events:

- `event: ready\ndata: {"id":"<remote_id>"}\n\n` — sent once on connect.
- `event: query\ndata: {"query":...,"limit":...,"offset":...,"order_by":...,"fields":...}\n\n`
  — one per push (`fields` optional).
- `: ping\n\n` — heartbeat comment (~15s) to keep the connection open and detect
  disconnects.

## Edge cases

- **Push to unknown/disarmed id** — `remote.push` returns
  `(False, "unknown or inactive session")`; the agent relays that. No browser
  effect.
- **Multiple tabs in one browser** — they share the cookie sid but each armed
  tab opens its own `EventSource`, so each gets its own `remote_id`/channel.
  Acceptable; a push targets exactly one channel.
- **Backend restart** — drops the in-memory hub (consistent with `_sessions`).
  The browser's `EventSource` auto-reconnects while armed and obtains a **new**
  `remote_id`; the popover reflects the new id. Any id the agent held is now
  stale and returns "unknown or inactive session".
- **Push arrives mid-query (busy)** — the apply effect still fires; it issues a
  fresh `runWith`. The latest push wins.
- **Disarm** — closing the `EventSource` ends `event_stream` via
  `is_disconnected()`, which `unregister`s the channel; further pushes to that
  id return not-delivered.
- **Pushed `order_by`/SQL safety** — unchanged: the browser sends them to the
  existing `/api/clickhouse/query`, which already backtick-quotes names and
  whitelists ASC/DESC. The push path never reaches ClickHouse directly.
- **Pushed `fields`** — names not present in the returned result are ignored
  (intersection with actual columns); omitted/empty `fields` shows all columns.
  Visibility is purely client-side, so this never changes what the query
  returns — only which columns the table renders.

## Security / scope

Matches the app's existing posture: no auth beyond the opt-in toggle, in-memory
single-process hub (multi-worker sharing is out of scope, as with `_sessions`).
The id exposed in the popover and consumed by the agent is a random
`remote_id`, never the `qv_session` cookie. A session is invisible and
unpushable unless the user has explicitly armed it.

## Testing

- **Backend unit** — `remote.py`: `register` returns distinct ids; `push` to a
  registered id enqueues and returns `(True, ...)`; `push` to an unknown id
  returns `(False, "unknown or inactive session")`; `unregister` makes a
  subsequent push not-deliver.
- **Backend route** — `POST /api/remote/push` shape (200 ok/not-delivered, 400
  on empty `query`/`session_id`).
- **e2e (Playwright + httpx, against CI's real ClickHouse)** — connect → select
  a database → open the query panel → click the agent icon → enable the
  toggle → read the displayed session id from the popover → `httpx POST
  /api/remote/push` with that id and a `SELECT` (+ `limit`/`order_by`/`fields`)
  → assert the SQL box shows the pushed query, the results table renders the
  rows, and only the pushed `fields` are shown as columns → disable the toggle →
  assert a push to that id now returns not-delivered.
- The single MCP tool is a thin wrapper over the hub the REST path exercises; a
  light check that `/mcp` is mounted (responds to a request) is sufficient.

## Docs

- New `docs/remote.md` — the MCP push-to-UI layer: arming, the popover + agent
  command, the `push_query` tool, the SSE channel, and the end-to-end flow.
- Update `docs/api.md` — add the `GET /api/remote/events` and
  `POST /api/remote/push` rows and a note about the `/mcp` mount; cross-link
  `remote.md`.
- `docs/queryview.md` — one line noting the agent icon by the status pill (the
  only persistent-UI addition).

## Out of scope

- Returning query results to the agent (the browser is the consumer).
- Enumerating sessions to the agent (`list_sessions`), session metadata in the
  popover, multi-worker hub sharing, and any auth beyond the opt-in toggle.
