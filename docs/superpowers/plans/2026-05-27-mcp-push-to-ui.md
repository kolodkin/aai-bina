# MCP push-to-UI layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an MCP client push a SQL query (with limit/offset/order-by/visible-fields) to a live, opted-in QueryView browser session, which fills its query panel and auto-runs it.

**Architecture:** A new in-memory hub (`remote.py`) holds one queue per armed browser channel, keyed by a random public id. The browser opens an SSE stream (`/api/remote/events`) when the user arms "remote control"; an MCP tool `push_query` (FastMCP mounted at `/mcp`) and a REST endpoint (`POST /api/remote/push`, used by e2e) both enqueue onto a channel; the browser receives the payload over SSE and runs it through the **existing** `/api/clickhouse/query`.

**Tech Stack:** FastAPI + Starlette SSE (`StreamingResponse`), the `mcp` Python SDK (FastMCP, Streamable HTTP), React 19 + TypeScript (Vite), Playwright (pytest) e2e.

---

## File structure

- **Create** `backend/queryview/remote.py` — the push hub: `register` / `unregister` / `push` / `next_message`. Pure in-memory state; **no** HTTP or ClickHouse concerns. (SSE framing + disconnect handling live in `main.py`, which is the HTTP layer.)
- **Create** `backend/queryview/mcp_server.py` — the `FastMCP` instance and the single `push_query` tool, delegating to `remote.py`.
- **Modify** `backend/queryview/main.py` — add the SSE + REST endpoints, mount the MCP app at `/mcp`, and add the app lifespan that runs the MCP session manager.
- **Modify** `pyproject.toml` — add the `mcp` dependency.
- **Create** `backend/tests/test_remote.py` — fast unit/TestClient tests for the hub and the REST endpoint (no browser, no ClickHouse).
- **Create** `e2e/test_remote.py` — the end-to-end push flow (real browser + real ClickHouse).
- **Modify** `frontend/src/App.tsx` — lift remote-control state to `App` (agent icon + popover by the status pill, the `EventSource`), and add the pushed-query apply path to `QueryPanel`.
- **Create** `docs/remote.md`; **Modify** `docs/api.md`, `docs/queryview.md`.

## Test / dev loop (used by several tasks)

ClickHouse is already running on `:8123` (started via `scripts/setup_clickhouse.sh`; `curl -sf localhost:8123/ping` → `Ok.`). Backend unit tests need neither ClickHouse nor a browser. The e2e tests need the built SPA served by the backend + ClickHouse, exactly like CI (`scripts/setup_browser.sh`). To (re)run e2e:

```bash
# once per session:
npm ci
uv sync --group test
uv run --group test playwright install chromium

# build the SPA and (re)start the backend serving it on :8000
npm run build -w frontend
pkill -f queryview-backend 2>/dev/null || true
SERVE_STATIC=1 PORT=8000 DB_PATH=.cache/queryview.db \
  uv run --group test queryview-backend > .cache/backend.log 2>&1 &
until curl -sf localhost:8000/api/health >/dev/null; do sleep 0.5; done

# run e2e against the served SPA
BASE_URL=http://localhost:8000 uv run --group test pytest e2e/test_remote.py -v
```

Frontend-only changes need only `npm run build -w frontend` + a page reload (the backend serves `frontend/dist` from disk per request). Backend Python changes need the backend restarted (the `pkill` + start lines above).

---

### Task 1: Push hub (`remote.py`)

**Files:**
- Create: `backend/queryview/remote.py`
- Test: `backend/tests/test_remote.py`

- [ ] **Step 1: Ensure test deps are installed**

Run: `uv sync --group test`
Expected: succeeds (pytest available in the project venv).

- [ ] **Step 2: Write the failing unit tests**

Create `backend/tests/test_remote.py`:

