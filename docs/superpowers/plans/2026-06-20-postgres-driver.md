# Postgres & DuckDB — Plan 2: Postgres driver

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Postgres as a connection type at full parity (connect, test, real-database picker, paginated query, CSV, describe, dashboards, predefined queries).

**Architecture:** A `PostgresDriver` (asyncpg) satisfying the `Driver` Protocol from Plan 1, registered in `DRIVERS`, plus a frontend driver-registry entry. No flow changes — the picker lists real Postgres databases (`pg_database`); the chosen db is the one queries connect to. Short-lived connections per request, 5s timeouts, matching the ClickHouse model.

**Tech Stack:** asyncpg; everything else as in Plan 1.

**Depends on:** Plan 1 (Driver foundation) merged.

## Global Constraints

- Inherit all of Plan 1's Global Constraints.
- Postgres requires connecting to *some* database to enumerate the rest; the bootstrap order is the connection user's own db, then `postgres`, then `template1`.
- Identifier quoting is `"`; the paginating subselect uses the `_qv` alias (Postgres requires a derived-table alias).
- Per-request connections with `timeout`/`command_timeout` = 5s.

---

### Task 1: Add the asyncpg dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to `[project].dependencies`:

```toml
    "asyncpg>=0.29",
```

- [ ] **Step 2: Update the lockfile**

Run: `uv sync --group test`
Expected: resolves and installs asyncpg; `uv.lock` updated.

- [ ] **Step 3: Verify import**

