# Dashboard view

## Goal

Add a second top-level page, `/dashboard`, alongside the existing query
workflow. An AI agent (e.g. Claude Code) authors a dashboard — an HTML layout
plus a set of named SQL queries — and pushes it into a live, armed QueryView
browser session via a new MCP tool. The dashboard is also persisted so it can
be reopened later by name.

The **React frontend** runs the dashboard's queries (it is the trusted code with
access to the session) against a named connection and feeds the results into the
agent-authored HTML, which renders inside an isolated iframe. The agent never
receives query results back — the browser is the consumer, exactly as in the
existing MCP push-to-UI layer.

This builds directly on the existing `remote.py` push hub, the SSE channel, and
the `predefined_queries` persistence pattern.

## Decisions

Settled during brainstorming:

1. **Two top-level pages, client-side routed** (`react-router-dom`):
   `/queries` (today's workflow) and `/dashboard` (new). `App` becomes a thin
   shell; today's `App` body is renamed `QueryView`.
2. **Lifecycle = persist + reopen.** `upsert_dashboard` saves
   `{name, connection, html, queries}` to SQLite (upsert by `name`) **and**
   pushes it live to the armed session. The `/dashboard` page has a **dropdown
   of saved dashboards**; selecting one (or arriving with `?name=x`) renders it.
   There is no separate index-list page.
3. **HTML rendering = sandboxed iframe + CDN.** Agent-authored HTML renders in
   `<iframe sandbox="allow-scripts" srcdoc=…>` (no `allow-same-origin`), isolated
   from the app's cookies/DOM. Results are injected as a `window.queries` JS
   global. External chart libraries from a CDN are allowed (no restrictive CSP).
4. **Result shape = column-oriented dict.** Each query result is a dict of
   `{column_name: [values, …]}` (insertion-ordered, so column order is
   preserved). This is exactly what dashboard HTML reads from `window.queries`,
   e.g. `window.queries.sales.revenue` is the list of values in the `revenue`
   column. No per-result wrapper.
5. **Error handling = fail-fast HTTP code.** `/api/runqueries` runs all the
   queries; if any one fails (or the connection/body is bad) the **whole request**
   returns an HTTP error (`400` for a bad body or a failing query, `404` for an
   unknown connection) with a message. On full success it returns `200` with
   `{ok:true, results:{name:{col:[…]}}}`. So the success value is always clean
   column dicts; `DashboardView` shows a dashboard-level error state if the
   request fails, rather than rendering partial panels.
6. **`/runqueries` takes an explicit connection name**, not the session's active
   connection — a dashboard is self-contained and portable. The dashboard stores
   its `connection`; `upsert_dashboard` takes it as a required parameter.
7. **`session_id` vs `connection` are distinct**: `session_id` is the live
   browser to push the preview to; `connection` is the data source the queries
   run against.

## Architecture & data flow

```
Agent ──(MCP /mcp)── upsert_dashboard(session_id, name, connection, html, queries) ─┐
                                                                                    ├─▶ dashboards store (SQLite, upsert by name)
Test/e2e ──(REST POST /api/dashboards)──────────────────────────────────────────────┘   └─▶ remote.py hub ──(SSE)──▶ browser shell
                                                                                                                          │ navigate /dashboard?name=x
                                                                                                                          ▼
                                                                                  DashboardView (React, trusted origin)
                                                                                    │ POST /api/runqueries {connection, queries}
                                                                                    ▼
                                                              run_queries_for_connection → ClickHouse → {name:{col:[…]}}  (or HTTP error)
                                                                                    │ inject as window.queries
                                                                                    ▼
                                                              <iframe sandbox="allow-scripts" srcdoc=html> renders
```

The MCP tool and the REST `POST /api/dashboards` are thin wrappers over one
shared upsert-and-push function, mirroring how `push_query` and
`POST /api/remote/push` both wrap `remote.push`. The REST mirror lets the e2e
suite drive the flow with plain `httpx`.

## Backend

### New module `backend/queryview/dashboards.py`

Mirrors `queries.py`; reuses the SQLite engine owned by `connect.py`.

```python
class Dashboard(SQLModel, table=True):
    __tablename__ = "dashboards"
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    connection: str                  # connection name the queries run against
    html: str                        # agent-authored HTML document
    queries: str                     # JSON text: {query_name: SQL}
    updated_at: int                  # unix ms
```

Functions:
- `upsert_dashboard(name, connection, html, queries: dict[str, str]) -> None`
  — upsert by `name`; `queries` serialized to JSON text.
- `get_dashboard(name) -> dict | None` — `{name, connection, html, queries}`
  with `queries` parsed back to a dict; `None` if absent.
- `list_dashboards() -> list[dict]` — `[{name, connection, updated_at}, …]`
  ordered by `name` (no `html`/`queries` payload in the list).

### New connection-scoped query runner (`backend/queryview/connect.py`)

`run_queries_for_connection(name, queries: dict[str, str]) -> dict` — decoupled
from session/cookie state. **Fail-fast**: any problem aborts the whole call.
- Looks up the saved connection by name (`_connection_by_name`); if absent,
  returns `{"ok": False, "reason": "no-connection", "message": 'no connection
  named "<name>"'}`.
- Uses the connection's **stored database**; if none is set, returns
  `{"ok": False, "reason": "no-database", "message": …}` advising to select one
  (queries may still fully-qualify `db.table`, but no default database is
  supplied).
- Runs each query through the existing `ch_query` path (wrapped in a paginated
  subselect with a dashboard result cap; no `order_by`), parsing the
  `TabSeparatedWithNames` output (first line = column names) into a
  column-oriented dict `{col: [values, …]}`, preserving column order. The
  **first** query that fails aborts the run and returns
  `{"ok": False, "reason": "query", "message": "<name>: <error>"}` (the failing
  panel's name is included so the agent can fix it).
- On full success returns `{"ok": True, "results": {name: {col: […]}}}`.

Result cap: a module constant (e.g. `DASHBOARD_ROW_CAP = 1000`) applied as the
`LIMIT` of the paginating subselect, matching the existing 1000-row ceiling on
`/api/clickhouse/query`.

### Endpoints (`backend/queryview/main.py`)

Declared **before** the SPA catch-all so they are not shadowed.

- `POST /api/runqueries` — body `{connection: str, queries: {name: SQL}}`.
  Validates `connection` is a non-empty string and `queries` is a non-empty
  mapping of string→string (others ignored), else `400`. Calls
  `run_queries_for_connection` and maps its result to an HTTP status:
  `200 {ok:true, results:{name:{col:[…]}}}` on success; on failure a body
  `{ok:false, message}` with status by `reason` — `404` for `no-connection`,
  `400` for `no-database` or a failing `query`.
- `POST /api/dashboards` — REST mirror of the MCP tool. Body
  `{name, connection, html, queries, session_id?}`. Validates `name`,
  `connection`, `html` non-empty and `queries` a string→string mapping. Calls
  the shared `_upsert_and_push` helper (persist via `dashboards.py`, then, if
  `session_id` given, best-effort `remote.push`). Returns
  `{ok, persisted, pushed, message}`. 400 on missing required fields.
- `GET /api/dashboards` — `{dashboards: list_dashboards()}`.
- `GET /api/dashboards/{name}` — `get_dashboard(name)` or 404
  `{error: "not found"}`.

`_upsert_and_push(name, connection, html, queries, session_id)` — the shared
helper both the MCP tool and the REST endpoint call:
1. `dashboards.upsert_dashboard(...)` (always; `persisted = True`).
2. If `session_id`: `ok, msg = remote.push(session_id, {type:"dashboard",
   name, connection, html, queries})`; `pushed = ok`.
3. Return `(persisted, pushed, message)`.

### SSE event naming (`backend/queryview/main.py`)

Generalize `_event_stream`: emit the SSE event named by the payload's `type`
field (`query` or `dashboard`) instead of hardcoding `"query"`:

```python
yield _sse(msg.get("type", "query"), msg)
```

The browser listens for both `query` and `dashboard` events. Existing
`push_query` payloads carry `type: "query"`, so behavior is unchanged.

### MCP tool (`backend/queryview/mcp_server.py`)

```python
@mcp.tool()
async def upsert_dashboard(session_id: str, name: str, connection: str,
                           html: str, queries: dict[str, str]) -> dict:
    """Create or update a dashboard and push it to a live QueryView session.

    Persists the dashboard (HTML + named SQL) by name, then pushes it to the
    browser identified by session_id (the id from the QueryView agent popover),
    which navigates to it and renders it. The dashboard's queries run against
    the named connection. Returns {ok, persisted, pushed, message}.
    """
    persisted, pushed, message = await _upsert_and_push(
        name, connection, html, queries, session_id)
    return {"ok": persisted, "persisted": persisted, "pushed": pushed,
            "message": message}
```

`_upsert_and_push` lives where both `main.py` and `mcp_server.py` can import it
without a cycle — either in `dashboards.py` (pulling in `remote`) or a small
shared spot. Chosen home: `dashboards.py` already owns persistence; it imports
`remote` (a leaf module) to do the push, keeping `main.py`/`mcp_server.py` thin.

### Dependency

`mcp` already present. No new backend dependency.

## Frontend

### New dependency

`react-router-dom` (v7) added to `frontend/package.json`.

### `App.tsx` — shell

`App` becomes the shell:
- Wraps everything in `<BrowserRouter>`.
- Owns **connection state** (fetched once via `/api/session`; `?connection=`
  handling stays here) and the **persistent connection pill + agent popover**
  (moved out of the old `App` body) so they show on every page.
- Owns the **armed / SSE remote-control** state (`armed`, `remoteId`,
  `agentOpen`). The SSE listener handles both events:
  - `query` event → store payload, navigate to `/queries`.
  - `dashboard` event → store payload, navigate to `/dashboard?name=<name>`.
- A small **nav** (links: Queries / Dashboard).
- Routes:
  | Path | Element |
  |------|---------|
  | `/queries` | `<QueryView … pushed={queryPush} />` |
  | `/dashboard` | `<DashboardView pushed={dashboardPush} />` (reads `?name=`) |
  | `/` | `<Navigate to="/queries" replace />` |

Navigation on push uses `useNavigate`. A pushed dashboard payload is passed to
`DashboardView` so a freshly-pushed dashboard renders without a refetch; opening
`/dashboard?name=x` directly (or after reload) fetches it via the API.

### `QueryView.tsx` — renamed from today's `App` body

The existing prompt/query workflow, minus the connection pill + agent popover
(now in the shell). It keeps the prompt, command parsing, `DatabasePicker`,
`ClickHouseForm`, and `QueryPanel`, and still consumes `pushed` query payloads.
The `dashboard <name>` command navigates to `/dashboard?name=<name>`.

### `DashboardView.tsx` — the dashboard page (`/dashboard?name=x`)

Props: optional `pushed` payload (`{name, connection, html, queries}`). The
selected dashboard name comes from the `?name=` query param (via
`useSearchParams`).

- **Dropdown selector** at the top: fetches `GET /api/dashboards` and lists all
  saved dashboards by name. Changing the selection sets `?name=<name>` (so the
  URL is shareable and the back button works). Empty state when none exist.
  `data-testid="dashboard-select"`.
- Resolve the active dashboard: use `pushed` if its `name` matches `?name=`,
  otherwise `GET /api/dashboards/{name}`. A push also updates `?name=` so the
  dropdown reflects it.
- On the resolved dashboard: `POST /api/runqueries` with
  `{connection, queries}`. On `200`, store `results` (the
  `{name:{col:[…]}}` map). On a non-2xx response, show a dashboard-level error
  banner (with the returned `message`) instead of rendering the iframe.
- Build the iframe `srcdoc`: a `<script>window.queries = <safe-json></script>`
  prologue followed by the dashboard's `html`. The JSON is produced with
  `JSON.stringify` and `<` escaped (`<`) so an embedded `</script>` cannot
  break out.
- Render `<iframe sandbox="allow-scripts" srcdoc={srcdoc}
  data-testid="dashboard-frame" />` at full width. Loading and error states
  (missing dashboard, runqueries error). `data-testid="dashboard-view"`.

`window.queries` is the documented contract for dashboard authors — a
column-oriented map: `{query_name: {column_name: unknown[]}}`. So
`window.queries.sales.revenue` is the `revenue` column's values as a list.
(If any query fails, the request fails as a whole and the iframe is not
rendered, so `window.queries` always holds a complete set of results — every
named query is present.)

## Result / payload shapes

- Push payload (SSE `dashboard` event):
  `{type:"dashboard", name, connection, html, queries:{name:SQL}}`.
- `/api/runqueries` — success `200 {ok:true, results:{name:{col:[…]}}}`;
  failure `4xx {ok:false, message}` (404 unknown connection, 400 bad body /
  no database / failing query).
- Injected into the iframe: `window.queries = results = {name:{col:[…]}}`.

## Routing & static serving

`main.py`'s `SERVE_STATIC` SPA fallback already returns `index.html` for any
unknown path, so `/queries` and `/dashboard` deep-links work in production;
Vite's dev server provides the same fallback. No backend routing change needed
beyond the new `/api/*` endpoints.

## Edge cases

- **Push to unknown/disarmed session_id** — `remote.push` returns
  `(False, "unknown or inactive session")`; the tool reports `pushed:false`
  while `persisted:true`. The dashboard is still saved and openable by name.
- **Unknown connection in `/runqueries`** — `404 {ok:false, message:'no
  connection named "<name>"'}`; `DashboardView` shows an error banner.
- **Connection has no selected database** — `400` with a message advising to
  select a database (or fully-qualify table names); no iframe rendered.
- **One failing query among many** — fail-fast: the whole request returns
  `400` with `"<panel>: <error>"`; the dashboard shows the error banner and no
  panels render (so `window.queries` is never partial).
- **`</script>` / `<` in result data** — escaped in the injected JSON so it
  cannot break out of the prologue script.
- **Iframe isolation** — `sandbox="allow-scripts"` without `allow-same-origin`
  gives the agent HTML an opaque origin: it can run JS and load CDN assets but
  cannot read the app's cookies, `localStorage`, or reach `/api/*` with
  credentials. All data reaches it only via the injected `window.queries`.
- **Reopen after reload** — `/dashboard?name=x` with no `pushed` payload fetches
  from the store and re-runs queries; the dropdown preselects `x`.
- **`/dashboard` with no `?name=`** — render the dropdown with no iframe (prompt
  to pick one); if dashboards exist, optionally default to the first.
- **Backend restart** — in-memory hub drops as today; persisted dashboards
  survive (SQLite). A stale `session_id` push returns not-delivered.

## Security / scope

Matches the app's posture: no auth beyond the opt-in arming toggle for live
push; persisted dashboards are global (shared SQLite), keyed by name, like
`predefined_queries`. Agent HTML is untrusted and confined to a no-same-origin
sandboxed iframe; it receives only query results, never connection secrets or
the session cookie.

## Testing

- **Backend unit (`backend/tests/`)**
  - `dashboards.py`: upsert creates then updates by name; `get_dashboard`
    round-trips the queries dict; `list_dashboards` ordering and omitted payload.
  - `run_queries_for_connection`: unknown connection → `reason="no-connection"`;
    a good query → `{name: {col: […]}}` (column-oriented); a bad query →
    `ok=False, reason="query"` with the panel name in the message (against CI's
    real ClickHouse, like the existing query tests).
  - `/api/runqueries` route: `400` on missing `connection`/`queries`; `404` on
    unknown connection; `400` on a failing query; `200` success shape
    `{ok, results:{name:{col:[…]}}}`.
  - `/api/dashboards` route: upsert persists and (with an armed `session_id`)
    pushes; `GET` list and by-name.
- **e2e (Playwright + httpx, CI's real ClickHouse)**
  - connect → select a database → arm remote control → read the session id →
    `httpx POST /api/dashboards` with `{session_id, name, connection, html, queries}`
    where `html` reads `window.queries` and writes a value into the DOM →
    assert the browser navigated to `/dashboard?name=x` and the iframe renders
    the expected value → reload / navigate directly to `/dashboard?name=x` and
    assert it re-renders from the store → check the dropdown lists it.
- **MCP** — light check that the tool is registered / `/mcp` still responds;
  the REST mirror exercises the same `_upsert_and_push` path.

## Docs

- New `docs/dashboard.md` — the dashboard page, the `upsert_dashboard` tool, the
  `window.queries` contract for HTML authors, `/api/runqueries`, the
  connection-name model, and the end-to-end flow.
- `docs/api.md` — rows for `POST /api/runqueries`, `POST /api/dashboards`,
  `GET /api/dashboards`, `GET /api/dashboards/{name}`; note the `upsert_dashboard`
  MCP tool and the `dashboard` SSE event.
- `docs/queryview.md` — note the two pages (`/queries`, `/dashboard`) / nav and
  the `dashboard <name>` command; cross-link `dashboard.md`.

## Out of scope

- Returning query results to the agent (browser is the consumer).
- Per-connection-type dashboard keying (dashboards are global by name).
- Dashboard deletion / rename UI.
- Multi-worker hub sharing and any auth beyond the opt-in arming toggle.
- Charting on the app side — charts are the agent HTML's concern (via CDN).
