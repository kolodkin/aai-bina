# Query Feature (pagination + predefined + CSV) + Seeded `test` DB E2E — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a SQL query feature to the QueryView SPA — run a query against the session's selected database with LIMIT/OFFSET pagination (Prev/Next), load/save global predefined queries (per connection type), and download the current page as CSV — and cover it with an e2e test against a seeded ClickHouse `test` database. Remove `EXPECT_CLICKHOUSE_OK` so the e2e suite always runs against a real ClickHouse.

**Architecture:** A new `POST /api/clickhouse/query` endpoint paginates by wrapping the user SQL in `SELECT * FROM (…) LIMIT n OFFSET m` and runs it through the existing ClickHouse HTTP helper with a chosen `FORMAT`. Predefined queries live in a new `predefined_queries` SQLite table (new `queries.py` module) keyed by connection `type`, which becomes a stored column on connections. The SPA gains a `query` command revealing a `QueryPanel`.

**Tech Stack:** FastAPI + SQLModel (backend), React + TypeScript + Vite (frontend), pytest-playwright + httpx (e2e), ClickHouse HTTP interface.

**Spec:** `docs/superpowers/specs/2026-05-25-query-feature-design.md`

---

## File Structure

- `backend/queryview/clickhouse.py` — `ch_query` gains `database` + `fmt`.
- `backend/queryview/connect.py` — `Connection.type` column threaded through; add `run_query`.
- `backend/queryview/queries.py` (new) — `PredefinedQuery` model + list/save ops.
- `backend/queryview/main.py` — `_parse_int`; query + predefined endpoints; `type` in `/open`.
- `frontend/src/App.tsx` — `Connection.type`, `query` command, `QueryPanel`.
- `e2e/conftest.py` — `httpx` seed helper + module-scoped `seeded_test_db`.
- `e2e/test_query.py` (new) — pagination + predefined + CSV test.
- `e2e/test_app.py`, `.github/workflows/ci.yml`, `scripts/setup_browser.sh`, `scripts/setup.sh` — drop `EXPECT_CLICKHOUSE_OK`.
- `docs/api.md`, `docs/connect.md` — document new endpoints + `type`.

---

## Task 1: Backend — `ch_query` gains `database` and `fmt`

**Files:** Modify `backend/queryview/clickhouse.py:28-44`

- [ ] **Step 1: Replace `ch_query`**

Replace lines 28-44 with:

```python
async def ch_query(
    c: ChConfig, query: str, database: str | None = None, fmt: str | None = None
) -> ChResult:
    """Run a query against the ClickHouse HTTP interface (Basic auth, 5s timeout).
    `database` scopes the query; `fmt` appends a ClickHouse `FORMAT` clause."""
    url = f"http://{c.host}:{c.port}/"
    q = f"{query}\nFORMAT {fmt}" if fmt else query
    params = {"query": q}
    if database:
        params["database"] = database
    try:
        async with httpx.AsyncClient(timeout=CH_TIMEOUT_SECONDS) as client:
            res = await client.get(url, params=params, auth=(c.username, c.password))
    except httpx.TimeoutException:
        return ChResult(False, "connection timed out")
    except httpx.HTTPError as err:
        return ChResult(False, str(err) or "connection failed")

    text = res.text.strip()
    if not res.is_success:
        return ChResult(False, f"ClickHouse responded {res.status_code}: {text[:200]}")
    return ChResult(True, text)
```

- [ ] **Step 2: Verify it imports + signature**

Run: `cd /home/user/queryview- && uv run python -c "from queryview.clickhouse import ch_query; import inspect; print(list(inspect.signature(ch_query).parameters))"`
Expected: `['c', 'query', 'database', 'fmt']`

- [ ] **Step 3: Commit**

```bash
git add backend/queryview/clickhouse.py
git commit -m "Add database scoping and FORMAT to ch_query"
```

---

## Task 2: Backend — connection `type` column threaded through `connect.py`

**Files:** Modify `backend/queryview/connect.py`

- [ ] **Step 1: Add `type` to the `Connection` model**

Replace:

```python
    name: str = Field(unique=True, index=True)
    host: str
```

with:

```python
    name: str = Field(unique=True, index=True)
    type: str = Field(default="clickhouse", index=True)
    host: str
```

- [ ] **Step 2: Add `type` to `StoredConnection`**

Replace:

```python
@dataclass
class StoredConnection:
    name: str
    config: ChConfig
    database: str | None
```

with:

```python
@dataclass
class StoredConnection:
    name: str
    type: str
    config: ChConfig
    database: str | None
```

- [ ] **Step 3: Store `type` in `_save_active_connection`**

Replace the whole `_save_active_connection` function with:

```python
async def _save_active_connection(name: str, c: ChConfig, conn_type: str = "clickhouse") -> None:
    password = _encrypt_password(c.password)
    now = _now_ms()
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Connection).where(Connection.name == name))).first()
        if row is None:
            row = Connection(
                name=name,
                type=conn_type,
                host=c.host,
                port=c.port,
                username=c.username,
                password=password,
                last_active_at=now,
            )
        else:
            # Upsert by name; the selected database is intentionally left as-is.
            row.type = conn_type
            row.host = c.host
            row.port = c.port
            row.username = c.username
            row.password = password
            row.last_active_at = now
        s.add(row)
        await s.commit()
```

