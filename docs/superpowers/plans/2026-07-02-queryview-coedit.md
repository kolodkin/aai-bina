# QueryView Co-Edit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent and a human co-edit a QueryView session without clobbering each other, via a server-side edit lock, strict push/save validation, persisted query presentation (`order_by` + `fields`), and an "agent update" toast.

**Architecture:** The backend is a hub. All relay + lock state lives in one module (`remote.py`) on a per-session `_Channel` dataclass (in RAM). `remote.push()` becomes the single choke point that validates the payload and checks the human lock before enqueuing to the browser's SSE stream. Durable presentation persists in SQLite (`predefined_queries`), written only by the human's Save. The agent learns lock/commit state on its own turn (push response / GET).

**Tech Stack:** Python (FastAPI, SQLModel, Alembic, pytest), TypeScript/React (Vite, vitest), SQLite, MCP (FastMCP).

## Global Constraints

- **Run tooling with `uv run`** (e.g. `uv run pytest`), never `python -m` or a manual venv. Frontend uses `npx`/`npm` from `frontend/`.
- **No `__all__`** in any module or package `__init__`. Import names where defined.
- **No `Co-Authored-By: Claude`** (or any AI attribution) trailer in commits.
- **Single backend process** assumption: the lock + channels are in-process RAM. Do not add cross-process coordination.
- **`cell_view` stays lenient** (stored verbatim; broken YAML → plain render). Only `order_by`/`fields` are strictly validated.
- **Invalid `order_by`/`fields` ⇒ fail-fast reject** (`{ok:false, message:"invalid …"}`), nothing applied or persisted.
- **Lock TTL = 30s**, browser heartbeat ≈ 10s while the panel is focused.
- Alembic owns the schema (current head `a1b2c3d4e5f6`); `_ensure_schema()` runs `upgrade("head")`. New columns need a migration, not `create_all`.

---

### Task 1: Presentation validator module

**Files:**
- Create: `backend/queryview/validation.py`
- Test: `backend/tests/test_validation.py`

**Interfaces:**
- Produces: `presentation_error(order_by: object, fields: object) -> str | None` — returns an error message string, or `None` when valid. `None` inputs are valid (absent = unchanged).
- Produces: `clamp_paging(limit: object, offset: object) -> tuple[int, int]`.
- Produces: `MAX_LIMIT: int = 10000`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_validation.py
from queryview.validation import presentation_error, clamp_paging, MAX_LIMIT


def test_none_inputs_are_valid():
    assert presentation_error(None, None) is None


def test_valid_order_by_and_fields():
    assert presentation_error([{"name": "id", "dir": "DESC"}], ["id", "name"]) is None
    assert presentation_error([{"name": "id", "dir": "asc"}], None) is None  # case-insensitive


def test_bad_direction_rejected():
    msg = presentation_error([{"name": "id", "dir": "SIDEWAYS"}], None)
    assert msg is not None and "dir" in msg


def test_missing_name_rejected():
    assert presentation_error([{"dir": "ASC"}], None) is not None


def test_backtick_in_name_rejected():
    assert presentation_error([{"name": "a`b", "dir": "ASC"}], None) is not None


def test_non_list_order_by_rejected():
    assert presentation_error("id DESC", None) is not None


def test_non_string_field_rejected():
    assert presentation_error(None, ["ok", 3]) is not None
    assert presentation_error(None, ["ok", ""]) is not None