```python
import asyncio

from queryview import remote


def test_register_returns_distinct_ids():
    a = remote.register()
    b = remote.register()
    assert a and b and a != b
    remote.unregister(a)
    remote.unregister(b)


def test_push_to_registered_session_delivers():
    rid = remote.register()
    try:
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True
        msg_in = asyncio.run(remote.next_message(rid, 1.0))
        assert msg_in == {"type": "query", "query": "SELECT 1"}
    finally:
        remote.unregister(rid)


def test_push_to_unknown_session_fails():
    ok, msg = remote.push("deadbeef", {"type": "query", "query": "SELECT 1"})
    assert ok is False
    assert "unknown" in msg.lower()


def test_unregister_makes_push_fail():
    rid = remote.register()
    remote.unregister(rid)
    ok, _ = remote.push(rid, {"type": "query", "query": "SELECT 1"})
    assert ok is False


def test_next_message_times_out_to_none():
    rid = remote.register()
    try:
        assert asyncio.run(remote.next_message(rid, 0.05)) is None
    finally:
        remote.unregister(rid)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run --group test pytest backend/tests/test_remote.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'queryview.remote'`.

- [ ] **Step 4: Implement `remote.py`**

Create `backend/queryview/remote.py`:

```python
"""In-memory push hub for the remote-control layer: one message queue per armed
browser channel, keyed by a random public id. No HTTP-server or ClickHouse
concerns live here — the SSE framing and disconnect handling live in main.py."""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Channel:
    queue: "asyncio.Queue[dict[str, Any]]" = field(default_factory=asyncio.Queue)


# remote_id -> channel. Module-level, like connect.py's _sessions.
_channels: dict[str, _Channel] = {}


def register() -> str:
    """Create a channel for a newly-armed browser session; return its public id.
    The id is random and unrelated to the qv_session cookie, so the session
    secret is never exposed to the agent."""
    remote_id = secrets.token_hex(4)
    _channels[remote_id] = _Channel()
    return remote_id


def unregister(remote_id: str) -> None:
    """Drop a channel (idempotent)."""
    _channels.pop(remote_id, None)


def push(remote_id: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """Enqueue a payload for a channel. (False, message) if no such channel."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
    channel.queue.put_nowait(payload)
    return True, "delivered"


async def next_message(remote_id: str, timeout: float) -> dict[str, Any] | None:
    """Wait up to `timeout` seconds for the next payload on a channel. Returns
    None on timeout or if the channel is gone."""
    channel = _channels.get(remote_id)
    if channel is None:
        return None
    try:
        return await asyncio.wait_for(channel.queue.get(), timeout)
    except asyncio.TimeoutError:
        return None
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --group test pytest backend/tests/test_remote.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/queryview/remote.py backend/tests/test_remote.py
git commit -m "Add in-memory push hub for the remote-control layer"
```

---

### Task 2: MCP server (`mcp_server.py`) + dependency

**Files:**
- Modify: `pyproject.toml` (add `mcp` to `[project].dependencies`)
- Create: `backend/queryview/mcp_server.py`

- [ ] **Step 1: Add the `mcp` dependency**

Edit `pyproject.toml`, adding `"mcp>=1.9"` to the `dependencies` list:

```toml
dependencies = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "sqlmodel>=0.0.16",
    "httpx>=0.27",
    "cryptography>=42",
    "aiosqlite>=0.22.1",
    "mcp>=1.9",
]
```

- [ ] **Step 2: Install and verify the FastMCP API**

Run:
```bash
uv sync && uv run python -c "
from mcp.server.fastmcp import FastMCP
m = FastMCP('t', stateless_http=True)
app = m.streamable_http_app()
assert m.session_manager is not None
import mcp as _m; print('mcp', getattr(_m, '__version__', '?'), 'OK')
"
```
Expected: prints `mcp <version> OK` with no exception. (If `stateless_http`/`session_manager` raise, bump the `mcp` version and re-run — the rest of the plan relies on these three symbols.)