- [ ] **Step 4: Carry `type` in `_row_to_stored`**

Replace:

```python
    return StoredConnection(
        name=row.name,
        config=ChConfig(
            host=row.host, port=row.port, username=row.username, password=password
        ),
        database=row.database,
    )
```

with:

```python
    return StoredConnection(
        name=row.name,
        type=row.type,
        config=ChConfig(
            host=row.host, port=row.port, username=row.username, password=password
        ),
        database=row.database,
    )
```

- [ ] **Step 5: Add `type` to `_SessionState`**

Replace:

```python
@dataclass
class _SessionState:
    name: str
    config: ChConfig
    databases: list[str]
    database: str | None
```

with:

```python
@dataclass
class _SessionState:
    name: str
    type: str
    config: ChConfig
    databases: list[str]
    database: str | None
```

- [ ] **Step 6: Thread `conn_type` through `_build_session`**

Replace the whole `_build_session` function with:

```python
async def _build_session(
    name: str, config: ChConfig, database: str | None, conn_type: str = "clickhouse"
) -> tuple[_SessionState | None, str | None]:
    """List a connection's databases and build a session object."""
    ok, result = await list_databases(config)
    if not ok:
        return None, result  # type: ignore[return-value]
    databases: list[str] = result  # type: ignore[assignment]
    return (
        _SessionState(
            name=name,
            type=conn_type,
            config=config,
            databases=databases,
            database=database if database and database in databases else None,
        ),
        None,
    )
```

- [ ] **Step 7: Pass `stored.type` in `_ensure_session`**

Replace:

```python
    state, _ = await _build_session(stored.name, stored.config, stored.database)
```

with:

```python
    state, _ = await _build_session(stored.name, stored.config, stored.database, stored.type)
```

- [ ] **Step 8: Return `type` from `get_session`**

Replace:

```python
    return {
        "connected": True,
        "name": s.name,
        "databases": s.databases,
        "database": s.database,
    }
```

with:

```python
    return {
        "connected": True,
        "name": s.name,
        "type": s.type,
        "databases": s.databases,
        "database": s.database,
    }
```

- [ ] **Step 9: Pass + return `type` in `connect_new`**

Replace the whole `connect_new` function with:

```python
async def connect_new(sid: str, name: str, config: ChConfig) -> dict[str, Any]:
    """Create: open a config, save + activate it for this session."""
    state, message = await _build_session(name, config, None, "clickhouse")
    if state is None:
        return {"ok": False, "message": message}
    _set_session_entry(sid, state)
    await _save_active_connection(name, config, "clickhouse")
    return {"ok": True, "name": name, "type": state.type, "databases": state.databases}
```

- [ ] **Step 10: Pass + return `type` in `open_saved`**

Replace:

```python
    state, message = await _build_session(stored.name, stored.config, None)
    if state is None:
        return {"ok": False, "message": message}
    _set_session_entry(sid, state)
    await _touch_connection(name)
    return {"ok": True, "name": name, "databases": state.databases}
```

with:

```python
    state, message = await _build_session(stored.name, stored.config, None, stored.type)
    if state is None:
        return {"ok": False, "message": message}
    _set_session_entry(sid, state)
    await _touch_connection(name)
    return {"ok": True, "name": name, "type": state.type, "databases": state.databases}
```

- [ ] **Step 11: Verify import**

Run: `cd /home/user/queryview- && uv run python -c "import queryview.connect; print('ok')"`
Expected: `ok`

- [ ] **Step 12: Commit**

```bash
git add backend/queryview/connect.py
git commit -m "Add a stored connection type, threaded through sessions"
```

---

## Task 3: Backend — `run_query` with pagination

**Files:** Modify `backend/queryview/connect.py` (import line 20; append at end)

- [ ] **Step 1: Import `ch_query`**

Replace line 20:

```python
from .clickhouse import ChConfig, list_databases
```

with:

```python
from .clickhouse import ChConfig, ch_query, list_databases
```

- [ ] **Step 2: Append `run_query` at the end of the file**

```python


async def run_query(
    sid: str, sql: str, limit: int, offset: int, fmt: str
) -> dict[str, Any]:
    """Run a paginated SQL query against this session's selected database.
    Pagination wraps the query in a subselect so any SELECT can be paged."""
    await _ensure_session(sid)
    s = _get_session_entry(sid)
    if s is None:
        return {"ok": False, "message": "not connected", "reason": "no-session"}
    if not s.database:
        return {"ok": False, "message": "select a database first", "reason": "no-database"}
    inner = sql.rstrip().rstrip(";")
    paginated = f"SELECT * FROM (\n{inner}\n) LIMIT {int(limit)} OFFSET {int(offset)}"
    r = await ch_query(s.config, paginated, database=s.database, fmt=fmt)
    if not r.ok:
        return {"ok": False, "message": r.value}
    return {"ok": True, "output": r.value}
```

- [ ] **Step 3: Verify import**