def test_clamp_paging():
    assert clamp_paging(50, 10) == (50, 10)
    assert clamp_paging(-5, -5) == (0, 0)
    assert clamp_paging(MAX_LIMIT + 1, 0) == (MAX_LIMIT, 0)
    assert clamp_paging("x", None) == (100, 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_validation.py -v`
Expected: FAIL — `ModuleNotFoundError: queryview.validation`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/queryview/validation.py
"""Strict validation for the structured presentation fields (order_by, fields)
carried on a push or a predefined-query save. cell_view is intentionally NOT
validated here (lenient by design). Shared by remote.push and the save endpoint
so every write path enforces the same rules."""

from __future__ import annotations

MAX_LIMIT = 10000


def presentation_error(order_by: object, fields: object) -> str | None:
    """Return an error message if order_by/fields are malformed, else None.
    None inputs are valid (the field is simply absent/unchanged)."""
    if order_by is not None:
        if not isinstance(order_by, list):
            return "invalid order_by: must be a list"
        for col in order_by:
            if not isinstance(col, dict):
                return "invalid order_by: each entry must be an object"
            name = col.get("name")
            if not isinstance(name, str) or not name:
                return "invalid order_by: name must be a non-empty string"
            if "`" in name:
                return "invalid order_by: name must not contain a backtick"
            direction = col.get("dir")
            if not isinstance(direction, str) or direction.upper() not in ("ASC", "DESC"):
                return "invalid order_by: dir must be ASC or DESC"
    if fields is not None:
        if not isinstance(fields, list):
            return "invalid fields: must be a list"
        for f in fields:
            if not isinstance(f, str) or not f:
                return "invalid fields: each column must be a non-empty string"
    return None


def clamp_paging(limit: object, offset: object) -> tuple[int, int]:
    """Coerce/clamp limit & offset to safe ints: 0 <= limit <= MAX_LIMIT, offset >= 0."""
    try:
        lim = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        lim = 100
    try:
        off = int(offset)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        off = 0
    return max(0, min(lim, MAX_LIMIT)), max(0, off)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_validation.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/queryview/validation.py backend/tests/test_validation.py
git commit -m "feat(queryview): add strict presentation validator (order_by/fields)"
```

---

### Task 2: Edit lock + validation gate in remote.py

**Files:**
- Modify: `backend/queryview/remote.py` (dataclass `_Channel` ~13-15, `push` ~36-42)
- Modify: `backend/queryview/main.py` (`remote_push` ~330-349 — pass raw order_by/fields so validation can reject them)
- Test: `backend/tests/test_remote.py` (append)

**Interfaces:**
- Consumes: `presentation_error` from Task 1.
- Produces: `acquire(remote_id: str, owner: str) -> tuple[bool, str]`, `release(remote_id: str, owner: str) -> tuple[bool, str]`, `LOCK_TTL_SECONDS: float = 30.0`. `owner` is `"human"` or `"agent"`.
- Produces (changed behavior): `push(remote_id, payload)` now returns `(False, "invalid …")` for bad presentation and `(False, "blocked, user editing")` when a human holds the lock.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_remote.py
import time as _time
from queryview import remote as _remote


def test_acquire_blocks_push_then_release_allows():
    rid = _remote.register()
    try:
        ok, _ = _remote.acquire(rid, "human")
        assert ok is True
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
        _remote.release(rid, "human")
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True and msg == "delivered"
    finally:
        _remote.unregister(rid)


def test_lock_ttl_expiry_allows_push():
    rid = _remote.register()
    try:
        _remote.acquire(rid, "human")
        # Simulate the heartbeat lapsing: age the lock past its TTL.
        _remote._channels[rid].lock_touched = _time.monotonic() - (_remote.LOCK_TTL_SECONDS + 1)
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True and msg == "delivered"
    finally:
        _remote.unregister(rid)


def test_push_rejects_invalid_order_by():
    rid = _remote.register()
    try:
        ok, msg = _remote.push(
            rid, {"type": "query", "query": "SELECT 1", "order_by": [{"name": "id", "dir": "X"}]}
        )
        assert ok is False and "invalid order_by" in msg
    finally:
        _remote.unregister(rid)


def test_release_by_nonowner_is_noop():
    rid = _remote.register()
    try:
        _remote.acquire(rid, "human")
        _remote.release(rid, "agent")  # wrong owner: must not clear
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
    finally:
        _remote.unregister(rid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_remote.py -k "acquire or ttl or invalid_order or nonowner" -v`
Expected: FAIL — `AttributeError: module 'queryview.remote' has no attribute 'acquire'`

- [ ] **Step 3: Implement lock + validation in remote.py**

Replace the dataclass and `push`, and add the lock functions:

```python
# backend/queryview/remote.py — add near the top imports
import time

from .validation import presentation_error

LOCK_TTL_SECONDS = 30.0


@dataclass
class _Channel:
    queue: "asyncio.Queue[dict[str, Any]]" = field(default_factory=asyncio.Queue)
    # Advisory edit lock: "human" | "agent" | None. lock_touched is a monotonic
    # timestamp refreshed on acquire; a human lock older than LOCK_TTL_SECONDS is
    # treated as released (heartbeat lapsed / tab froze).
    lock_owner: str | None = None
    lock_touched: float = 0.0


def _human_holds(channel: _Channel) -> bool:
    return (
        channel.lock_owner == "human"
        and (time.monotonic() - channel.lock_touched) < LOCK_TTL_SECONDS
    )


def acquire(remote_id: str, owner: str) -> tuple[bool, str]:
    """Take (or refresh) the edit lock for `owner`. Succeeds if free, already
    yours, or the current holder's TTL has lapsed. Otherwise returns the
    owner-named block reason."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
    now = time.monotonic()
    expired = (now - channel.lock_touched) >= LOCK_TTL_SECONDS
    if channel.lock_owner in (None, owner) or expired:
        channel.lock_owner = owner
        channel.lock_touched = now
        return True, "acquired"
    who = "user" if channel.lock_owner == "human" else "agent"
    return False, f"blocked, {who} editing"


def release(remote_id: str, owner: str) -> tuple[bool, str]:
    """Release the lock only if `owner` holds it (idempotent)."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
    if channel.lock_owner == owner:
        channel.lock_owner = None
    return True, "released"
```

Then replace `push`:

```python
def push(remote_id: str, payload: dict[str, Any]) -> tuple[bool, str]:
    """Validate + lock-check + enqueue a payload for a channel. Ordered:
    unknown session -> invalid presentation -> human holds lock -> deliver."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False, "unknown or inactive session"
    err = presentation_error(payload.get("order_by"), payload.get("fields"))
    if err is not None:
        return False, err
    if _human_holds(channel):
        return False, "blocked, user editing"
    channel.queue.put_nowait(payload)
    return True, "delivered"
```

- [ ] **Step 4: Let the HTTP push endpoint pass raw order_by/fields**

In `backend/queryview/main.py`, `remote_push` currently coerces non-lists to `None`, which would hide invalid input from the validator. Change it to pass the raw values so `remote.push` can reject them:

```python
    # backend/queryview/main.py — in remote_push, replace the order_by/fields coercion
    payload = {
        "type": "query",
        "query": query,
        "limit": limit,
        "offset": offset,
        "order_by": b.get("order_by"),
        "fields": b.get("fields"),
        "cell_view": cell_view,
        "name": name,
    }
```

(Remove the now-unused `raw_order`/`order_by`/`raw_fields`/`fields` local coercion lines above the payload; `limit`/`offset` still use the existing `_parse_int` clamping.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_remote.py -v`
Expected: PASS (all — existing + 4 new)

- [ ] **Step 6: Commit**

```bash
git add backend/queryview/remote.py backend/queryview/main.py backend/tests/test_remote.py
git commit -m "feat(queryview): server-side edit lock + validation gate in remote.push"
```

---

### Task 3: Lock acquire/release HTTP endpoint

**Files:**
- Modify: `backend/queryview/main.py` (add endpoint near `remote_push` ~317)
- Test: `backend/tests/test_remote.py` (append)

**Interfaces:**
- Consumes: `remote.acquire`, `remote.release` from Task 2.
- Produces: `POST /api/remote/lock` body `{session_id: str, action: "acquire"|"release"}` → `{ok: bool, message: str}`. Owner is always `"human"`.

- [ ] **Step 1: Write the failing test**

```python
# append to backend/tests/test_remote.py
def test_lock_endpoint_acquire_blocks_push():
    from queryview import remote
    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post("/api/remote/lock", json={"session_id": rid, "action": "acquire"})
        assert r.json()["ok"] is True
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
        r = client.post("/api/remote/lock", json={"session_id": rid, "action": "release"})
        assert r.json()["ok"] is True
        ok, _ = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True
    finally:
        remote.unregister(rid)


def test_lock_endpoint_bad_action_400():
    from queryview import remote
    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post("/api/remote/lock", json={"session_id": rid, "action": "nope"})
        assert r.status_code == 400
    finally:
        remote.unregister(rid)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_remote.py -k "lock_endpoint" -v`
Expected: FAIL — 404 (route missing)

- [ ] **Step 3: Add the endpoint**

```python
# backend/queryview/main.py — after the remote_push handler
@app.post("/api/remote/lock")
async def remote_lock(request: Request):
    """Browser-only edit-lock control for a live session. action=acquire is sent
    on panel focus (and as a ~10s heartbeat); action=release on blur. Owner is
    always 'human' — the agent never calls this."""
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    raw_sid = b.get("session_id")
    session_id = raw_sid.strip() if isinstance(raw_sid, str) else ""
    action = b.get("action")
    if not session_id or action not in ("acquire", "release"):
        return JSONResponse(
            {"ok": False, "message": "session_id and action (acquire|release) required"},
            status_code=400,
        )
    if action == "acquire":
        ok, message = remote.acquire(session_id, "human")
    else:
        ok, message = remote.release(session_id, "human")
    return {"ok": ok, "message": message}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_remote.py -k "lock_endpoint" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/queryview/main.py backend/tests/test_remote.py
git commit -m "feat(queryview): add POST /api/remote/lock (human acquire/release)"
```

---

### Task 4: Persist order_by + fields on predefined queries

**Files:**
- Modify: `backend/queryview/queries.py` (`PredefinedQuery`, `save_predefined_query`, `list_predefined_queries`)
- Create: `backend/queryview/migrations/versions/b2c3d4e5f6a7_predefined_presentation.py`
- Modify: `backend/queryview/main.py` (`predefined_queries_save` ~247, `predefined_queries_list` ~241)
- Test: `backend/tests/test_queries.py` (append), `backend/tests/test_migrations.py` (append)

**Interfaces:**
- Produces: `save_predefined_query(query_name, conn_type, query, cell_view=None, order_by=None, fields=None)` — `order_by`/`fields` are raw JSON **text** or `None` (stored verbatim, like `cell_view`).
- Produces: `list_predefined_queries` rows include `"order_by"` and `"fields"` keys (raw text or `None`).
- Produces: `POST /api/predefined-queries` accepts `order_by`/`fields` as JSON **arrays**, validates them, stores as text; `GET` returns them parsed back to arrays.

- [ ] **Step 1: Write the failing tests**

```python
# append to backend/tests/test_queries.py
import asyncio
from queryview.queries import save_predefined_query, list_predefined_queries


def test_order_by_and_fields_round_trip():
    async def go():
        await save_predefined_query(
            "q1", "clickhouse", "SELECT 1",
            cell_view=None,
            order_by='[{"name":"id","dir":"DESC"}]',
            fields='["id","name"]',
        )
        rows = await list_predefined_queries("clickhouse")
        row = next(r for r in rows if r["query_name"] == "q1")
        assert row["order_by"] == '[{"name":"id","dir":"DESC"}]'
        assert row["fields"] == '["id","name"]'

    asyncio.run(go())


def test_null_presentation_is_preserved():
    async def go():
        await save_predefined_query("q2", "clickhouse", "SELECT 2")
        rows = await list_predefined_queries("clickhouse")
        row = next(r for r in rows if r["query_name"] == "q2")
        assert row["order_by"] is None and row["fields"] is None

    asyncio.run(go())
```

```python
# append to backend/tests/test_migrations.py
def test_predefined_queries_has_presentation_columns():
    _run(_ensure_schema())
    con = sqlite3.connect(os.environ["DB_PATH"])
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(predefined_queries)")}
    finally:
        con.close()
    assert {"order_by", "fields"} <= cols, f"missing columns, got {sorted(cols)}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/test_queries.py -k presentation -v tests/test_migrations.py -k presentation -v`
Expected: FAIL — `TypeError: unexpected keyword 'order_by'` and missing columns.

- [ ] **Step 3: Add the columns to the model**

```python
# backend/queryview/queries.py — inside class PredefinedQuery, after cell_view
    # Raw JSON text (or NULL) for saved presentation, stored verbatim like
    # cell_view: order_by is [{"name","dir"}], fields is ["col", ...].
    order_by: str | None = Field(default=None)
    fields: str | None = Field(default=None)
```

- [ ] **Step 4: Extend save/list**

```python
# backend/queryview/queries.py — replace save_predefined_query signature/body
async def save_predefined_query(
    query_name: str,
    conn_type: str,
    query: str,
    cell_view: str | None = None,
    order_by: str | None = None,
    fields: str | None = None,
) -> None:
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
            row = PredefinedQuery(
                query_name=query_name, type=conn_type, query=query,
                cell_view=cell_view, order_by=order_by, fields=fields,
            )
        else:
            row.query = query
            row.cell_view = cell_view
            row.order_by = order_by
            row.fields = fields
        s.add(row)
        await s.commit()
```

```python
# backend/queryview/queries.py — in list_predefined_queries, extend the returned dict
    return [
        {
            "query_name": r.query_name,
            "query": r.query,
            "cell_view": r.cell_view,
            "order_by": r.order_by,
            "fields": r.fields,
        }
        for r in rows
    ]
```

- [ ] **Step 5: Write the Alembic migration**

```python
# backend/queryview/migrations/versions/b2c3d4e5f6a7_predefined_presentation.py
"""predefined_queries: add order_by + fields presentation columns

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-02

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("predefined_queries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("order_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("fields", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("predefined_queries", schema=None) as batch_op:
        batch_op.drop_column("fields")
        batch_op.drop_column("order_by")
```

- [ ] **Step 6: Wire the endpoints (validate arrays → store text; parse text → arrays)**

```python
# backend/queryview/main.py — top-level imports
import json
from .validation import presentation_error
```

```python
# backend/queryview/main.py — in predefined_queries_save, after computing cell_view
    order_by_arr = b.get("order_by")
    fields_arr = b.get("fields")
    perr = presentation_error(order_by_arr, fields_arr)
    if perr is not None:
        return JSONResponse({"ok": False, "message": perr}, status_code=400)
    order_by = json.dumps(order_by_arr) if isinstance(order_by_arr, list) and order_by_arr else None
    fields = json.dumps(fields_arr) if isinstance(fields_arr, list) and fields_arr else None
    await save_predefined_query(name, conn_type, query, cell_view, order_by, fields)
    return {"ok": True}
```

```python
# backend/queryview/main.py — replace predefined_queries_list body
@app.get("/api/predefined-queries")
async def predefined_queries_list(request: Request):
    conn_type = request.query_params.get("type") or ""
    rows = await list_predefined_queries(conn_type)
    for r in rows:
        r["order_by"] = json.loads(r["order_by"]) if r["order_by"] else None
        r["fields"] = json.loads(r["fields"]) if r["fields"] else None
    return {"queries": rows}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_queries.py tests/test_migrations.py -v`
Expected: PASS (all — existing + 3 new)

- [ ] **Step 8: Commit**

```bash
git add backend/queryview/queries.py backend/queryview/migrations/versions/b2c3d4e5f6a7_predefined_presentation.py backend/queryview/main.py backend/tests/test_queries.py backend/tests/test_migrations.py
git commit -m "feat(queryview): persist order_by + fields on predefined queries"
```

---

### Task 5: Frontend session-lock client + focus wiring

**Files:**
- Create: `frontend/src/sessionLock.ts`
- Modify: `frontend/src/QueryView.tsx` (panel `<section data-testid="query-panel">` ~1044; needs `remoteId`)
- Modify: `frontend/src/App.tsx` (pass `remoteId` into QueryView — it already holds `remoteId` state ~25/88)
- Test: `frontend/src/sessionLock.test.ts`

**Interfaces:**
- Consumes: `POST /api/remote/lock` from Task 3.
- Produces: `postLock(sessionId: string, action: "acquire" | "release"): Promise<void>` — fire-and-forget (swallows errors; lock is advisory).
- Consumes: `QueryView`/`QueryPanel` gain a `remoteId?: string | null` prop threaded from `App`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/sessionLock.test.ts
import { describe, it, expect, vi, afterEach } from 'vitest'
import { postLock } from './sessionLock'

afterEach(() => vi.restoreAllMocks())

describe('postLock', () => {
  it('posts acquire with the session id', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ok: true }) })
    vi.stubGlobal('fetch', fetchMock)
    await postLock('abc', 'acquire')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/remote/lock',
      expect.objectContaining({ method: 'POST' }),
    )
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body).toEqual({ session_id: 'abc', action: 'acquire' })
  })

  it('never throws on network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('down')))
    await expect(postLock('abc', 'release')).resolves.toBeUndefined()
  })

  it('no-ops without a session id', async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await postLock('', 'acquire')
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/sessionLock.test.ts`
Expected: FAIL — cannot resolve `./sessionLock`

- [ ] **Step 3: Implement the client**

```typescript
// frontend/src/sessionLock.ts
// Fire-and-forget edit-lock control for the live QueryView session. The lock is
// advisory (the backend TTL-expires it), so failures are swallowed.
export async function postLock(
  sessionId: string,
  action: 'acquire' | 'release',
): Promise<void> {
  if (!sessionId) return
  try {
    await fetch('/api/remote/lock', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, action }),
    })
  } catch {
    /* advisory lock: ignore transport errors */
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/sessionLock.test.ts`
Expected: PASS (3 tests)

- [ ] **Step 5: Thread remoteId and wire panel focus/blur/heartbeat**

In `frontend/src/App.tsx`, pass the existing `remoteId` to `QueryView` (near `onPushConsumed={() => setQueryPush(null)}`):

```tsx
              remoteId={remoteId}
```

In `frontend/src/QueryView.tsx`: add `remoteId` to the `QueryView` prop type and forward it to `QueryPanel`, add `remoteId` to `QueryPanel`'s prop type, then add the focus tracker inside `QueryPanel` (uses the existing `useRef`/`useEffect` imports):

```tsx
// import at top
import { postLock } from './sessionLock'

// inside QueryPanel, after existing state
  const panelRef = useRef<HTMLElement>(null)
  const blurTimer = useRef<number | undefined>(undefined)

  useEffect(() => {
    const el = panelRef.current
    if (!el || !remoteId) return
    const acquire = () => void postLock(remoteId, 'acquire')
    const onFocusIn = () => {
      window.clearTimeout(blurTimer.current)
      acquire()
    }
    const onFocusOut = () => {
      // Debounce: moving between inputs fires focusout then focusin.
      window.clearTimeout(blurTimer.current)
      blurTimer.current = window.setTimeout(() => {
        if (!el.contains(document.activeElement)) void postLock(remoteId, 'release')
      }, 150)
    }
    el.addEventListener('focusin', onFocusIn)
    el.addEventListener('focusout', onFocusOut)
    // Heartbeat while the panel holds focus, to refresh the 30s TTL.
    const beat = window.setInterval(() => {
      if (el.contains(document.activeElement)) acquire()
    }, 10000)
    return () => {
      el.removeEventListener('focusin', onFocusIn)
      el.removeEventListener('focusout', onFocusOut)
      window.clearInterval(beat)
      window.clearTimeout(blurTimer.current)
    }
  }, [remoteId])
```

Attach the ref to the panel root: change `<section data-testid="query-panel" ...>` to include `ref={panelRef}`.

- [ ] **Step 6: Verify build + full frontend tests**

Run: `cd frontend && npx tsc -b && npx vitest run`
Expected: tsc exit 0; all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/sessionLock.ts frontend/src/sessionLock.test.ts frontend/src/QueryView.tsx frontend/src/App.tsx
git commit -m "feat(queryview): acquire/release edit lock on panel focus/blur"
```

---

### Task 6: Frontend — save & restore order_by + fields

**Files:**
- Modify: `frontend/src/QueryView.tsx` (`PredefinedQuery` type ~35, `save` ~979, `onSelectName` ~965)
- Test: `frontend/src/QueryView.tsx` behavior covered via a small pure helper `frontend/src/presentation.ts` + `frontend/src/presentation.test.ts`

**Interfaces:**
- Consumes: `GET /api/predefined-queries` now returns `order_by: OrderCol[] | null`, `fields: string[] | null` (Task 4).
- Produces: `PredefinedQuery` type gains `order_by: OrderCol[] | null` and `fields: string[] | null`.
- Produces: `presentationForSave(orderBy: OrderCol[], visibleCols: string[]): { order_by: OrderCol[] | null; fields: string[] | null }` — empty arrays become `null`.

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/presentation.test.ts
import { describe, it, expect } from 'vitest'
import { presentationForSave } from './presentation'

describe('presentationForSave', () => {
  it('passes through non-empty picks', () => {
    expect(presentationForSave([{ name: 'id', dir: 'DESC' }], ['id', 'name'])).toEqual({
      order_by: [{ name: 'id', dir: 'DESC' }],
      fields: ['id', 'name'],
    })
  })
  it('maps empty arrays to null', () => {
    expect(presentationForSave([], [])).toEqual({ order_by: null, fields: null })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/presentation.test.ts`
Expected: FAIL — cannot resolve `./presentation`

- [ ] **Step 3: Add the helper**

```typescript
// frontend/src/presentation.ts
import type { OrderCol } from './QueryView'

// Saved presentation payload: empty selections persist as null ("no selection"),
// matching the backend's nullable columns.
export function presentationForSave(
  orderBy: OrderCol[],
  visibleCols: string[],
): { order_by: OrderCol[] | null; fields: string[] | null } {
  return {
    order_by: orderBy.length ? orderBy : null,
    fields: visibleCols.length ? visibleCols : null,
  }
}
```

Export `OrderCol` from `QueryView.tsx` if not already exported: change `type OrderCol = ...` to `export type OrderCol = ...`.

- [ ] **Step 4: Wire save + type + restore**

`PredefinedQuery` type (~35):

```tsx
type PredefinedQuery = {
  query_name: string
  query: string
  cell_view: string | null
  order_by: OrderCol[] | null
  fields: string[] | null
}
```

`save` body (~988) — add presentation, import the helper:

```tsx
// import at top
import { presentationForSave } from './presentation'

// in save(), replace the JSON body object:
        body: JSON.stringify({
          query_name: name,
          type: connectionType,
          query: sql,
          cell_view: cellViewValue,
          ...presentationForSave(orderBy, visibleCols),
        }),
```

`onSelectName` (~965) — restore presentation when loading a saved query:

```tsx
  function onSelectName(value: string) {
    if (value === NEW_NAME_OPTION) {
      const name = window.prompt('Save query as (name):', selectedName || '')?.trim()
      if (name) setSelectedName(name)
      return
    }
    setSelectedName(value)
    setPushedCellView(null) // selecting a query reverts to its saved cell view
    const q = predefined.find((p) => p.query_name === value)
    if (q) {
      setSql(q.query)
      setOrderBy(q.order_by ?? [])
      setVisibleCols(q.fields ?? []) // [] = show all (matches pushed-fields semantics)
    }
  }
```

- [ ] **Step 5: Run tests + build**

Run: `cd frontend && npx vitest run src/presentation.test.ts && npx tsc -b`
Expected: PASS; tsc exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/presentation.ts frontend/src/presentation.test.ts frontend/src/QueryView.tsx
git commit -m "feat(queryview): save & restore order_by + fields with predefined queries"
```

---

### Task 7: Frontend — "agent update" toast

**Files:**
- Create: `frontend/src/Toast.tsx`
- Modify: `frontend/src/App.tsx` (the `query` SSE listener ~93-100; render the toast)
- Test: `frontend/src/Toast.test.tsx`

**Interfaces:**
- Produces: `Toast({ message, onDone }: { message: string | null; onDone: () => void })` — renders nothing when `message` is null; auto-calls `onDone` after ~3s.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/Toast.test.tsx
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, act } from '@testing-library/react'
import { Toast } from './Toast'

afterEach(() => vi.useRealTimers())

describe('Toast', () => {
  it('renders nothing when message is null', () => {
    const { container } = render(<Toast message={null} onDone={() => {}} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the message and auto-dismisses', () => {
    vi.useFakeTimers()
    const onDone = vi.fn()
    render(<Toast message="Agent updated the query" onDone={onDone} />)
    expect(screen.getByText('Agent updated the query')).toBeTruthy()
    act(() => vi.advanceTimersByTime(3000))
    expect(onDone).toHaveBeenCalled()
  })
})
```

> If `@testing-library/react`/`jsdom` aren't already dev-deps, install them: `cd frontend && npm i -D @testing-library/react @testing-library/jest-dom jsdom` and ensure `vitest` uses the `jsdom` environment (add `/** @vitest-environment jsdom */` at the top of this test file). Check `frontend/package.json` first — only add what's missing.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/Toast.test.tsx`
Expected: FAIL — cannot resolve `./Toast`

- [ ] **Step 3: Implement the toast**

```tsx
// frontend/src/Toast.tsx
import { useEffect } from 'react'

// Transient bottom notice. Renders nothing when message is null; auto-dismisses.
export function Toast({ message, onDone }: { message: string | null; onDone: () => void }) {
  useEffect(() => {
    if (!message) return
    const t = window.setTimeout(onDone, 3000)
    return () => window.clearTimeout(t)
  }, [message, onDone])
  if (!message) return null
  return (
    <div
      data-testid="agent-toast"
      className="fixed bottom-4 left-1/2 -translate-x-1/2 rounded-md bg-slate-800/90 px-4 py-2 text-sm text-slate-100 shadow-lg ring-1 ring-white/10"
    >
      {message}
    </div>
  )
}
```

- [ ] **Step 4: Fire it on agent push in App.tsx**

```tsx
// frontend/src/App.tsx — add state near queryPush
  const [toast, setToast] = useState<string | null>(null)

// in the 'query' SSE listener, after setQueryPush(...)
        setToast('Agent updated the query')

// in the render tree (top level), add:
      <Toast message={toast} onDone={() => setToast(null)} />
```

Add `import { Toast } from './Toast'` at the top of `App.tsx`.

- [ ] **Step 5: Run tests + build**

Run: `cd frontend && npx vitest run src/Toast.test.tsx && npx tsc -b`
Expected: PASS; tsc exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/Toast.tsx frontend/src/Toast.test.tsx frontend/src/App.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat(queryview): agent-update toast on incoming push"
```

---

### Task 8: Docs + full-suite green

**Files:**
- Modify: `docs/query.md` (document the lock, presentation persistence, and toast)

- [ ] **Step 1: Document the behavior**

Add a short "Co-edit & edit lock" subsection to `docs/query.md` covering: the human focus/blur lock, the `"blocked, user editing"` push rejection, that Save now persists `order_by`/`fields`, and the agent-update toast. Keep it consistent with the existing prose style.

- [ ] **Step 2: Run the entire backend + frontend suites**

Run: `cd backend && uv run pytest -q`
Expected: PASS (all).

Run: `cd frontend && npx tsc -b && npx vitest run`
Expected: tsc exit 0; all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add docs/query.md
git commit -m "docs: document QueryView co-edit lock, presentation persistence, toast"
```

---

## Self-Review

**Spec coverage:**
- Edit lock (acquire/release, TTL, owner-named block) → Tasks 2, 3, 5. ✅
- Atomic agent push (no held lock) → Task 2 (`push` never sets `lock_owner`). ✅
- Validation fail-fast (order_by/fields, cell_view lenient) → Tasks 1, 2, 4. ✅
- Persist order_by + fields (columns + migration + endpoints) → Task 4. ✅
- Restore presentation on load → Task 6. ✅
- Agent-update toast → Task 7. ✅
- Communication topology / backend-mediated (no new agent transport) → inherent (uses existing relay + new HTTP lock endpoint). ✅
- Single-process constraint → honored (RAM lock in `remote.py`); no cross-process code. ✅
- order_by SQL safety → pre-existing `build_order_by` (backtick-quote + ASC/DESC whitelist); validation is defense-in-depth. ✅

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The one prose step (Task 8 doc text) is a documentation step, not code.

**Type consistency:** `postLock(sessionId, action)`, `presentationForSave(orderBy, visibleCols)`, `presentation_error(order_by, fields)`, `acquire/release(remote_id, owner)`, `PredefinedQuery.{order_by,fields}` used consistently across tasks. `OrderCol` exported from `QueryView.tsx` and consumed by `presentation.ts`.

**Known follow-ups (out of scope, noted in spec):** multi-process lock backplane; blur-without-Save may let a later agent push overwrite unsaved edits (accepted).