- [ ] **Step 3: Implement `mcp_server.py`**

Create `backend/queryview/mcp_server.py`:

```python
"""The MCP layer: a FastMCP server (mounted by main.py at /mcp) exposing a
single tool that pushes a query to a live QueryView browser session. Tools
delegate to the in-process remote.py hub."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import remote

mcp = FastMCP("queryview", stateless_http=True)


@mcp.tool()
async def push_query(
    session_id: str,
    query: str,
    limit: int = 100,
    offset: int = 0,
    order_by: list[dict[str, Any]] | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Push a SQL query to a live QueryView browser session.

    The targeted browser fills its query panel and auto-runs the query. Get the
    `session_id` from the QueryView UI: the agent icon next to the connection
    status pill, after enabling "Allow remote control".

    Args:
        session_id: The session id shown in the QueryView popover.
        query: The SQL to run.
        limit: Page size (default 100).
        offset: Row offset (default 0).
        order_by: Optional sort, e.g. [{"name": "id", "dir": "DESC"}].
        fields: Optional column names to display; omit to show all columns.
    """
    payload = {
        "type": "query",
        "query": query,
        "limit": limit,
        "offset": offset,
        "order_by": order_by,
        "fields": fields,
    }
    ok, message = remote.push(session_id, payload)
    return {"ok": ok, "message": message}
```

- [ ] **Step 4: Verify it imports**

Run: `uv run python -c "from queryview.mcp_server import mcp; print([t.name for t in __import__('asyncio').get_event_loop().run_until_complete(mcp.list_tools())])"`
Expected: prints `['push_query']` (a list containing `push_query`).

If the one-liner is awkward, instead run:
```bash
uv run python -c "from queryview.mcp_server import mcp; print('import OK')"
```
Expected: `import OK`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock backend/queryview/mcp_server.py
git commit -m "Add FastMCP server with push_query tool"
```

---

### Task 3: Backend endpoints + MCP mount (`main.py`)

**Files:**
- Modify: `backend/queryview/main.py`
- Test: `backend/tests/test_remote.py` (append REST tests)

- [ ] **Step 1: Add REST endpoint tests (TestClient)**

Append to `backend/tests/test_remote.py`:

```python
from fastapi.testclient import TestClient

from queryview.main import app


def test_push_endpoint_requires_query():
    client = TestClient(app)
    r = client.post("/api/remote/push", json={"session_id": "x", "query": ""})
    assert r.status_code == 400


def test_push_endpoint_unknown_session_returns_not_delivered():
    client = TestClient(app)
    r = client.post(
        "/api/remote/push",
        json={"session_id": "deadbeef", "query": "SELECT 1"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_push_endpoint_delivers_to_registered_session():
    import asyncio
    from queryview import remote

    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/remote/push",
            json={
                "session_id": rid,
                "query": "SELECT id, name FROM items",
                "limit": 5,
                "order_by": [{"name": "id", "dir": "DESC"}],
                "fields": ["name"],
            },
        )
        assert r.json()["ok"] is True
        msg = asyncio.run(remote.next_message(rid, 1.0))
        assert msg["type"] == "query"
        assert msg["query"] == "SELECT id, name FROM items"
        assert msg["limit"] == 5
        assert msg["order_by"] == [{"name": "id", "dir": "DESC"}]
        assert msg["fields"] == ["name"]
    finally:
        remote.unregister(rid)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run --group test pytest backend/tests/test_remote.py -k push_endpoint -v`
Expected: FAIL — the `/api/remote/push` route does not exist yet (404), so the assertions fail.

- [ ] **Step 3: Update imports in `main.py`**

In `backend/queryview/main.py`, change the imports near the top. Replace:

```python
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
```

with:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import remote
from .mcp_server import mcp
```

(Keep the existing `from .clickhouse import ...`, `from .connect import ...`, and `from .queries import ...` lines as they are.)

- [ ] **Step 4: Add the lifespan and mount the MCP app**

In `main.py`, replace the single line:

```python
app = FastAPI(title="queryview-backend")
```

with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # A mounted Starlette sub-app's lifespan is not run by the parent, so run
    # the MCP session manager here. streamable_http_app() (called at mount,
    # below) initializes mcp.session_manager before this runs at startup.
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="queryview-backend", lifespan=lifespan)
app.mount("/mcp", mcp.streamable_http_app())
```

- [ ] **Step 5: Add the SSE + push endpoints**

In `main.py`, insert the following **after** the `predefined_queries_save` route and **before** the `@app.api_route("/api/{rest:path}", ...)` (`api_not_found`) route:

```python
# --- Remote control (MCP push -> live browser session) --------------------