Run: `cd /home/user/queryview- && uv run python -c "from queryview.connect import run_query; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add backend/queryview/connect.py
git commit -m "Add run_query: paginated SQL against the selected database"
```

---

## Task 4: Backend — `queries.py` predefined-query store

**Files:** Create `backend/queryview/queries.py`

- [ ] **Step 1: Create the module**

```python
"""Predefined query store: globally-shared, reusable SQL keyed by connection
type. Reuses the SQLite engine owned by connect.py."""

from __future__ import annotations

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from .connect import _engine_for_db, _ensure_schema


class PredefinedQuery(SQLModel, table=True):
    __tablename__ = "predefined_queries"
    __table_args__ = (
        UniqueConstraint("type", "query_name", name="uq_predefined_type_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    query_name: str = Field(index=True)
    type: str = Field(index=True)
    query: str


async def list_predefined_queries(conn_type: str) -> list[dict[str, str]]:
    """Saved queries for a connection type, ordered by name."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        rows = (
            await s.exec(
                select(PredefinedQuery)
                .where(PredefinedQuery.type == conn_type)
                .order_by(PredefinedQuery.query_name)
            )
        ).all()
    return [{"query_name": r.query_name, "query": r.query} for r in rows]


async def save_predefined_query(query_name: str, conn_type: str, query: str) -> None:
    """Upsert a predefined query by (type, query_name)."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (
            await s.exec(
                select(PredefinedQuery).where(
                    PredefinedQuery.type == conn_type,
                    PredefinedQuery.query_name == query_name,
                )
            )
        ).first()
        if row is None:
            row = PredefinedQuery(query_name=query_name, type=conn_type, query=query)
        else:
            row.query = query
        s.add(row)
        await s.commit()
```

- [ ] **Step 2: Verify import + that the model registers in metadata**

Run: `cd /home/user/queryview- && uv run python -c "from queryview.queries import PredefinedQuery, list_predefined_queries, save_predefined_query; from sqlmodel import SQLModel; print('predefined_queries' in SQLModel.metadata.tables)"`
Expected: `True`

- [ ] **Step 3: Commit**

```bash
git add backend/queryview/queries.py
git commit -m "Add predefined query store (predefined_queries table)"
```

---

## Task 5: Backend — query + predefined endpoints in `main.py`

**Files:** Modify `backend/queryview/main.py`

- [ ] **Step 1: Update imports**

Replace line 15:

```python
from .connect import connect_new, get_session, open_saved, select_database
```

with:

```python
from .connect import connect_new, get_session, open_saved, run_query, select_database
from .queries import list_predefined_queries, save_predefined_query
```

- [ ] **Step 2: Add the `_parse_int` helper**

Immediately after the `_read_json` function (ends at the line `        return None`), add:

```python


def _parse_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default
```

- [ ] **Step 3: Return `type` from the open endpoint**

In `clickhouse_open`, replace:

```python
    return {"ok": True, "name": r["name"], "databases": r["databases"]}
```

with:

```python
    return {"ok": True, "name": r["name"], "type": r["type"], "databases": r["databases"]}
```

- [ ] **Step 4: Insert the query + predefined routes before the `/api/{rest:path}` catch-all**

Immediately before the line `@app.api_route("/api/{rest:path}", methods=[...])`, insert:

```python
# Run a SQL query (paginated) against this session's selected database.
@app.post("/api/clickhouse/query")
async def clickhouse_query(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    raw_sql = b.get("query")
    sql = raw_sql.strip() if isinstance(raw_sql, str) else ""
    if not sql:
        return JSONResponse({"ok": False, "message": "query required"}, status_code=400)
    limit = _parse_int(b.get("limit"), 100)
    limit = 100 if limit < 1 else min(limit, 1000)
    offset = _parse_int(b.get("offset"), 0)
    offset = 0 if offset < 0 else offset
    fmt = "CSVWithNames" if b.get("format") == "csv" else "TabSeparatedWithNames"
    r = await run_query(request.state.sid, sql, limit, offset, fmt)
    if not r["ok"]:
        status = 409 if r.get("reason") == "no-session" else 200
        return JSONResponse({"ok": False, "message": r["message"]}, status_code=status)
    return {"ok": True, "output": r["output"]}


# Predefined queries: global, keyed by connection type.
@app.get("/api/predefined-queries")
async def predefined_queries_list(request: Request):
    conn_type = request.query_params.get("type") or "clickhouse"
    return {"queries": await list_predefined_queries(conn_type)}


@app.post("/api/predefined-queries")
async def predefined_queries_save(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    name = b.get("query_name")
    conn_type = b.get("type")
    query = b.get("query")
    name = name.strip() if isinstance(name, str) else ""
    conn_type = conn_type.strip() if isinstance(conn_type, str) else ""
    query = query.strip() if isinstance(query, str) else ""
    if not name or not conn_type or not query:
        return JSONResponse(
            {"ok": False, "message": "query_name, type and query are required"},
            status_code=400,
        )
    await save_predefined_query(name, conn_type, query)
    return {"ok": True}


```

- [ ] **Step 5: Verify endpoints with `TestClient` (no ClickHouse needed)**