Run: `cd backend && uv run python -c "import asyncpg; print(asyncpg.__version__)"`
Expected: prints a version (no ImportError)

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add asyncpg dependency"
```

---

### Task 2: PostgresDriver

**Files:**
- Create: `backend/queryview/drivers/postgres.py`
- Modify: `backend/queryview/drivers/__init__.py` (register the driver)
- Test: `backend/tests/test_driver_postgres.py`

**Interfaces:**
- Consumes: `QueryResult`, `build_order_by`, `serialize_rows`, `wrap_paginated` (Plan 1, Task 1).
- Produces:
  - `@dataclass(frozen=True) class PgConfig: host:str; port:int; username:str; password:str`
  - `class PostgresDriver` with `type = "postgres"`, `requires_database = True`, satisfying `Driver`
  - Registry entry `DRIVERS["postgres"]`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_driver_postgres.py
"""Postgres driver: validation, config round-trip, registry conformance, and
the paginated-SQL shape (asyncpg calls are monkeypatched — no server needed).
Live connect/query/describe are covered by e2e."""
from __future__ import annotations

import asyncio

from queryview.drivers import DRIVERS, Driver
from queryview.drivers.postgres import PgConfig, PostgresDriver


def test_registry_has_postgres_satisfying_protocol():
    d = DRIVERS["postgres"]
    assert isinstance(d, Driver)
    assert d.type == "postgres" and d.requires_database is True


def test_parse_config_validates_host_and_port():
    d = PostgresDriver()
    cfg, err = d.parse_config({"host": "h", "port": "5432", "username": "u", "password": "p"})
    assert err is None and cfg == PgConfig("h", 5432, "u", "p")
    assert d.parse_config({"port": 5432})[0] is None
    assert d.parse_config({"host": "h", "port": 99999})[0] is None


def test_config_dict_round_trip():
    d = PostgresDriver()
    cfg = PgConfig("h", 5432, "u", "p")
    assert d.config_from_dict(d.config_to_dict(cfg)) == cfg


def test_run_query_builds_aliased_double_quoted_sql(monkeypatch):
    d = PostgresDriver()
    captured = {}

    class _Stmt:
        def get_attributes(self):
            class _A:
                name = "name"
                class type:  # noqa: N801
                    name = "text"
            return (_A(),)
        async def fetch(self):
            return [["alpha"]]

    class _Conn:
        async def prepare(self, sql):
            captured["sql"] = sql
            return _Stmt()
        async def close(self):
            pass

    async def fake_connect(c, database):
        captured["database"] = database
        return _Conn()

    monkeypatch.setattr("queryview.drivers.postgres._connect", fake_connect)
    r = asyncio.run(
        d.run_query(PgConfig("h", 5432, "u", ""), "SELECT name FROM t;", "mydb",
                    50, 10, [{"name": "name", "dir": "ASC"}], "tsv")
    )
    assert r.ok and r.value == "name\nalpha"
    assert captured["database"] == "mydb"
    assert captured["sql"] == (
        'SELECT * FROM (\nSELECT name FROM t\n) AS _qv ORDER BY "name" ASC LIMIT 50 OFFSET 10'
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --group test pytest tests/test_driver_postgres.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'queryview.drivers.postgres'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/queryview/drivers/postgres.py
"""Postgres driver (asyncpg). Short-lived connections per request; the picker
lists real databases and the selected one is where queries run."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import asyncpg

from .base import QueryResult, build_order_by, serialize_rows, wrap_paginated

PG_TIMEOUT_SECONDS = 5.0
_BOOTSTRAP_DBS = ("postgres", "template1")


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    username: str
    password: str


def parse_pg_config(body: Any) -> tuple[PgConfig | None, str | None]:
    b = body if isinstance(body, dict) else {}
    raw_host = b.get("host")
    host = raw_host.strip() if isinstance(raw_host, str) else ""
    raw_port = b.get("port")
    if isinstance(raw_port, bool):
        port = None
    elif isinstance(raw_port, int):
        port = raw_port
    elif isinstance(raw_port, str):
        try:
            port = int(raw_port)
        except ValueError:
            port = None
    else:
        port = None
    username = b.get("username") if isinstance(b.get("username"), str) else ""
    password = b.get("password") if isinstance(b.get("password"), str) else ""
    if not host:
        return None, "host required"
    if port is None or port <= 0 or port > 65535:
        return None, "valid port required"
    return PgConfig(host=host, port=port, username=username, password=password), None


async def _connect(c: PgConfig, database: str | None):
    return await asyncpg.connect(
        host=c.host, port=c.port,
        user=c.username or None, password=c.password or None,
        database=database, timeout=PG_TIMEOUT_SECONDS, command_timeout=PG_TIMEOUT_SECONDS,
    )


async def _connect_bootstrap(c: PgConfig):
    """Connect to *some* database so we can enumerate the rest."""
    candidates = ([c.username] if c.username else []) + list(_BOOTSTRAP_DBS)
    last: Exception | None = None
    for db in candidates:
        try:
            return await _connect(c, db)
        except Exception as e:  # noqa: BLE001
            last = e
    raise last if last is not None else RuntimeError("could not connect")


class PostgresDriver:
    type = "postgres"
    requires_database = True

    def parse_config(self, body: Any) -> tuple[PgConfig | None, str | None]:
        return parse_pg_config(body)

    def config_to_dict(self, config: PgConfig) -> dict[str, Any]:
        return {
            "host": config.host, "port": config.port,
            "username": config.username, "password": config.password,
        }

    def config_from_dict(self, data: dict[str, Any]) -> PgConfig:
        return PgConfig(
            host=data["host"], port=int(data["port"]),
            username=data.get("username", ""), password=data.get("password", ""),
        )

    async def test(self, config: PgConfig) -> dict[str, Any]:
        try:
            conn = await _connect_bootstrap(config)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e) or "connection failed"}
        try:
            val = await conn.fetchval("SELECT 1")
            return {"ok": True, "message": f"Connected — SELECT 1 returned {val}"}
        finally:
            await conn.close()

    async def list_databases(self, config: PgConfig) -> tuple[bool, list[str] | str]:
        try:
            conn = await _connect_bootstrap(config)
        except Exception as e:  # noqa: BLE001
            return False, str(e) or "connection failed"
        try:
            rows = await conn.fetch(
                "SELECT datname FROM pg_database "
                "WHERE datallowconn AND NOT datistemplate ORDER BY datname"
            )
            return True, [r["datname"] for r in rows]
        finally:
            await conn.close()

    async def run_query(self, config: PgConfig, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict[str, Any]] | None, fmt: str) -> QueryResult:
        order_clause = build_order_by(order_by, '"')
        paginated = wrap_paginated(sql, order_clause, limit, offset, alias="_qv")
        try:
            conn = await _connect(config, database)
        except Exception as e:  # noqa: BLE001
            return QueryResult(False, str(e) or "connection failed")
        try:
            stmt = await conn.prepare(paginated)
            columns = [a.name for a in stmt.get_attributes()]
            records = await stmt.fetch()
            return QueryResult(True, serialize_rows(columns, [list(r) for r in records], fmt))
        except Exception as e:  # noqa: BLE001
            return QueryResult(False, str(e))
        finally:
            await conn.close()

    async def describe_query(self, config: PgConfig, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]:
        inner = sql.rstrip().rstrip(";")
        try:
            conn = await _connect(config, database)
        except Exception as e:  # noqa: BLE001
            return False, str(e) or "connection failed"
        try:
            stmt = await conn.prepare(inner)
            return True, [{"name": a.name, "type": a.type.name} for a in stmt.get_attributes()]
        except Exception as e:  # noqa: BLE001
            return False, str(e)
        finally:
            await conn.close()
```

