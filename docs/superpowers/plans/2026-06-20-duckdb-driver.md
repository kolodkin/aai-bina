# Postgres & DuckDB — Plan 3: DuckDB driver

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add DuckDB as a connection type at full parity — file-based (a path or `:memory:`), no network, **no database picker**: connect goes straight to the query panel.

**Architecture:** A `DuckDBDriver` (the sync `duckdb` library run via `asyncio.to_thread`) satisfying the `Driver` Protocol; `list_databases` returns `[]` so the session/dashboard gates skip "select a database first". The frontend learns to treat a picker-less connection (empty `databases`) as immediately ready to query.

**Tech Stack:** duckdb (in-process); everything else as in Plan 1.

**Depends on:** Plan 1 (Driver foundation) merged. Independent of Plan 2.

## Global Constraints

- Inherit all of Plan 1's Global Constraints.
- DuckDB config is a single `path` (file path, or `:memory:` when blank). No host/port/username/password.
- DuckDB exposes no databases (`list_databases -> (True, [])`); `requires_database = False`. Queries run directly against the file; schema-qualify in SQL.
- Per-request connections opened read-only where possible, run in a worker thread (the lib is synchronous).
- Identifier quoting is `"`; the paginating subselect uses the `_qv` alias.

---

### Task 1: Add the duckdb dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to `[project].dependencies`:

```toml
    "duckdb>=1.0",
```

- [ ] **Step 2: Update the lockfile**

Run: `uv sync --group test`
Expected: resolves and installs duckdb; `uv.lock` updated.

- [ ] **Step 3: Verify import**