Run:

```bash
cd /home/user/queryview- && \
DB_PATH=/tmp/qv_query_check.db \
DB_ENCRYPTION_KEY=$(uv run python -c "import base64,os;print(base64.b64encode(os.urandom(32)).decode())") \
uv run --group test python -c "
from fastapi.testclient import TestClient
from queryview.main import app
c = TestClient(app)
assert c.post('/api/clickhouse/query', json={}).status_code == 400
assert c.post('/api/clickhouse/query', json={'query': 'SELECT 1'}).status_code == 409
assert c.get('/api/predefined-queries?type=clickhouse').json() == {'queries': []}
assert c.post('/api/predefined-queries', json={'query_name': '', 'type': 'clickhouse', 'query': ''}).status_code == 400
assert c.post('/api/predefined-queries', json={'query_name': 'q1', 'type': 'clickhouse', 'query': 'SELECT 1'}).status_code == 200
qs = c.get('/api/predefined-queries?type=clickhouse').json()['queries']
assert any(q['query_name'] == 'q1' and q['query'] == 'SELECT 1' for q in qs), qs
print('backend endpoints OK')
"
rm -f /tmp/qv_query_check.db /tmp/qv_query_check.db.key
```

Expected: prints `backend endpoints OK`

- [ ] **Step 6: Commit**

```bash
git add backend/queryview/main.py
git commit -m "Add query and predefined-queries endpoints"
```

---

## Task 6: Frontend — `query` command + `QueryPanel`

**Files:** Modify `frontend/src/App.tsx`

- [ ] **Step 1: Add `type` to the `Connection` type**

Replace:

```tsx
type Connection = {
  name: string
  databases: string[]
  database: string | null
}
```

with:

```tsx
type Connection = {
  name: string
  type: string
  databases: string[]
  database: string | null
}

type PredefinedQuery = { query_name: string; query: string }
```

- [ ] **Step 2: Add `showQuery` state**

After the line `const [connection, setConnection] = useState<Connection | null>(null)`, add:

```tsx
  const [showQuery, setShowQuery] = useState(false)
```

- [ ] **Step 3: Include `type` when resuming the session**

In the `/api/session` handler, replace:

```tsx
          setConnection({
            name: s.name,
            databases: s.databases ?? [],
            database: s.database ?? null,
          })
```

with:

```tsx
          setConnection({
            name: s.name,
            type: s.type ?? 'clickhouse',
            databases: s.databases ?? [],
            database: s.database ?? null,
          })
```

- [ ] **Step 4: Include `type` + reset `showQuery` in `openSaved`**

Replace:

```tsx
      setShowForm(false)
      setHint(null)
      setConnection({
        name: data.name,
        databases: (data.databases ?? []) as string[],
        database: null,
      })
      setPrompt(`connect ${data.name}`)
```

with:

```tsx
      setShowForm(false)
      setShowQuery(false)
      setHint(null)
      setConnection({
        name: data.name,
        type: (data.type ?? 'clickhouse') as string,
        databases: (data.databases ?? []) as string[],
        database: null,
      })
      setPrompt(`connect ${data.name}`)
```

- [ ] **Step 5: Replace `submitPrompt` to handle `query`**

Replace the whole `submitPrompt` function with:

```tsx
  function submitPrompt(e: React.FormEvent) {
    e.preventDefault()
    const raw = prompt.trim()
    if (!raw) return
    const lower = raw.toLowerCase()
    if (lower === 'new clickhouse') {
      setShowForm(true)
      setShowQuery(false)
      setHint(null)
      return
    }
    if (lower === 'query') {
      if (connection?.database) {
        setShowQuery(true)
        setShowForm(false)
        setHint(null)
      } else {
        setHint('Select a database first.')
      }
      return
    }
    if (lower.startsWith('connect ')) {
      const name = raw.slice('connect '.length).trim().split(/\s+/)[0]
      if (name) {
        void openSaved(name)
        return
      }
    }
    setShowForm(false)
    setShowQuery(false)
    setHint(`Unknown command “${raw}”. Try “new clickhouse” or “connect <name>”.`)
  }
```

- [ ] **Step 6: Update `handleConnected` to accept `type`**

Replace:

```tsx
  function handleConnected(name: string, databases: string[]) {
    setConnection({ name, databases, database: null })
    setShowForm(false)
    setPrompt(`connect ${name}`)
  }
```

with:

```tsx
  function handleConnected(name: string, type: string, databases: string[]) {
    setConnection({ name, type, databases, database: null })
    setShowForm(false)
    setShowQuery(false)
    setPrompt(`connect ${name}`)
  }
```

- [ ] **Step 7: Render `QueryPanel` after the database picker block**

After the `DatabasePicker` render block:

```tsx
        {!showForm && connection && connection.database === null && (
          <DatabasePicker connection={connection} onSelect={selectDatabase} />
        )}
```

add:

```tsx
        {showQuery && connection?.database && (
          <QueryPanel connectionType={connection.type} />
        )}
```

- [ ] **Step 8: Update `ClickHouseForm`'s `onConnected` prop type + call**

Replace:

```tsx
function ClickHouseForm({
  onConnected,
}: {
  onConnected: (name: string, databases: string[]) => void
}) {
```

with:

```tsx
function ClickHouseForm({
  onConnected,
}: {
  onConnected: (name: string, type: string, databases: string[]) => void
}) {
```

Then replace:

```tsx
      if (data.ok) {
        onConnected(data.name as string, (data.databases ?? []) as string[])
```

with:

```tsx
      if (data.ok) {
        onConnected(
          data.name as string,
          (data.type ?? 'clickhouse') as string,
          (data.databases ?? []) as string[],
        )
```

- [ ] **Step 9: Add the `QueryPanel` component before `export default App`**

Insert before `export default App`:

```tsx
function QueryPanel({ connectionType }: { connectionType: string }) {
  const [sql, setSql] = useState('')
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [rows, setRows] = useState(4)
  const [output, setOutput] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [predefined, setPredefined] = useState<PredefinedQuery[]>([])
  const [saveName, setSaveName] = useState('')

  async function loadPredefined() {
    try {
      const res = await fetch(
        `/api/predefined-queries?type=${encodeURIComponent(connectionType)}`,
      )
      const data = await res.json()
      setPredefined((data.queries ?? []) as PredefinedQuery[])
    } catch {
      // a missing list is non-fatal; leave the selector empty
    }
  }

  useEffect(() => {
    void loadPredefined()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectionType])

  async function run(nextOffset: number) {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sql, limit, offset: nextOffset, format: 'text' }),
      })
      const data = await res.json()
      if (data.ok) {
        setOutput(data.output as string)
        setOffset(nextOffset)
      } else {
        setError(data.message ?? 'query failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  async function downloadCsv() {
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/clickhouse/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: sql, limit, offset, format: 'csv' }),
      })
      const data = await res.json()
      if (!data.ok) {
        setError(data.message ?? 'query failed')
        return
      }
      const blob = new Blob([data.output as string], { type: 'text/csv' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'query.csv'
      a.click()
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  async function save() {
    const name = saveName.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    try {
      const res = await fetch('/api/predefined-queries', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query_name: name, type: connectionType, query: sql }),
      })
      const data = await res.json()
      if (data.ok) {
        await loadPredefined()
      } else {
        setError(data.message ?? 'save failed')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'request failed')
    } finally {
      setBusy(false)
    }
  }

  const sizes: [string, number, string][] = [
    ['S', 4, 'query-size-s'],
    ['M', 8, 'query-size-m'],
    ['L', 16, 'query-size-l'],
    ['XL', 28, 'query-size-xl'],
  ]
  const inputClass =
    'rounded-md border border-slate-300 px-3 py-2 outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200'

  return (
    <section
      data-testid="query-panel"
      className="mt-6 space-y-3 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <select
          data-testid="query-predefined-select"
          aria-label="Predefined queries"
          defaultValue=""
          onChange={(e) => {
            const q = predefined.find((p) => p.query_name === e.target.value)
            if (q) setSql(q.query)
          }}
          className={`flex-1 ${inputClass}`}
        >
          <option value="">Predefined queries…</option>
          {predefined.map((p) => (
            <option key={p.query_name} value={p.query_name}>
              {p.query_name}
            </option>
          ))}
        </select>
        <input
          type="text"
          value={saveName}
          onChange={(e) => setSaveName(e.target.value)}
          placeholder="name"
          aria-label="Save query name"
          data-testid="query-save-name"
          className={inputClass}
        />
        <button
          type="button"
          onClick={save}
          disabled={busy}
          data-testid="query-save"
          className="rounded-md border border-indigo-600 px-3 py-2 font-medium text-indigo-700 transition hover:bg-indigo-50 disabled:opacity-50"
        >
          Save
        </button>
      </div>

      <div className="flex justify-end gap-1">
        {sizes.map(([label, n, testid]) => (
          <button
            key={testid}
            type="button"
            onClick={() => setRows(n)}
            data-testid={testid}
            className={`rounded-md border px-2 py-1 text-xs ${
              rows === n
                ? 'border-indigo-600 bg-indigo-600 text-white'
                : 'border-slate-300 hover:bg-slate-50'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      <textarea
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        aria-label="SQL query"
        data-testid="query-input"
        rows={rows}
        placeholder="SELECT …"
        className="w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm outline-none focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200"
      />

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void run(offset)}
          disabled={busy}
          data-testid="query-run"
          className="rounded-md bg-indigo-600 px-4 py-2 font-medium text-white transition hover:bg-indigo-700 disabled:opacity-50"
        >
          Execute
        </button>
        <label className="text-sm text-slate-700">
          Limit
          <input
            type="number"
            value={limit}
            min={1}
            onChange={(e) => setLimit(Number(e.target.value) || 1)}
            aria-label="Limit"
            data-testid="query-limit"
            className={`ml-1 w-20 ${inputClass}`}
          />
        </label>
        <label className="text-sm text-slate-700">
          Offset
          <input
            type="number"
            value={offset}
            min={0}
            onChange={(e) => setOffset(Number(e.target.value) || 0)}
            aria-label="Offset"
            data-testid="query-offset"
            className={`ml-1 w-20 ${inputClass}`}
          />
        </label>
        <button
          type="button"
          onClick={() => void run(Math.max(0, offset - limit))}
          disabled={busy || offset === 0}
          data-testid="query-prev"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm transition hover:bg-slate-50 disabled:opacity-50"
        >
          ← Previous
        </button>
        <button
          type="button"
          onClick={() => void run(offset + limit)}
          disabled={busy}
          data-testid="query-next"
          className="rounded-md border border-slate-300 px-3 py-2 text-sm transition hover:bg-slate-50 disabled:opacity-50"
        >
          Next →
        </button>
        <button
          type="button"
          onClick={downloadCsv}
          disabled={busy}
          data-testid="query-csv"
          className="rounded-md border border-emerald-600 px-3 py-2 text-sm font-medium text-emerald-700 transition hover:bg-emerald-50 disabled:opacity-50"
        >
          Download CSV
        </button>
      </div>

      {output !== null && (
        <pre
          data-testid="query-output"
          className="overflow-auto rounded-md bg-slate-900 p-3 text-sm text-slate-100"
        >
          {output}
        </pre>
      )}
      {error && (
        <p data-testid="query-error" className="text-sm text-red-600">
          {error}
        </p>
      )}
    </section>
  )
}