Register it in `backend/queryview/drivers/__init__.py`:

```python
from .clickhouse import ClickHouseDriver
from .postgres import PostgresDriver

DRIVERS: dict[str, Driver] = {d.type: d for d in (ClickHouseDriver(), PostgresDriver())}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run --group test pytest tests/test_driver_postgres.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/queryview/drivers/postgres.py backend/queryview/drivers/__init__.py backend/tests/test_driver_postgres.py
git commit -m "feat: Postgres driver (asyncpg)"
```

---

### Task 3: Generalize the dashboard database gate

**Files:**
- Modify: `backend/queryview/dashboard_queries.py`

**Interfaces:**
- Consumes: `DRIVERS`, `driver.requires_database` (default True when absent).

- [ ] **Step 1: Replace the ClickHouse-only gate with a driver-driven one**

In `run_queries_for_connection`, replace the explicit `clickhouse` check from
Plan 1 with:

```python
    driver = DRIVERS[stored.type]
    if getattr(driver, "requires_database", True) and not stored.database:
        return {"ok": False, "reason": "no-database", "message": (
            f'connection "{name}" has no selected database — select one for it '
            "or fully-qualify table names as db.table")}
```

(The per-query loop already uses `driver.run_query(...)` from Plan 1, Task 5.)

- [ ] **Step 2: Run the dashboard unit tests**

Run: `cd backend && uv run --group test pytest tests/test_dashboards.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add backend/queryview/dashboard_queries.py
git commit -m "feat: gate dashboard queries on the driver's requires_database"
```

---

### Task 4: Frontend — register the Postgres form

**Files:**
- Modify: `frontend/src/drivers.ts`

- [ ] **Step 1: Add the postgres entry**

Add to the `DRIVERS` map in `frontend/src/drivers.ts`:

```typescript
  postgres: {
    type: 'postgres',
    label: 'Postgres',
    formTestid: 'postgres-form',
    testTestid: 'pg-test',
    connectTestid: 'pg-connect',
    resultTestid: 'pg-result',
    fields: [
      { key: 'name', label: 'Name', testid: 'pg-name', type: 'text', default: 'postgres' },
      { key: 'host', label: 'Host', testid: 'pg-host', type: 'text', default: 'localhost' },
      { key: 'port', label: 'Port', testid: 'pg-port', type: 'text', default: '5432' },
      { key: 'username', label: 'Username', testid: 'pg-username', type: 'text', default: 'postgres' },
      { key: 'password', label: 'Password', testid: 'pg-password', type: 'password', default: '' },
    ],
  },
```

> No other frontend change: `new postgres` and the form are already driven by
> this registry (Plan 1, Task 7). The picker lists Postgres databases like
> ClickHouse, and the query panel is identical.

- [ ] **Step 2: Build the frontend**

Run: `npm run build -w frontend`
Expected: build succeeds, no type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/drivers.ts
git commit -m "feat: Postgres connection form"
```

---

### Task 5: CI service + e2e

**Files:**
- Modify: `.github/workflows/ci.yml` (add a `postgres` service)
- Modify: `e2e/conftest.py` (a `seeded_pg_db` fixture)
- Create: `e2e/test_postgres.py`

**Interfaces:**
- Consumes: the running backend + a Postgres service on `localhost:5432`.

- [ ] **Step 1: Add the Postgres service to CI**

In `.github/workflows/ci.yml`, under `jobs.test-e2e.services`, add alongside
`clickhouse`:

```yaml
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: postgres
          POSTGRES_HOST_AUTH_METHOD: trust
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
```

> `POSTGRES_HOST_AUTH_METHOD: trust` lets the form defaults (user `postgres`,
> empty password) connect, mirroring the ClickHouse e2e's default-driven flow.

- [ ] **Step 2: Add the seeding fixture**

Append to `e2e/conftest.py`:

```python
# --- Postgres seeding for query tests -------------------------------------
PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "postgres")
PG_PASSWORD = os.environ.get("PG_PASSWORD", "")