Run: `cd backend && uv run python -c "import duckdb; print(duckdb.__version__)"`
Expected: prints a version

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add duckdb dependency"
```

---

### Task 2: DuckDBDriver

**Files:**
- Create: `backend/queryview/drivers/duckdb.py`
- Modify: `backend/queryview/drivers/__init__.py` (register the driver)
- Test: `backend/tests/test_driver_duckdb.py`

**Interfaces:**
- Consumes: `QueryResult`, `build_order_by`, `serialize_rows`, `wrap_paginated` (Plan 1, Task 1).
- Produces:
  - `@dataclass(frozen=True) class DuckConfig: path: str`
  - `class DuckDBDriver` with `type = "duckdb"`, `requires_database = False`, satisfying `Driver`
  - Registry entry `DRIVERS["duckdb"]`

This task uses a real (temp-file) DuckDB — no mocking needed, since duckdb is
in-process and fast.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_driver_duckdb.py
"""DuckDB driver against a real temp-file database: config round-trip, no
picker, paginated query, describe, and registry conformance."""
from __future__ import annotations

import asyncio

import duckdb
import pytest

from queryview.drivers import DRIVERS, Driver
from queryview.drivers.duckdb import DuckConfig, DuckDBDriver


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def duck_path(tmp_path):
    path = tmp_path / "qv.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    con.execute("INSERT INTO items VALUES (1,'alpha'),(2,'beta'),(3,'gamma')")
    con.close()
    return str(path)


def test_registry_has_duckdb_satisfying_protocol():
    d = DRIVERS["duckdb"]
    assert isinstance(d, Driver)
    assert d.type == "duckdb" and d.requires_database is False


def test_parse_config_defaults_blank_path_to_memory():
    d = DuckDBDriver()
    assert d.parse_config({"path": ""})[0] == DuckConfig(":memory:")
    assert d.parse_config({"path": "/tmp/x.duckdb"})[0] == DuckConfig("/tmp/x.duckdb")


def test_config_dict_round_trip():
    d = DuckDBDriver()
    assert d.config_from_dict(d.config_to_dict(DuckConfig("/p"))) == DuckConfig("/p")


def test_list_databases_is_empty(duck_path):
    d = DuckDBDriver()
    assert _run(d.list_databases(DuckConfig(duck_path))) == (True, [])


def test_run_query_paginates_and_serializes(duck_path):
    d = DuckDBDriver()
    r = _run(d.run_query(DuckConfig(duck_path), "SELECT id, name FROM items ORDER BY id",
                         None, 2, 0, [{"name": "name", "dir": "ASC"}], "tsv"))
    assert r.ok
    assert r.value == "id\tname\n1\talpha\n2\tbeta"


def test_describe_query_returns_columns(duck_path):
    d = DuckDBDriver()
    ok, fields = _run(d.describe_query(DuckConfig(duck_path), "SELECT id, name FROM items", None))
    assert ok
    names = [f["name"] for f in fields]
    assert names == ["id", "name"]


def test_run_query_error_is_reported(duck_path):
    d = DuckDBDriver()
    r = _run(d.run_query(DuckConfig(duck_path), "SELECT * FROM no_such", None, 10, 0, None, "tsv"))
    assert r.ok is False and "no_such" in r.value
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --group test pytest tests/test_driver_duckdb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'queryview.drivers.duckdb'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/queryview/drivers/duckdb.py
"""DuckDB driver: file-based, no network, no picker. The synchronous duckdb
library is driven in a worker thread so the event loop is never blocked."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import duckdb

from .base import QueryResult, build_order_by, serialize_rows, wrap_paginated


@dataclass(frozen=True)
class DuckConfig:
    path: str


def parse_duck_config(body: Any) -> tuple[DuckConfig | None, str | None]:
    b = body if isinstance(body, dict) else {}
    raw = b.get("path")
    path = raw.strip() if isinstance(raw, str) else ""
    return DuckConfig(path=path or ":memory:"), None


def _open(path: str):
    # read_only avoids lock contention between concurrent describe/query opens;
    # :memory: cannot be read_only, so open it read-write.
    return duckdb.connect(path, read_only=(path != ":memory:"))


class DuckDBDriver:
    type = "duckdb"
    requires_database = False

    def parse_config(self, body: Any) -> tuple[DuckConfig | None, str | None]:
        return parse_duck_config(body)

    def config_to_dict(self, config: DuckConfig) -> dict[str, Any]:
        return {"path": config.path}

    def config_from_dict(self, data: dict[str, Any]) -> DuckConfig:
        return DuckConfig(path=data["path"])

    async def test(self, config: DuckConfig) -> dict[str, Any]:
        def _work():
            con = _open(config.path)
            try:
                return con.execute("SELECT 1").fetchone()[0]
            finally:
                con.close()
        try:
            val = await asyncio.to_thread(_work)
            return {"ok": True, "message": f"Connected — SELECT 1 returned {val}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e) or "connection failed"}

    async def list_databases(self, config: DuckConfig) -> tuple[bool, list[str] | str]:
        # No picker: queries run directly against the file (schema-qualify in SQL).
        return True, []

    async def run_query(self, config: DuckConfig, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict[str, Any]] | None, fmt: str) -> QueryResult:
        order_clause = build_order_by(order_by, '"')
        paginated = wrap_paginated(sql, order_clause, limit, offset, alias="_qv")

        def _work():
            con = _open(config.path)
            try:
                cur = con.execute(paginated)
                columns = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                return columns, rows
            finally:
                con.close()
        try:
            columns, rows = await asyncio.to_thread(_work)
            return QueryResult(True, serialize_rows(columns, rows, fmt))
        except Exception as e:  # noqa: BLE001
            return QueryResult(False, str(e))

    async def describe_query(self, config: DuckConfig, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]:
        inner = sql.rstrip().rstrip(";")

        def _work():
            con = _open(config.path)
            try:
                # DuckDB's DESCRIBE returns (column_name, column_type, ...).
                return con.execute(f"DESCRIBE {inner}").fetchall()
            finally:
                con.close()
        try:
            rows = await asyncio.to_thread(_work)
            return True, [{"name": r[0], "type": r[1]} for r in rows]
        except Exception as e:  # noqa: BLE001
            return False, str(e)
```

Register it in `backend/queryview/drivers/__init__.py`:

```python
from .clickhouse import ClickHouseDriver
from .duckdb import DuckDBDriver
from .postgres import PostgresDriver  # present only if Plan 2 is merged

DRIVERS: dict[str, Driver] = {
    d.type: d for d in (ClickHouseDriver(), PostgresDriver(), DuckDBDriver())
}
```

> If Plan 2 is **not** merged, omit the `PostgresDriver` import/entry and
> register `(ClickHouseDriver(), DuckDBDriver())`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run --group test pytest tests/test_driver_duckdb.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/queryview/drivers/duckdb.py backend/queryview/drivers/__init__.py backend/tests/test_driver_duckdb.py
git commit -m "feat: DuckDB driver (file-based, no picker)"
```

---

### Task 3: Frontend — picker-less "ready to query" state

**Files:**
- Modify: `frontend/src/QueryView.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/drivers.ts`

A picker-less connection (DuckDB) never selects a database, so `connection.database`
stays `null`. The UI currently gates the query panel, the `query` command, and
the connected indicator on `connection.database` being truthy. Generalize those
to: **ready = connected AND (a database is selected OR the driver has no
databases)**.

**Interfaces:**
- Consumes: `Connection` (`{ name, type, databases, database }`) from Plan 1.

- [ ] **Step 1: Add the duckdb form entry**

Add to the `DRIVERS` map in `frontend/src/drivers.ts`:

```typescript
  duckdb: {
    type: 'duckdb',
    label: 'DuckDB',
    formTestid: 'duckdb-form',
    testTestid: 'duck-test',
    connectTestid: 'duck-connect',
    resultTestid: 'duck-result',
    fields: [
      { key: 'name', label: 'Name', testid: 'duck-name', type: 'text', default: 'duckdb' },
      { key: 'path', label: 'Path', testid: 'duck-path', type: 'text', default: ':memory:' },
    ],
  },