```

- [ ] **Step 10: Type-check + lint**

Run: `cd /home/user/queryview- && npm run build -w frontend && npm run lint -w frontend`
Expected: build succeeds, eslint clean.

- [ ] **Step 11: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "Add query command and QueryPanel (pagination, predefined, CSV)"
```

---

## Task 7: E2E — seed fixture + query test

**Files:** Modify `e2e/conftest.py`; create `e2e/test_query.py`

- [ ] **Step 1: Add `httpx` import + seed fixture to `conftest.py`**

Replace the import block:

```python
import os

import pytest
from playwright.sync_api import expect
```

with:

```python
import os

import httpx
import pytest
from playwright.sync_api import expect
```

Then append to the end of `e2e/conftest.py`:

```python


# --- ClickHouse seeding for query tests -----------------------------------
# Coordinates default to the connection-form defaults the suite uses.
CH_HOST = os.environ.get("CLICKHOUSE_HOST", "localhost")
CH_PORT = os.environ.get("CLICKHOUSE_PORT", "8123")
CH_USER = os.environ.get("CLICKHOUSE_USER", "default")
CH_PASSWORD = os.environ.get("CLICKHOUSE_PASSWORD", "")


def _ch_exec(sql: str) -> None:
    """Run a statement against ClickHouse over HTTP. POST allows writes (the GET
    interface is read-only by default), so seeding/teardown go through here."""
    res = httpx.post(
        f"http://{CH_HOST}:{CH_PORT}/",
        content=sql.encode("utf-8"),
        auth=(CH_USER, CH_PASSWORD),
        timeout=10.0,
    )
    res.raise_for_status()


@pytest.fixture(scope="module")
def seeded_test_db():
    """Module-level: create a ClickHouse database named `test` with a small
    `items` table of known rows, then drop the whole database on teardown."""
    _ch_exec("CREATE DATABASE IF NOT EXISTS test")
    _ch_exec(
        "CREATE TABLE IF NOT EXISTS test.items (id UInt32, name String) "
        "ENGINE = MergeTree ORDER BY id"
    )
    _ch_exec(
        "INSERT INTO test.items (id, name) VALUES (1, 'alpha'), (2, 'beta'), (3, 'gamma')"
    )
    yield
    _ch_exec("DROP DATABASE IF EXISTS test")
```

- [ ] **Step 2: Create `e2e/test_query.py`**

```python
from playwright.sync_api import Page, expect


def test_query_against_seeded_db(seeded_test_db, page: Page) -> None:
    # Connect using the form defaults (host=localhost, port=8123, user=default).
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    page.get_by_test_id("ch-connect").click()

    # Pick the seeded `test` database.
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="test"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - test")

    # `query` reveals the panel.
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()

    # Write a query.
    sql = "SELECT name FROM items ORDER BY id"
    page.get_by_test_id("query-input").fill(sql)

    # Predefined round-trip: save it, see it in the selector, reload it.
    page.get_by_test_id("query-save-name").fill("all items")
    page.get_by_test_id("query-save").click()
    select = page.get_by_test_id("query-predefined-select")
    expect(select.locator('option[value="all items"]')).to_have_count(1)
    page.get_by_test_id("query-input").fill("")
    select.select_option("all items")
    expect(page.get_by_test_id("query-input")).to_have_value(sql)

    # Pagination: limit 2 -> first page is alpha, beta (not gamma).
    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output).to_contain_text("alpha")
    expect(output).to_contain_text("beta")
    expect(output).not_to_contain_text("gamma")

    # Download CSV of the current page.
    with page.expect_download() as dl_info:
        page.get_by_test_id("query-csv").click()
    csv_text = open(dl_info.value.path(), encoding="utf-8").read()
    assert "name" in csv_text
    assert "alpha" in csv_text

    # Next page: gamma (not alpha).
    page.get_by_test_id("query-next").click()
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
```