_SSE_POLL_SECONDS = 1.0
_SSE_HEARTBEAT_SECONDS = 15.0


def _sse(event: str, data: dict[str, Any]) -> bytes:
    import json

    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


async def _event_stream(remote_id: str, request: Request):
    """Yield SSE: a `ready` event with the id, then `query` events as they are
    pushed, plus a heartbeat. Polls disconnect every second so disarming (the
    browser closing the EventSource) unregisters the channel promptly."""
    try:
        yield _sse("ready", {"id": remote_id})
        elapsed = 0.0
        while True:
            if await request.is_disconnected():
                break
            msg = await remote.next_message(remote_id, _SSE_POLL_SECONDS)
            if msg is None:
                elapsed += _SSE_POLL_SECONDS
                if elapsed >= _SSE_HEARTBEAT_SECONDS:
                    elapsed = 0.0
                    yield b": ping\n\n"
                continue
            yield _sse("query", msg)
    finally:
        remote.unregister(remote_id)


# Open an SSE channel for this browser; the browser does this when the user
# arms "remote control". Closing the EventSource unregisters the channel.
@app.get("/api/remote/events")
async def remote_events(request: Request):
    remote_id = remote.register()
    return StreamingResponse(
        _event_stream(remote_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Push a query to a live session (used by the MCP tool and, in tests, directly).
@app.post("/api/remote/push")
async def remote_push(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    raw_sid = b.get("session_id")
    session_id = raw_sid.strip() if isinstance(raw_sid, str) else ""
    raw_sql = b.get("query")
    query = raw_sql.strip() if isinstance(raw_sql, str) else ""
    if not session_id or not query:
        return JSONResponse(
            {"ok": False, "message": "session_id and query are required"},
            status_code=400,
        )
    limit = _parse_int(b.get("limit"), 100)
    offset = _parse_int(b.get("offset"), 0)
    raw_order = b.get("order_by")
    order_by = raw_order if isinstance(raw_order, list) else None
    raw_fields = b.get("fields")
    fields = (
        [f for f in raw_fields if isinstance(f, str)]
        if isinstance(raw_fields, list)
        else None
    )
    payload = {
        "type": "query",
        "query": query,
        "limit": limit,
        "offset": offset,
        "order_by": order_by,
        "fields": fields,
    }
    ok, message = remote.push(session_id, payload)
    return {"ok": ok, "message": message}
```

- [ ] **Step 6: Run the REST tests to verify they pass**

Run: `uv run --group test pytest backend/tests/test_remote.py -v`
Expected: PASS (all tests, including the three `push_endpoint` tests).

- [ ] **Step 7: Verify the server boots and `/mcp` is mounted**

Run:
```bash
pkill -f queryview-backend 2>/dev/null || true
SERVE_STATIC=1 PORT=8000 DB_PATH=.cache/queryview.db \
  uv run --group test queryview-backend > .cache/backend.log 2>&1 &
until curl -sf localhost:8000/api/health >/dev/null; do sleep 0.5; done
echo "health:"; curl -s localhost:8000/api/health
echo; echo "mcp mounted (expect a non-404 status, e.g. 400/406):"
curl -s -o /dev/null -w "%{http_code}\n" -X POST localhost:8000/mcp/
echo "backend log tail:"; tail -5 .cache/backend.log
```
Expected: health returns `{"status":"ok",...}`; the `/mcp/` POST returns a non-404 status (the MCP handler rejects the bare request, e.g. `400` or `406`, proving the mount is reachable and the lifespan started without error). The log shows no traceback.

- [ ] **Step 8: Commit**

```bash
git add backend/queryview/main.py backend/tests/test_remote.py
git commit -m "Wire SSE + push endpoints and mount the MCP app"
```

---

### Task 4: e2e test for the push flow (red)

**Files:**
- Create: `e2e/test_remote.py`

- [ ] **Step 1: Write the e2e test**

Create `e2e/test_remote.py`:

```python
import httpx
from playwright.sync_api import Page, expect


def _open_query_panel(page: Page) -> None:
    """Connect with form defaults, select the seeded `test` db, open the panel."""
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    page.get_by_test_id("ch-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="test"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - test")
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()


def test_mcp_push_to_live_session(seeded_test_db, page: Page, base_url: str) -> None:
    _open_query_panel(page)

    # Arm remote control via the agent popover, then read the session id.
    page.get_by_test_id("agent-toggle").click()
    expect(page.get_by_test_id("agent-panel")).to_be_visible()
    page.get_by_test_id("remote-arm").check()
    sid_el = page.get_by_test_id("remote-session-id")
    expect(sid_el).to_be_visible()
    session_id = sid_el.inner_text().strip()
    assert session_id

    # Push a query the way an MCP client would (via the REST surface).
    res = httpx.post(
        f"{base_url}/api/remote/push",
        json={
            "session_id": session_id,
            "query": "SELECT id, name FROM items",
            "limit": 10,
            "order_by": [{"name": "id", "dir": "DESC"}],
            "fields": ["name"],
        },
        timeout=10.0,
    )
    res.raise_for_status()
    assert res.json()["ok"] is True

    # The browser filled the SQL box and auto-ran; only the pushed field shows.
    expect(page.get_by_test_id("query-input")).to_have_value("SELECT id, name FROM items")
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output.locator("table thead th")).to_have_count(1)
    expect(output.locator("table thead th")).to_contain_text("name")
    expect(output).to_contain_text("gamma")  # id DESC -> gamma first

    # Disarm: the popover no longer exposes the session id...
    page.get_by_test_id("remote-arm").uncheck()
    expect(page.get_by_test_id("remote-session-id")).to_have_count(0)

    # ...and a push to an unknown/inactive id is reported as not delivered.
    res2 = httpx.post(
        f"{base_url}/api/remote/push",
        json={"session_id": "deadbeef", "query": "SELECT 1"},
        timeout=10.0,
    )
    assert res2.json()["ok"] is False
```

- [ ] **Step 2: Build the SPA and (re)start the backend, then run the test**

Run (see "Test / dev loop"):
```bash
npm run build -w frontend
pkill -f queryview-backend 2>/dev/null || true
SERVE_STATIC=1 PORT=8000 DB_PATH=.cache/queryview.db \
  uv run --group test queryview-backend > .cache/backend.log 2>&1 &
until curl -sf localhost:8000/api/health >/dev/null; do sleep 0.5; done
BASE_URL=http://localhost:8000 uv run --group test pytest e2e/test_remote.py -v
```
Expected: FAIL — timeout waiting for `agent-toggle` (the UI doesn't exist yet). This confirms the test exercises the new UI.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_remote.py
git commit -m "Add e2e test for MCP push-to-UI (red)"
```

---

### Task 5: Frontend — agent icon, popover, SSE, and the pushed-query apply path

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add the `PushPayload` type**

In `frontend/src/App.tsx`, after the `OrderCol` type, add:

```tsx
type PushPayload = {
  query: string
  limit?: number
  offset?: number
  order_by?: OrderCol[]
  fields?: string[]
}
```

- [ ] **Step 2: Add remote-control state and the EventSource effect to `App`**

In the `App` component, after `const [showQuery, setShowQuery] = useState(false)`, add:

```tsx
  const [armed, setArmed] = useState(false)
  const [remoteId, setRemoteId] = useState<string | null>(null)
  const [pushed, setPushed] = useState<PushPayload | null>(null)
  const [agentOpen, setAgentOpen] = useState(false)
```

After the existing session-resume `useEffect`, add a second effect:

```tsx
  // When armed, open an SSE channel: `ready` gives this session's id; each
  // `query` event carries a pushed query for the panel to run.
  useEffect(() => {
    if (!armed) return
    const es = new EventSource('/api/remote/events')
    es.addEventListener('ready', (e) => {
      try {
        setRemoteId(JSON.parse((e as MessageEvent).data).id as string)
      } catch {
        /* ignore malformed event */
      }
    })
    es.addEventListener('query', (e) => {
      try {
        setPushed(JSON.parse((e as MessageEvent).data) as PushPayload)
      } catch {
        /* ignore malformed event */
      }
    })
    return () => {
      es.close()
      setRemoteId(null)
    }
  }, [armed])

  function toggleArm(e: React.ChangeEvent<HTMLInputElement>) {
    const next = e.target.checked
    setArmed(next)
    if (next) setShowQuery(true) // ensure the query panel is mounted to receive pushes
  }

  const agentCommand = `Use the queryview MCP to push queries to QueryView session "${remoteId ?? ''}".`
```

- [ ] **Step 3: Render the agent icon + popover beside the status pill**

In `App`'s returned JSX, replace the connection-status block:

```tsx
      {connection?.database && (
        <div
          className="absolute left-4 top-4 flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium shadow-sm"
          data-testid="connection-status"
        >
          <span
            className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500"
            data-testid="connection-indicator"
            aria-label="connected"
          />
          connected - {connection.database}
        </div>
      )}
```

with:

```tsx
      {connection?.database && (
        <div className="absolute left-4 top-4 flex items-center gap-2">
          <div
            className="flex items-center gap-2 rounded-full border border-slate-200 bg-white px-3 py-1.5 text-sm font-medium shadow-sm"
            data-testid="connection-status"
          >
            <span
              className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500"
              data-testid="connection-indicator"
              aria-label="connected"
            />
            connected - {connection.database}
          </div>
          <div className="relative">
            <button
              type="button"
              data-testid="agent-toggle"
              onClick={() => setAgentOpen((o) => !o)}
              aria-label="Remote control"
              className={`flex h-8 w-8 items-center justify-center rounded-full border shadow-sm transition ${
                armed
                  ? 'border-indigo-600 bg-indigo-600 text-white'
                  : 'border-slate-200 bg-white text-slate-600 hover:border-indigo-400'
              }`}
            >
              <svg
                viewBox="0 0 24 24"
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <rect x="4" y="8" width="16" height="11" rx="2" />
                <path d="M12 8V4M9 3h6" />
                <circle cx="9" cy="13" r="1" />
                <circle cx="15" cy="13" r="1" />
              </svg>
            </button>
            {agentOpen && (
              <div
                data-testid="agent-panel"
                className="absolute left-0 top-full z-10 mt-2 w-72 rounded-lg border border-slate-200 bg-white p-3 text-sm shadow-lg"
              >
                <label className="flex items-center gap-2 font-medium text-slate-700">
                  <input
                    type="checkbox"
                    data-testid="remote-arm"
                    checked={armed}
                    onChange={toggleArm}
                  />
                  Allow remote control
                </label>
                {armed && remoteId && (
                  <div className="mt-3 space-y-2">
                    <div className="text-xs text-slate-500">Session id</div>
                    <code
                      data-testid="remote-session-id"
                      className="block rounded bg-slate-100 px-2 py-1 font-mono text-slate-800"
                    >
                      {remoteId}
                    </code>
                    <button
                      type="button"
                      data-testid="remote-copy"
                      onClick={() => void navigator.clipboard?.writeText(agentCommand)}
                      className="rounded-md border border-indigo-600 px-2 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
                    >
                      Copy agent command
                    </button>
                    <p className="text-xs text-slate-500">{agentCommand}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}
```

- [ ] **Step 4: Pass `pushed` into `QueryPanel`**

In `App`'s JSX, update the `QueryPanel` usage:

```tsx
        {showQuery && connection?.database && (
          <QueryPanel
            connectionType={connection.type}
            promptSlot={promptInput}
            pushed={pushed}
          />
        )}
```

- [ ] **Step 5: Add the `pushed` prop and the `runWith` refactor to `QueryPanel`**

Change the `QueryPanel` signature:

```tsx
function QueryPanel({
  connectionType,
  promptSlot,
  pushed,
}: {
  connectionType: string
  promptSlot?: React.ReactNode
  pushed?: PushPayload | null
}) {
```

Replace the existing `async function run(nextOffset: number) { ... }` with:

```tsx
  async function runWith(
    q: string,
    lim: number,
    off: number,
    ord: OrderCol[],
    selectFields?: string[],
  ) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, limit: lim, offset: off, format: 'text', order_by: ord }),
      })
      const data = await res.json()
      if (data.ok) {
        const text = data.output as string
        setOutput(text)
        setOffset(off)
        // A pushed selection is authoritative: synthesize the field list from
        // the actual result columns so the existing visibility filter restricts
        // the table to exactly the pushed columns (empty/absent => show all).
        if (selectFields !== undefined) {
          const cols = parseTsv(text).columns
          setFields(cols.map((name) => ({ name, type: '' })))
          setVisibleCols(
            selectFields.length ? selectFields.filter((f) => cols.includes(f)) : cols,
          )
        }
      } else {
        setError(data.message ?? 'query failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  function run(nextOffset: number) {
    void runWith(sql, limit, nextOffset, orderBy)
  }
```

(The existing call sites — `() => void run(offset)`, `() => void run(offset + limit)`, `() => void run(Math.max(0, offset - limit))`, and the order-by `onClick={() => void run(offset)}` — keep working unchanged.)

- [ ] **Step 6: Add the apply effect to `QueryPanel`**

Add this effect inside `QueryPanel` (next to the existing `useEffect` for `loadPredefined`):

```tsx
  // Apply a pushed query: reflect it in the controls and run it with the pushed
  // values directly (not state, which hasn't settled yet).
  useEffect(() => {
    if (!pushed) return
    const q = pushed.query
    const lim = pushed.limit ?? 100
    const off = pushed.offset ?? 0
    const ord = pushed.order_by ?? []
    const fld = pushed.fields ?? []
    /* eslint-disable react-hooks/set-state-in-effect */
    setSql(q)
    setLimit(lim)
    setOffset(off)
    setOrderBy(ord)
    /* eslint-enable react-hooks/set-state-in-effect */
    void runWith(q, lim, off, ord, fld)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pushed])
```

- [ ] **Step 7: Type-check and lint**

Run: `npm run build -w frontend && npm run lint -w frontend`
Expected: both succeed (tsc + vite build produce `frontend/dist`; eslint reports no errors).

- [ ] **Step 8: Run the e2e push test to verify it passes**

Run (rebuild already done in Step 7; restart backend to be safe):
```bash
pkill -f queryview-backend 2>/dev/null || true
SERVE_STATIC=1 PORT=8000 DB_PATH=.cache/queryview.db \
  uv run --group test queryview-backend > .cache/backend.log 2>&1 &
until curl -sf localhost:8000/api/health >/dev/null; do sleep 0.5; done
BASE_URL=http://localhost:8000 uv run --group test pytest e2e/test_remote.py -v
```
Expected: PASS (`test_mcp_push_to_live_session`).

- [ ] **Step 9: Run the full e2e suite to check for regressions**

Run: `BASE_URL=http://localhost:8000 uv run --group test pytest e2e -v`
Expected: PASS (all tests — the new test plus the existing `test_app.py` / `test_query.py`; the agent icon is additive and must not break them).

- [ ] **Step 10: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "Add agent icon, remote-control popover, and pushed-query auto-run"
```

---

### Task 6: Docs

**Files:**
- Create: `docs/remote.md`
- Modify: `docs/api.md`, `docs/queryview.md`

- [ ] **Step 1: Write `docs/remote.md`**

Create `docs/remote.md`:

```markdown
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

The backend mounts a FastMCP server (Streamable HTTP) at `/mcp` exposing one
tool:

- `push_query(session_id, query, limit?=100, offset?=0, order_by?, fields?)` —
  push a query to the session. `order_by` is `[{name, dir}]`; `fields` is the
  list of column names to display (omit to show all). Returns
  `{ok, message}`; an unknown/disarmed id returns `{ok: false}`.

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
```

- [ ] **Step 2: Update `docs/api.md`**

Add these rows to the endpoint table in `docs/api.md` (after the predefined-queries rows):

```markdown
| GET    | `/api/remote/events`        | —                                      | SSE stream a browser opens when "remote control" is armed. Emits a `ready` event (`{id}`) then `query` events with pushed payloads. |
| POST   | `/api/remote/push`          | `{session_id, query, limit?, offset?, order_by?, fields?}` | Push a query to a live session (the surface `push_query` and the e2e suite use). `{ok}` \| `{ok:false, message}` (unknown session). Empty `query`/`session_id` → `400`. |
```

Then add a short paragraph after the table:

```markdown
**MCP:** a FastMCP server is mounted at `/mcp` (Streamable HTTP) exposing a
single `push_query` tool that delegates to `/api/remote/push`. See
[remote.md](./remote.md).
```

And add to the "Related docs" list:

```markdown
- [remote.md](./remote.md) — pushing queries to a live session over MCP.
```

- [ ] **Step 3: Update `docs/queryview.md`**

In `docs/queryview.md`, under "Connection status" (the persistent top-left
element), add a sentence:

```markdown
Next to the status pill is an **agent icon** that opens the remote-control
popover (opt-in "Allow remote control"); see [remote.md](./remote.md).
```

- [ ] **Step 4: Commit**

```bash
git add docs/remote.md docs/api.md docs/queryview.md
git commit -m "Document the MCP push-to-UI remote-control layer"
```

---

## Self-review notes

- **Spec coverage:** hub (Task 1), `push_query` tool only / no `list_sessions` (Task 2), SSE + REST push + `/mcp` mount + lifespan (Task 3), e2e of the real flow (Task 4–5), agent icon + popover + opt-in toggle + session id + copyable command (Task 5), pushed `fields` authoritative selection (Task 5 Steps 5–6), docs (Task 6). Push transport = SSE; targeting = UI-surfaced id; opt-in default off — all covered.
- **Boundary refinement vs spec:** the spec sketched `event_stream(remote_id, request)` inside `remote.py`; this plan keeps `remote.py` HTTP-free (`next_message`) and puts SSE framing + disconnect polling in `main.py`. Same behavior, cleaner boundary.
- **Disarm test:** verified deterministically (UI hides the id + push to an unknown id returns `ok:false`) rather than racing the ~1s disconnect poll.
- **Types:** `PushPayload` (frontend) and the payload dict (`type/query/limit/offset/order_by/fields`) are consistent across `remote_push`, `push_query`, the SSE `query` event, and the `QueryPanel` apply effect.
```