```

- [ ] **Step 2: Generalize "ready" in QueryView.tsx**

(2a) Add a helper near the top of `QueryView` (after `connection` is in scope):

```typescript
  const ready =
    !!connection && (connection.database !== null || connection.databases.length === 0)
```

(2b) In `submitPrompt`, the `query` command branch — replace
`if (connection?.database)` with `if (ready)`:

```typescript
    if (lower === 'query') {
      if (ready) {
        setShowQuery(true)
        setShowForm(false)
        setHint(null)
      } else {
        setHint('Select a database first.')
      }
      return
    }
```

(2c) Replace `const inQueryMode = showQuery && Boolean(connection?.database)` with:

```typescript
  const inQueryMode = showQuery && ready
```

(2d) The database picker should only show when the driver *has* databases.
Replace its render guard:

```typescript
      {!showForm && connection && connection.database === null &&
        connection.databases.length > 0 && (
          <DatabasePicker connection={connection} onSelect={selectDatabase} />
        )}
```

(2e) Replace the query-panel render guard `{showQuery && connection?.database && (`
with:

```typescript
      {showQuery && ready && (
        <QueryPanel
          connectionType={connection.type}
          promptSlot={promptInput}
          pushed={pushed}
          onPushConsumed={onPushConsumed}
        />
      )}
```

(2f) The prompt placeholder uses `connection?.database`; make it follow `ready`:

```typescript
        placeholder={ready ? 'query' : 'Type a command, e.g. new clickhouse'}
```

(2g) The pushed-query effect mounts the panel on `connection?.database`; broaden it:

```typescript
  useEffect(() => {
    if (pushed && ready) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowQuery(true)
    }
  }, [pushed, ready])
```

- [ ] **Step 3: Generalize the indicator in App.tsx**

The shell shows the connected pill only when `connection?.database`. Compute the
same `ready` and show the pill for picker-less connections, labelled by the
database when present, else the connection name.

(3a) After `const [connection, setConnection] = useState...`, add:

```typescript
  const ready =
    !!connection && (connection.database !== null || connection.databases.length === 0)
```

(3b) Replace `{connection?.database && (` (the wrapper around the status pill)
with `{ready && connection && (`, and replace the label text
`connected - {connection.database}` with:

```typescript
            connected - {connection.database ?? connection.name}
```

- [ ] **Step 4: Build the frontend**

Run: `npm run build -w frontend`
Expected: build succeeds, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/QueryView.tsx frontend/src/App.tsx frontend/src/drivers.ts
git commit -m "feat: DuckDB form + picker-less ready-to-query state"
```

---

### Task 4: e2e for DuckDB

**Files:**
- Modify: `e2e/conftest.py` (a `seeded_duckdb` fixture)
- Create: `e2e/test_duckdb.py`

DuckDB needs no service container (in-process). The fixture writes a temp `.duckdb`
file the backend reads directly (same filesystem, which holds in CI too).

- [ ] **Step 1: Add the seeding fixture**

Append to `e2e/conftest.py`:

```python
# --- DuckDB seeding for query tests ---------------------------------------
@pytest.fixture(scope="module")
def seeded_duckdb(tmp_path_factory) -> str:
    """A temp DuckDB file with a small `items` table; returns its path for the
    connection form to point at."""
    import duckdb

    path = tmp_path_factory.mktemp("duck") / "qv.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE items (id INTEGER, name TEXT)")
    con.execute("INSERT INTO items VALUES (1,'alpha'),(2,'beta'),(3,'gamma')")
    con.close()
    return str(path)
```

- [ ] **Step 2: Write the e2e test**

```python
# e2e/test_duckdb.py
from playwright.sync_api import Page, expect


def test_duckdb_connect_no_picker_and_query(seeded_duckdb, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new duckdb")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("duckdb-form")).to_be_visible()

    # Point at the seeded file and connect.
    page.get_by_test_id("duck-path").fill(seeded_duckdb)
    shot("duckdb connection form")
    page.get_by_test_id("duck-connect").click()

    # No picker for DuckDB: it goes straight to ready (indicator shows the name).
    expect(page.get_by_test_id("db-picker")).to_have_count(0)
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - duckdb")
    shot("connected to duckdb (no picker)")

    # Open the query panel directly and query the file.
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("query-panel")).to_be_visible()
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items ORDER BY id")
    page.get_by_test_id("query-limit").fill("2")
    page.get_by_test_id("query-run").click()
    output = page.get_by_test_id("query-output")
    expect(output).to_be_visible()
    expect(output.locator("table thead th")).to_contain_text("name")
    expect(output).to_contain_text("alpha")
    expect(output).to_contain_text("beta")
    expect(output).not_to_contain_text("gamma")
    shot("duckdb results page 1")

    # CSV + next page.
    with page.expect_download() as dl_info:
        page.get_by_test_id("query-csv").click()
    csv_text = open(dl_info.value.path(), encoding="utf-8").read()
    assert "name" in csv_text and "alpha" in csv_text
    page.get_by_test_id("query-next").click()
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
    shot("duckdb results page 2")


def test_duckdb_fields_describe(seeded_duckdb, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new duckdb")
    page.keyboard.press("Enter")
    page.get_by_test_id("duck-path").fill(seeded_duckdb)
    page.get_by_test_id("duck-connect").click()
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items")
    page.get_by_test_id("query-fields").click()
    expect(page.get_by_test_id("field-pickers")).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"]')).to_have_count(2)
    expect(page.locator('[data-testid="field-toggle"][data-col="id"]')).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"][data-col="name"]')).to_be_visible()
    shot("duckdb describe fields")
```

- [ ] **Step 3: Run the e2e locally**

```bash
docker run -d --rm -p 8123:8123 --name qv-ch clickhouse/clickhouse-server:24
npm ci && npm run build -w frontend
SERVE_STATIC=1 uv run --frozen queryview-backend > /tmp/backend.log 2>&1 &
# wait for /api/health, then:
BASE_URL=http://localhost:8000 uv run --group test pytest e2e/test_duckdb.py -v
```

Expected: PASS (both DuckDB e2e tests). No DuckDB service needed — the file is
local to the backend process.

- [ ] **Step 4: Commit**

```bash
git add e2e/conftest.py e2e/test_duckdb.py
git commit -m "test: DuckDB e2e (file-based, no picker)"
```

---

### Task 5: Docs

**Files:**
- Modify: `docs/connect.md` (document the three drivers)

- [ ] **Step 1: Update the connection docs**

In `docs/connect.md`, update the commands table and "Creating a connection" to
list all three drivers:

```
| `new clickhouse` | Open the form to create a new ClickHouse connection. |
| `new postgres`   | Open the form to create a new Postgres connection. |
| `new duckdb`     | Open the form to create a new DuckDB connection (file path; no database picker). |
```

Add a short note: DuckDB connections take a **path** (or `:memory:`) and have no
database picker — connecting goes straight to the query panel; queries
schema-qualify as needed. Postgres lists real databases in the picker (the chosen
one is where queries run).

- [ ] **Step 2: Commit**

```bash
git add docs/connect.md
git commit -m "docs: document Postgres and DuckDB connection drivers"
```

---

## Self-Review

**Spec coverage:** DuckDB connect/test (Task 2 `test`) ✓; file-based config with
`:memory:` default (Task 2 `parse_config`) ✓; **no picker** (`list_databases ->
(True, [])`, Task 2) ✓; picker-less ready-to-query frontend state (Task 3) ✓;
paginated query with `"`-quoting + `_qv` alias and shared serializer (Task 2,
asserted) ✓; describe via DuckDB `DESCRIBE` (Task 2) ✓; sync lib off the event
loop via `asyncio.to_thread` (Task 2) ✓; dashboards skip the db gate
(`requires_database = False` + Plan 2 Task 3 gate; if Plan 2 isn't merged, the
Plan 1 dashboard gate's explicit `clickhouse` check already excludes duckdb) ✓;
e2e without a service container (Task 4) ✓; duckdb dependency (Task 1) ✓; docs
(Task 5) ✓.

**Placeholder scan:** none — all code is concrete.

**Type consistency:** `DuckDBDriver` matches the `Driver` Protocol (method
names/arity/`fmt` values) from Plan 1. `DuckConfig.path` is consistent across
`parse_config`/`config_to_dict`/`config_from_dict`. Frontend `ready` expression
is identical in `QueryView.tsx` and `App.tsx`. `DriverMeta` entry matches Plan 1,
Task 7's type.
</content>