- [ ] **Step 3: Verify collection**

Run: `cd /home/user/queryview- && uv run --group test pytest e2e/test_query.py --collect-only -q`
Expected: lists `e2e/test_query.py::test_query_against_seeded_db`, no errors.

- [ ] **Step 4: Commit**

```bash
git add e2e/conftest.py e2e/test_query.py
git commit -m "Add module-scoped seed fixture and query e2e test"
```

---

## Task 8: Remove `EXPECT_CLICKHOUSE_OK` from `test_app.py`

**Files:** Modify `e2e/test_app.py`

- [ ] **Step 1: Replace the whole file**

```python
from playwright.sync_api import Page, expect


def test_queryview_e2e(page: Page) -> None:
    # loads the app and shows the heading
    page.goto("/", wait_until="networkidle")
    expect(page.locator("h1")).to_have_text("QueryView")

    # typing `new clickhouse` reveals the connection form
    page.get_by_test_id("prompt-input").fill("new clickhouse")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("clickhouse-form")).to_be_visible()
    for test_id in ("ch-name", "ch-host", "ch-port", "ch-username", "ch-password"):
        expect(page.get_by_test_id(test_id)).to_be_visible()

    # test connection succeeds against the real ClickHouse
    page.get_by_test_id("ch-test").click()
    result = page.get_by_test_id("ch-result")
    expect(result).to_be_visible()
    expect(result).to_have_attribute("data-ok", "true")
    expect(result).to_contain_text("Connected")

    # connect opens the database picker
    page.get_by_test_id("ch-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    expect(page.locator('[data-db="default"]')).to_be_visible()

    # selecting a database shows the connected indicator
    page.locator('[data-db="default"]').click()
    expect(page.get_by_test_id("connection-indicator")).to_be_visible()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - default")

    # reload resumes the session, then reconnect and select the system database
    page.goto("/", wait_until="networkidle")
    # Resume: came back connected to the previously selected database.
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - default")
    # `connect <name>` reopens the picker; choose a different database.
    page.get_by_test_id("prompt-input").fill("connect clickhouse")
    page.keyboard.press("Enter")
    page.locator('[data-db="system"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - system")

    # opening with ?connection=<name> opens that connection
    page.goto("/?connection=clickhouse", wait_until="networkidle")
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="information_schema"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text(
        "connected - information_schema"
    )
```

- [ ] **Step 2: Verify both files collect**

Run: `cd /home/user/queryview- && uv run --group test pytest e2e --collect-only -q`
Expected: lists `test_app.py::test_queryview_e2e` and `test_query.py::test_query_against_seeded_db`, no errors.

- [ ] **Step 3: Commit**

```bash
git add e2e/test_app.py
git commit -m "Drop EXPECT_CLICKHOUSE_OK gating from the e2e test"
```

---

## Task 9: Remove `EXPECT_CLICKHOUSE_OK` from CI + scripts

**Files:** `.github/workflows/ci.yml`, `scripts/setup_browser.sh`, `scripts/setup.sh`

- [ ] **Step 1: `ci.yml` — drop the env line**

In the `Run Playwright e2e tests` step `env:`, remove the line `EXPECT_CLICKHOUSE_OK: "1"` so only `BASE_URL: http://localhost:8000` remains.

- [ ] **Step 2: `scripts/setup_browser.sh` — remove all three references**

- Delete the doc-comment line: `#   EXPECT_CLICKHOUSE_OK  assert the connection succeeds (default 1)`
- Delete the assignment line: `EXPECT_CLICKHOUSE_OK="${EXPECT_CLICKHOUSE_OK:-1}"`
- In the pytest invocation, delete the line `EXPECT_CLICKHOUSE_OK="$EXPECT_CLICKHOUSE_OK" \` so it reads:

```bash
BASE_URL="http://localhost:$BACKEND_PORT" \
  uv run --frozen --group test pytest e2e \
    --tracing retain-on-failure \
    --html=report/index.html --self-contained-html
```

- [ ] **Step 3: `scripts/setup.sh` — drop it from the comment**

Replace `# (CLICKHOUSE_PORT, BACKEND_PORT, EXPECT_CLICKHOUSE_OK, ...).` with `# (CLICKHOUSE_PORT, BACKEND_PORT, ...).`

- [ ] **Step 4: Verify only the docs reference remains**