@pytest.fixture(scope="module")
def seeded_pg_db():
    """Create a `qvtest` database with a small `items` table; drop it after.
    Uses asyncpg (a project dependency)."""
    import asyncio

    import asyncpg

    async def _seed():
        sys = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD or None, database="postgres",
        )
        await sys.execute("DROP DATABASE IF EXISTS qvtest WITH (FORCE)")
        await sys.execute("CREATE DATABASE qvtest")
        await sys.close()
        db = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD or None, database="qvtest",
        )
        await db.execute("CREATE TABLE items (id int, name text)")
        await db.execute("INSERT INTO items (id, name) VALUES (1,'alpha'),(2,'beta'),(3,'gamma')")
        await db.close()

    async def _teardown():
        sys = await asyncpg.connect(
            host=PG_HOST, port=PG_PORT, user=PG_USER,
            password=PG_PASSWORD or None, database="postgres",
        )
        await sys.execute("DROP DATABASE IF EXISTS qvtest WITH (FORCE)")
        await sys.close()

    asyncio.run(_seed())
    yield
    asyncio.run(_teardown())
```

- [ ] **Step 3: Write the e2e test**

```python
# e2e/test_postgres.py
from playwright.sync_api import Page, expect


def test_postgres_connect_pick_db_and_query(seeded_pg_db, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new postgres")
    page.keyboard.press("Enter")
    expect(page.get_by_test_id("postgres-form")).to_be_visible()
    shot("postgres connection form")
    page.get_by_test_id("pg-connect").click()

    # Picker lists real databases; pick the seeded one.
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="qvtest"]').click()
    expect(page.get_by_test_id("connection-status")).to_contain_text("connected - qvtest")
    shot("connected to qvtest")

    # Query the seeded table with pagination + order.
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
    shot("postgres results page 1")

    # CSV download of the current page.
    with page.expect_download() as dl_info:
        page.get_by_test_id("query-csv").click()
    csv_text = open(dl_info.value.path(), encoding="utf-8").read()
    assert "name" in csv_text and "alpha" in csv_text

    # Next page.
    page.get_by_test_id("query-next").click()
    expect(output).to_contain_text("gamma")
    expect(output).not_to_contain_text("alpha")
    shot("postgres results page 2")


def test_postgres_fields_describe(seeded_pg_db, page: Page, shot) -> None:
    page.goto("/", wait_until="networkidle")
    page.get_by_test_id("prompt-input").fill("new postgres")
    page.keyboard.press("Enter")
    page.get_by_test_id("pg-connect").click()
    expect(page.get_by_test_id("db-picker")).to_be_visible()
    page.locator('[data-db="qvtest"]').click()
    page.get_by_test_id("prompt-input").fill("query")
    page.keyboard.press("Enter")
    page.get_by_test_id("query-input").fill("SELECT id, name FROM items")
    page.get_by_test_id("query-fields").click()
    expect(page.get_by_test_id("field-pickers")).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"]')).to_have_count(2)
    expect(page.locator('[data-testid="field-toggle"][data-col="id"]')).to_be_visible()
    expect(page.locator('[data-testid="field-toggle"][data-col="name"]')).to_be_visible()
    shot("postgres describe fields")
```

- [ ] **Step 4: Run the e2e locally**

```bash
docker run -d --rm -p 5432:5432 -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_HOST_AUTH_METHOD=trust --name qv-pg postgres:16
docker run -d --rm -p 8123:8123 --name qv-ch clickhouse/clickhouse-server:24
npm ci && npm run build -w frontend
SERVE_STATIC=1 uv run --frozen queryview-backend > /tmp/backend.log 2>&1 &
# wait for /api/health, then:
BASE_URL=http://localhost:8000 uv run --group test pytest e2e/test_postgres.py -v
```

Expected: PASS (both Postgres e2e tests)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml e2e/conftest.py e2e/test_postgres.py
git commit -m "test: Postgres e2e + CI service container"
```

---

## Self-Review

**Spec coverage:** Postgres connect/test (Task 2 `test`) ✓; real-database picker
via `pg_database` (Task 2 `list_databases`) ✓; paginated query with `"`-quoting
and `_qv` alias (Task 2 `run_query`, asserted in unit test) ✓; describe via
prepared-statement attributes (Task 2) ✓; serialization through the shared TSV/CSV
serializer (Task 2) ✓; dashboards (Task 3 gate + Plan 1 loop) ✓; predefined
queries by type (already type-driven, no change) ✓; frontend form (Task 4) ✓;
CI service + e2e (Task 5) ✓; asyncpg dependency (Task 1) ✓.

**Placeholder scan:** none — all code is concrete.

**Type consistency:** `PostgresDriver` matches the `Driver` Protocol from Plan 1
(same method names/arity/`fmt` values). `requires_database` consumed by Task 3
via `getattr(..., True)`. Registry construction matches Plan 1's `__init__.py`
shape. Frontend `DriverMeta` fields match Plan 1, Task 7's type exactly.
</content>