Run: `cd /home/user/queryview- && grep -rn "EXPECT_CLICKHOUSE_OK" . --include='*.py' --include='*.md' --include='*.sh' --include='*.yml' | grep -v node_modules`
Expected: only `docs/connect.md` (handled next).

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml scripts/setup_browser.sh scripts/setup.sh
git commit -m "Remove EXPECT_CLICKHOUSE_OK from CI and setup scripts"
```

---

## Task 10: Docs — new endpoints, connection `type`, CI wording

**Files:** `docs/api.md`, `docs/connect.md`

- [ ] **Step 1: `docs/api.md` — add endpoint rows**

After the `/api/clickhouse/database` row, add:

```markdown
| POST   | `/api/clickhouse/query`     | `{query, limit?, offset?, format?}`    | Run SQL against this session's selected database, paginated by `limit`/`offset` (defaults 100/0). `format:"csv"` returns CSV. `{ok, output}` (raw text) \| `{ok:false, message}`. Empty query → `400`; no session → `409`. |
| GET    | `/api/predefined-queries`   | `?type=<connType>`                     | Global predefined queries for a connection type. `{queries:[{query_name, query}]}`. |
| POST   | `/api/predefined-queries`   | `{query_name, type, query}`            | Upsert a global predefined query. `{ok}`; missing fields → `400`. |
```

Also note the session shape now includes `type`: in the `/api/session` row, change `{connected, name?, databases?, database?}` to `{connected, name?, type?, databases?, database?}`.

- [ ] **Step 2: `docs/connect.md` — add the API rows**

After the `/api/clickhouse/database` row in the API table, add:

```markdown
| POST   | `/api/clickhouse/query`       | `{query, limit?, offset?, format?}`    | `{ok, output}` \| `{ok:false, message}`; paginated SQL against the session's selected database (`format:"csv"` for CSV) |
| GET    | `/api/predefined-queries`     | `?type=<connType>`                     | `{queries:[{query_name, query}]}`; global predefined queries by connection type |
| POST   | `/api/predefined-queries`     | `{query_name, type, query}`            | `{ok}`; upserts a global predefined query |
```

- [ ] **Step 3: `docs/connect.md` — reword the CI paragraph**

Replace:

```markdown
CI runs a real `clickhouse/clickhouse-server` service container so the e2e test
exercises an actual connection. With `EXPECT_CLICKHOUSE_OK=1` the test asserts
that connecting succeeds, a database can be selected, and the indicator shows
`connected - <database>`.
```

with:

```markdown
CI runs a real `clickhouse/clickhouse-server` service container so the e2e suite
exercises an actual connection: the tests assert that connecting succeeds, a
database can be selected, the indicator shows `connected - <database>`, and a
query against a seeded `test` database returns its rows.
```

- [ ] **Step 4: Verify no `EXPECT_CLICKHOUSE_OK` remains**

Run: `cd /home/user/queryview- && grep -rn "EXPECT_CLICKHOUSE_OK" . --include='*.py' --include='*.md' --include='*.sh' --include='*.yml' | grep -v node_modules || echo "none remaining"`
Expected: `none remaining`

- [ ] **Step 5: Commit**

```bash
git add docs/api.md docs/connect.md
git commit -m "Document query + predefined-query endpoints and connection type"
```

---

## Task 11: Full e2e verification against a real ClickHouse

Needs network access (downloads a ClickHouse binary + Playwright Chromium). If
the environment blocks the downloads, record that the full e2e ran in CI and
rely on the per-task checks (backend `TestClient`, frontend build/lint,
`pytest --collect-only`).

- [ ] **Step 1: Run the full local e2e flow**

Run: `cd /home/user/queryview- && scripts/setup.sh`
Expected: ClickHouse starts, SPA builds, backend serves it, pytest runs
`test_app.py` and `test_query.py` — both pass. The query test seeds `test`,
pages alpha/beta then gamma, round-trips a predefined query, and downloads CSV.

- [ ] **Step 2: Stop the local ClickHouse**

Run: `cd /home/user/queryview- && scripts/setup_clickhouse.sh stop`
Expected: `[clickhouse] stopped` (or `nothing to stop`).

- [ ] **Step 3 (if anything failed): debug**

Use superpowers:systematic-debugging. Likely culprits: `test` db absent in the
picker (seed didn't run / wrong CH coords), empty output (database scoping or the
pagination wrap), predefined option missing (`type` mismatch between save and
list), or CSV download not captured (blob download timing).

---

## Self-Review Notes

- **Spec coverage:** pagination (Tasks 3,5,6,7), predefined queries table+ops+endpoints+UI+test (Tasks 4,5,6,7), CSV (Tasks 1,5,6,7), connection `type` stored + threaded (Task 2), `query` command + panel (Task 6), seed fixture module-scoped (Task 7), new test file (Task 7), `EXPECT_CLICKHOUSE_OK` removal across test/CI/scripts/docs (Tasks 8,9,10), docs (Task 10). Out-of-scope items (table command, HTML tables, edit/delete predefined, migrations) intentionally omitted.
- **Type/name consistency:** `ch_query(c, query, database, fmt)`; `run_query(sid, sql, limit, offset, fmt)` returns `{ok, output|message, reason?}`; endpoint reads `output`/`message`/`reason`. `PredefinedQuery` ops return `{query_name, query}`; GET returns `{queries:[…]}`; frontend reads `data.queries`, `data.output`, `data.ok`, `data.type`. test-ids match between `App.tsx` and `test_query.py`: `query-panel`, `query-predefined-select`, `query-save-name`, `query-save`, `query-input`, `query-size-s|m|l|xl`, `query-run`, `query-limit`, `query-offset`, `query-prev`, `query-next`, `query-csv`, `query-output`. Fixture `seeded_test_db` matches the test parameter.
- **No placeholders:** every code/command step is concrete.
