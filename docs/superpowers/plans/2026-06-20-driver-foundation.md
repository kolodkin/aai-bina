# Postgres & DuckDB — Plan 1: Driver Foundation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hardcoded ClickHouse-only data path with a pluggable `Driver` abstraction (ClickHouse as the sole driver, no behavior change) and an encrypted-JSON connection store, exposed through a driver-agnostic `/api/db/*` API.

**Architecture:** One shared flow (FastAPI handlers + `connect.py` sessions/storage) dispatches every per-backend step to `DRIVERS[connection.type]`, a registry of objects satisfying a `typing.Protocol`. Driver-specific config is persisted as a single AES-GCM-encrypted JSON blob. This plan ships ClickHouse on the new rails; Plans 2 and 3 add Postgres and DuckDB by registering a driver and a frontend form entry — no flow changes.

**Tech Stack:** Python 3.11+, FastAPI, SQLModel/SQLAlchemy (async, aiosqlite), Alembic, cryptography (AES-256-GCM), httpx; React + TypeScript (Vite) frontend; pytest (backend unit) + Playwright (e2e).

## Global Constraints

- Python `requires-python = ">=3.11"`; keep new deps in `[project.dependencies]`.
- The backend is single-process; SQLite is single-writer; schema is owned by Alembic (`alembic upgrade head` on startup) — never `create_all`.
- Passwords/secrets are **never** stored in plaintext. The whole connection config is stored as `base64(AES-GCM(iv ‖ ciphertext))` over JSON, using the existing key resolution (`DB_ENCRYPTION_KEY` or `<DB_PATH>.key`).
- Output contract is unchanged: query results are `TabSeparatedWithNames` text by default, `CSVWithNames` when CSV is requested; the frontend parses `\n`/`\t` and the dashboard parser reads TSV.
- ClickHouse behavior must not change in this plan: identical SQL, identical responses, all existing e2e green.
- Existing data-testids used by e2e (`clickhouse-form`, `ch-name`, `ch-host`, `ch-port`, `ch-username`, `ch-password`, `ch-test`, `ch-connect`, `ch-result`, `db-picker`, `db-option`) must keep working.

---

### Task 1: Driver Protocol, dialect helpers, and row serializer

**Files:**
- Create: `backend/queryview/drivers/__init__.py`
- Create: `backend/queryview/drivers/base.py`
- Test: `backend/tests/test_drivers_base.py`

**Interfaces:**
- Produces:
  - `class QueryResult(NamedTuple): ok: bool; value: str`
  - `class Driver(Protocol)` with `type: str`, `parse_config(body) -> tuple[Any|None, str|None]`, `config_to_dict(config) -> dict`, `config_from_dict(data) -> Any`, `async test(config) -> dict`, `async list_databases(config) -> tuple[bool, list[str]|str]`, `async run_query(config, sql, database, limit, offset, order_by, fmt) -> QueryResult`, `async describe_query(config, sql, database) -> tuple[bool, list[dict[str,str]]|str]`
  - `build_order_by(order_by: list[dict]|None, quote: str) -> str`
  - `wrap_paginated(sql: str, order_clause: str, limit: int, offset: int, alias: str|None = None) -> str`
  - `serialize_rows(columns: list[str], rows: list, fmt: str) -> str` (`fmt` in `{"tsv","csv"}`)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_drivers_base.py
"""Driver dialect helpers and the row serializer (the shared output contract)."""
from __future__ import annotations

from queryview.drivers.base import build_order_by, serialize_rows, wrap_paginated


def test_build_order_by_quotes_and_whitelists_direction():
    assert build_order_by([{"name": "a", "dir": "desc"}], "`") == "ORDER BY `a` DESC"
    # Unknown direction falls back to ASC; quote chars in the name are doubled.
    assert build_order_by([{"name": "a`b", "dir": "x"}], "`") == "ORDER BY `a``b` ASC"
    assert build_order_by([{"name": "a"}], '"') == 'ORDER BY "a" ASC'
    assert build_order_by(None, "`") == ""
    assert build_order_by([{"bad": 1}], "`") == ""


def test_wrap_paginated_matches_clickhouse_shape_without_alias():
    out = wrap_paginated("SELECT 1;", "", 100, 0, alias=None)
    assert out == "SELECT * FROM (\nSELECT 1\n) LIMIT 100 OFFSET 0"


def test_wrap_paginated_adds_alias_and_order():
    out = wrap_paginated("SELECT 1", "ORDER BY \"a\" ASC", 10, 5, alias="_qv")
    assert out == 'SELECT * FROM (\nSELECT 1\n) AS _qv ORDER BY "a" ASC LIMIT 10 OFFSET 5'


def test_serialize_rows_tsv_with_names_and_nulls():
    out = serialize_rows(["id", "name"], [[1, "a"], [2, None]], "tsv")
    assert out == "id\tname\n1\ta\n2\t"


def test_serialize_rows_csv_quotes_and_uses_lf():
    out = serialize_rows(["a", "b"], [["x,y", "z"]], "csv")
    assert out == 'a,b\n"x,y",z'


def test_serialize_rows_empty_is_just_header():
    assert serialize_rows(["a"], [], "tsv") == "a"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --group test pytest tests/test_drivers_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'queryview.drivers'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/queryview/drivers/base.py
"""The driver contract (Protocol) plus dialect helpers and the row serializer
shared by row-returning drivers. No backend/storage concerns here."""
from __future__ import annotations

import csv
import io
from typing import Any, NamedTuple, Protocol, runtime_checkable


class QueryResult(NamedTuple):
    ok: bool
    value: str  # serialized rows when ok; an error message otherwise


@runtime_checkable
class Driver(Protocol):
    type: str

    def parse_config(self, body: Any) -> tuple[Any | None, str | None]: ...
    def config_to_dict(self, config: Any) -> dict[str, Any]: ...
    def config_from_dict(self, data: dict[str, Any]) -> Any: ...
    async def test(self, config: Any) -> dict[str, Any]: ...
    async def list_databases(self, config: Any) -> tuple[bool, list[str] | str]: ...
    async def run_query(
        self, config: Any, sql: str, database: str | None,
        limit: int, offset: int, order_by: list[dict[str, Any]] | None, fmt: str,
    ) -> QueryResult: ...
    async def describe_query(
        self, config: Any, sql: str, database: str | None,
    ) -> tuple[bool, list[dict[str, str]] | str]: ...


def build_order_by(order_by: list[dict[str, Any]] | None, quote: str) -> str:
    """`ORDER BY` clause from `[{"name","dir"}]`. Names are `quote`-quoted (any
    embedded quote doubled) and directions whitelisted to ASC/DESC, so malformed
    input can't inject SQL. Empty/absent input yields no clause."""
    if not order_by:
        return ""
    parts: list[str] = []
    for col in order_by:
        if not isinstance(col, dict):
            continue
        name = col.get("name")
        if not isinstance(name, str) or not name:
            continue
        raw_dir = col.get("dir")
        direction = raw_dir.upper() if isinstance(raw_dir, str) else ""
        if direction not in ("ASC", "DESC"):
            direction = "ASC"
        escaped = name.replace(quote, quote + quote)
        parts.append(f"{quote}{escaped}{quote} {direction}")
    if not parts:
        return ""
    return "ORDER BY " + ", ".join(parts)


def wrap_paginated(
    sql: str, order_clause: str, limit: int, offset: int, alias: str | None = None,
) -> str:
    """Wrap a SELECT in a paginating subselect. `alias` (e.g. `_qv`) is required
    by Postgres/DuckDB for a derived table; ClickHouse passes alias=None to keep
    its historical SQL byte-for-byte identical."""
    inner = sql.rstrip().rstrip(";")
    head = f"SELECT * FROM (\n{inner}\n)"
    if alias:
        head += f" AS {alias}"
    clauses = [head]
    if order_clause:
        clauses.append(order_clause)
    clauses.append(f"LIMIT {int(limit)} OFFSET {int(offset)}")
    return " ".join(clauses)


def serialize_rows(columns: list[str], rows: list[Any], fmt: str) -> str:
    """Serialize rows to the text contract ClickHouse emits: TabSeparatedWithNames
    (fmt='tsv') or CSVWithNames (fmt='csv'). None -> empty field. Non-strings are
    str()-ified. No trailing newline (matches ClickHouse's stripped output)."""
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if v is None else str(v) for v in row])
        return buf.getvalue().rstrip("\n")
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)
```

```python
# backend/queryview/drivers/__init__.py
"""Driver registry: maps a connection `type` to the Driver that executes it.
Plans 2 and 3 append PostgresDriver / DuckDBDriver to DRIVERS."""
from __future__ import annotations

from .base import Driver, QueryResult, build_order_by, serialize_rows, wrap_paginated
from .clickhouse import ClickHouseDriver

DRIVERS: dict[str, Driver] = {d.type: d for d in (ClickHouseDriver(),)}

__all__ = [
    "Driver",
    "QueryResult",
    "DRIVERS",
    "build_order_by",
    "serialize_rows",
    "wrap_paginated",
]
```

> Note: `__init__.py` imports `clickhouse`, created in Task 2. Until then the
> package import fails — that's expected; Task 1's test imports `drivers.base`
> directly, which does not import `clickhouse`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run --group test pytest tests/test_drivers_base.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/queryview/drivers/base.py backend/queryview/drivers/__init__.py backend/tests/test_drivers_base.py
git commit -m "feat: driver Protocol, dialect helpers, and row serializer"
```

---

### Task 2: ClickHouse driver on the new rails

**Files:**
- Create: `backend/queryview/drivers/clickhouse.py` (moved + adapted from `backend/queryview/clickhouse.py`)
- Delete: `backend/queryview/clickhouse.py`
- Test: `backend/tests/test_driver_clickhouse.py`

**Interfaces:**
- Consumes: `QueryResult`, `build_order_by`, `wrap_paginated` (Task 1).
- Produces:
  - `@dataclass(frozen=True) class ChConfig: host:str; port:int; username:str; password:str`
  - `async def ch_query(c, query, database=None, fmt=None) -> ChResult` (low-level HTTP, unchanged)
  - `class ClickHouseDriver` with `type = "clickhouse"` satisfying `Driver`
  - Registry entry `DRIVERS["clickhouse"]` (via `__init__.py` from Task 1)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_driver_clickhouse.py
"""ClickHouse driver: config round-trip, validation, registry conformance, and
that run_query builds the historical paginated SQL (no network needed)."""
from __future__ import annotations

from queryview.drivers import DRIVERS, Driver
from queryview.drivers.clickhouse import ChConfig, ClickHouseDriver


def test_registry_has_clickhouse_satisfying_protocol():
    d = DRIVERS["clickhouse"]
    assert isinstance(d, Driver)
    assert d.type == "clickhouse"


def test_parse_config_validates_host_and_port():
    d = ClickHouseDriver()
    cfg, err = d.parse_config({"host": "h", "port": "8123", "username": "u", "password": "p"})
    assert err is None and cfg == ChConfig("h", 8123, "u", "p")
    assert d.parse_config({"port": 8123})[0] is None       # missing host
    assert d.parse_config({"host": "h", "port": 0})[0] is None  # bad port


def test_config_dict_round_trip():
    d = ClickHouseDriver()
    cfg = ChConfig("h", 8123, "u", "p")
    assert d.config_from_dict(d.config_to_dict(cfg)) == cfg


def test_run_query_builds_clickhouse_sql(monkeypatch):
    d = ClickHouseDriver()
    seen = {}

    async def fake_ch_query(c, query, database=None, fmt=None):
        from queryview.drivers.clickhouse import ChResult
        seen["query"] = query
        seen["fmt"] = fmt
        seen["database"] = database
        return ChResult(True, "ok")

    monkeypatch.setattr("queryview.drivers.clickhouse.ch_query", fake_ch_query)
    import asyncio
    r = asyncio.run(
        d.run_query(ChConfig("h", 1, "u", ""), "SELECT 1;", "db", 100, 0,
                    [{"name": "a", "dir": "DESC"}], "tsv")
    )
    assert r.ok and r.value == "ok"
    assert seen["query"] == "SELECT * FROM (\nSELECT 1\n) ORDER BY `a` DESC LIMIT 100 OFFSET 0"
    assert seen["fmt"] == "TabSeparatedWithNames"
    assert seen["database"] == "db"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --group test pytest tests/test_driver_clickhouse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'queryview.drivers.clickhouse'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/queryview/drivers/clickhouse.py` with the existing low-level
functions (copied verbatim from `backend/queryview/clickhouse.py`: `ChConfig`,
`ChResult`, `ch_query`, `parse_ch_config`, and the bodies of `test_connection`,
`list_databases`, `describe_query`) plus the driver class:

```python
# backend/queryview/drivers/clickhouse.py
"""ClickHouse driver: the HTTP-interface client and a Driver implementation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

import httpx

from .base import QueryResult, build_order_by, wrap_paginated

CH_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class ChConfig:
    host: str
    port: int
    username: str
    password: str


class ChResult(NamedTuple):
    ok: bool
    value: str


async def ch_query(c: ChConfig, query: str, database: str | None = None,
                   fmt: str | None = None) -> ChResult:
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


def parse_ch_config(body: Any) -> tuple[ChConfig | None, str | None]:
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
    return ChConfig(host=host, port=port, username=username, password=password), None


class ClickHouseDriver:
    type = "clickhouse"

    def parse_config(self, body: Any) -> tuple[ChConfig | None, str | None]:
        return parse_ch_config(body)

    def config_to_dict(self, config: ChConfig) -> dict[str, Any]:
        return {
            "host": config.host, "port": config.port,
            "username": config.username, "password": config.password,
        }

    def config_from_dict(self, data: dict[str, Any]) -> ChConfig:
        return ChConfig(
            host=data["host"], port=int(data["port"]),
            username=data.get("username", ""), password=data.get("password", ""),
        )

    async def test(self, config: ChConfig) -> dict[str, Any]:
        r = await ch_query(config, "SELECT 1")
        if r.ok:
            return {"ok": True, "message": f"Connected — SELECT 1 returned {r.value}"}
        return {"ok": False, "message": r.value}

    async def list_databases(self, config: ChConfig) -> tuple[bool, list[str] | str]:
        r = await ch_query(config, "SHOW DATABASES")
        if not r.ok:
            return False, r.value
        return True, [s.strip() for s in r.value.split("\n") if s.strip()]

    async def run_query(self, config: ChConfig, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict[str, Any]] | None, fmt: str) -> QueryResult:
        order_clause = build_order_by(order_by, "`")
        paginated = wrap_paginated(sql, order_clause, limit, offset, alias=None)
        ch_fmt = "CSVWithNames" if fmt == "csv" else "TabSeparatedWithNames"
        r = await ch_query(config, paginated, database=database, fmt=ch_fmt)
        return QueryResult(r.ok, r.value)

    async def describe_query(self, config: ChConfig, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]:
        inner = sql.rstrip().rstrip(";")
        r = await ch_query(config, f"DESCRIBE (\n{inner}\n)", database=database,
                           fmt="TabSeparated")
        if not r.ok:
            return False, r.value
        fields: list[dict[str, str]] = []
        for line in r.value.split("\n"):
            if not line.strip():
                continue
            cols = line.split("\t")
            if len(cols) < 2:
                continue
            fields.append({"name": cols[0], "type": cols[1]})
        return True, fields
```

Then delete the old module:

```bash
git rm backend/queryview/clickhouse.py
```

> `connect.py`, `main.py`, and `dashboard_queries.py` still import the old paths
> and will fail to import until Tasks 5–6 update them. That's fine: Task 2's test
> imports only `queryview.drivers...`. Do not run the full suite until Task 7.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run --group test pytest tests/test_driver_clickhouse.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/queryview/drivers/clickhouse.py backend/tests/test_driver_clickhouse.py
git rm backend/queryview/clickhouse.py
git commit -m "feat: ClickHouse driver implementing the Driver protocol"
```

---

### Task 3: Encrypted-JSON connection store + migration

**Files:**
- Modify: `backend/queryview/connect.py` (model, encryption helpers, save/load)
- Create: `backend/queryview/migrations/versions/<rev>_connection_config_blob.py`
- Test: `backend/tests/test_connect_store.py`
- Test: `backend/tests/test_migrations.py:17` (extend with the data-migration case)

**Interfaces:**
- Consumes: `DRIVERS` (Task 1/2), `ChConfig`/`ClickHouseDriver` for tests.
- Produces:
  - `class Connection` columns: `id, name, type, config, database, last_active_at` (no host/port/username/password)
  - `_encrypt_str(plain: str) -> str`, `_decrypt_str(stored: str) -> str`
  - `_save_active_connection(name: str, config: Any, conn_type: str) -> None`
  - `StoredConnection(name, type, config: Any, database)`
  - unchanged public coroutines `_connection_by_name`, `_latest_active_connection`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_connect_store.py
"""The connection store round-trips a driver config through the encrypted JSON
blob, keyed by type, and never persists secrets in plaintext."""
from __future__ import annotations

import asyncio
import sqlite3
import os

from queryview.connect import _connection_by_name, _save_active_connection
from queryview.drivers.clickhouse import ChConfig


def _run(coro):
    return asyncio.run(coro)


def test_save_then_load_round_trips_config_and_type():
    cfg = ChConfig("h", 8123, "u", "s3cret")
    _run(_save_active_connection("ch1", cfg, "clickhouse"))
    stored = _run(_connection_by_name("ch1"))
    assert stored is not None
    assert stored.type == "clickhouse"
    assert stored.config == cfg


def test_password_is_not_stored_in_plaintext():
    _run(_save_active_connection("ch2", ChConfig("h", 8123, "u", "TOPSECRET"), "clickhouse"))
    con = sqlite3.connect(os.environ["DB_PATH"])
    try:
        blob = con.execute("SELECT config FROM connections WHERE name='ch2'").fetchone()[0]
    finally:
        con.close()
    assert "TOPSECRET" not in blob
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --group test pytest tests/test_connect_store.py -v`
Expected: FAIL (currently `_save_active_connection` writes host/port columns; after the model change below it passes). It may error on import first — proceed to Step 3.

- [ ] **Step 3: Write minimal implementation**

In `backend/queryview/connect.py`:

(a) Replace the import of the old clickhouse module:

```python
# old:
from .clickhouse import (
    ChConfig, ch_query, describe_query as ch_describe_query, list_databases,
)
# new:
import json
from typing import Any
from .drivers import DRIVERS
```

(b) Replace the `Connection` model:

```python
class Connection(SQLModel, table=True):
    __tablename__ = "connections"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    type: str = Field(default="clickhouse", index=True)
    config: str  # base64(AES-GCM(json.dumps(driver config))) — never plaintext
    database: str | None = Field(default=None)
    last_active_at: int
```

(c) Rename the password helpers to generic string helpers (same crypto):

```python
def _encrypt_str(plain: str) -> str:
    iv = os.urandom(12)
    ct = AESGCM(_key_bytes()).encrypt(iv, plain.encode("utf-8"), None)
    return base64.b64encode(iv + ct).decode("ascii")


def _decrypt_str(stored: str) -> str:
    combined = base64.b64decode(stored)
    iv, ct = combined[:12], combined[12:]
    return AESGCM(_key_bytes()).decrypt(iv, ct, None).decode("utf-8")
```

(d) Replace `StoredConnection`, `_save_active_connection`, and `_row_to_stored`:

```python
@dataclass
class StoredConnection:
    name: str
    type: str
    config: Any
    database: str | None


async def _save_active_connection(name: str, config: Any, conn_type: str) -> None:
    blob = _encrypt_str(json.dumps(DRIVERS[conn_type].config_to_dict(config)))
    now = _now_ms()
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Connection).where(Connection.name == name))).first()
        if row is None:
            row = Connection(name=name, type=conn_type, config=blob, last_active_at=now)
        else:
            row.type = conn_type
            row.config = blob
            row.last_active_at = now
        s.add(row)
        await s.commit()


def _row_to_stored(row: Connection | None) -> StoredConnection | None:
    if row is None:
        return None
    try:
        data = json.loads(_decrypt_str(row.config))
        config = DRIVERS[row.type].config_from_dict(data)
    except Exception:
        # Unreadable (key changed) or unknown type — treat as unavailable.
        return None
    return StoredConnection(
        name=row.name, type=row.type, config=config, database=row.database,
    )
```

(e) Delete the now-unused `_encrypt_password`/`_decrypt_password` definitions.

(f) Create the migration (`<rev>` is the generated hash; `down_revision` is the
current head `9a536b7c0328`):

```python
# backend/queryview/migrations/versions/<rev>_connection_config_blob.py
"""connections: per-driver columns -> one encrypted JSON config blob

Revision ID: <rev>
Revises: 9a536b7c0328
Create Date: 2026-06-20

"""
from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "<rev>"
down_revision: Union[str, None] = "9a536b7c0328"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add the new blob column (nullable while we backfill).
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.add_column(sa.Column("config", sqlmodel.sql.sqltypes.AutoString(), nullable=True))

    # 2. Backfill each ClickHouse row: decrypt the old password, build the config
    #    dict, re-encrypt the whole JSON. The app's key loader is imported here.
    from queryview.connect import _decrypt_str, _encrypt_str

    conn = op.get_bind()
    rows = conn.execute(
        sa.text("SELECT id, host, port, username, password FROM connections")
    ).fetchall()
    for rid, host, port, username, password in rows:
        plain = _decrypt_str(password) if password else ""
        blob = _encrypt_str(json.dumps(
            {"host": host, "port": port, "username": username, "password": plain}
        ))
        conn.execute(
            sa.text("UPDATE connections SET config = :c WHERE id = :i"),
            {"c": blob, "i": rid},
        )

    # 3. Enforce NOT NULL and drop the per-driver columns.
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.alter_column("config", existing_type=sqlmodel.sql.sqltypes.AutoString(), nullable=False)
        batch_op.drop_column("host")
        batch_op.drop_column("port")
        batch_op.drop_column("username")
        batch_op.drop_column("password")


def downgrade() -> None:
    with op.batch_alter_table("connections", schema=None) as batch_op:
        batch_op.add_column(sa.Column("host", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("port", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("username", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.add_column(sa.Column("password", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.drop_column("config")
```

> Generate the revision id by copying the filename pattern of the existing
> migration; or run `cd backend && DB_PATH=/tmp/x.db uv run alembic revision -m
> "connection config blob"` to get a stamped empty file, then paste the
> `upgrade`/`downgrade` bodies above into it and delete `/tmp/x.db`.

- [ ] **Step 4: Add the migration data-path test**

Append to `backend/tests/test_migrations.py`:

```python
def test_config_blob_migration_backfills_existing_clickhouse_row():
    """A row written at the pre-blob revision is rewrapped into an encrypted
    JSON config that decrypts back to the original host/port/user/password."""
    import json
    import sqlite3
    from alembic import command

    from queryview.connect import _alembic_config, _db_path, _encrypt_str, _decrypt_str

    cfg = _alembic_config()
    command.downgrade(cfg, "9a536b7c0328")  # the per-column schema

    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            "INSERT INTO connections (name, type, host, port, username, password, "
            "database, last_active_at) VALUES (?,?,?,?,?,?,?,?)",
            ("legacy", "clickhouse", "h", 8123, "u", _encrypt_str("pw"), "db", 1),
        )
        con.commit()
    finally:
        con.close()

    command.upgrade(cfg, "head")

    con = sqlite3.connect(_db_path())
    try:
        blob = con.execute("SELECT config FROM connections WHERE name='legacy'").fetchone()[0]
    finally:
        con.close()
    data = json.loads(_decrypt_str(blob))
    assert data == {"host": "h", "port": 8123, "username": "u", "password": "pw"}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run --group test pytest tests/test_connect_store.py tests/test_migrations.py -v`
Expected: PASS (store round-trip, no-plaintext, fresh-DB head, blob backfill)

- [ ] **Step 6: Commit**

```bash
git add backend/queryview/connect.py backend/queryview/migrations/versions/ backend/tests/test_connect_store.py backend/tests/test_migrations.py
git commit -m "feat: store connection config as an encrypted JSON blob"
```

---

### Task 4: Flow dispatch + database-gate relaxation in connect.py

**Files:**
- Modify: `backend/queryview/connect.py` (sessions, `connect_new`, `run_query`, `describe_query`, `_build_session`)
- Test: `backend/tests/test_connect_flow.py`

**Interfaces:**
- Consumes: `DRIVERS`, `StoredConnection` (Task 3).
- Produces:
  - `async def connect_new(sid, name, config, conn_type) -> dict` (now takes `conn_type`)
  - `run_query`/`describe_query` dispatch via `DRIVERS[session.type]`; the
    "select a database first" gate applies only when the session exposes
    databases (skipped when `databases == []`, e.g. DuckDB).
  - `_build_session`/`_SessionState` unchanged in shape (already carry `type`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_connect_flow.py
"""Flow dispatch: connect_new persists with the given type; run_query routes to
the driver; the database gate is skipped when the driver exposes no databases."""
from __future__ import annotations

import asyncio

import queryview.connect as connect
from queryview.drivers import DRIVERS
from queryview.drivers.base import QueryResult


def _run(coro):
    return asyncio.run(coro)


class _FakeDriver:
    type = "fake"
    def parse_config(self, body): return {"v": 1}, None
    def config_to_dict(self, c): return c
    def config_from_dict(self, d): return d
    async def test(self, c): return {"ok": True, "message": "ok"}
    async def list_databases(self, c): return True, []  # no picker
    async def run_query(self, c, sql, database, limit, offset, order_by, fmt):
        return QueryResult(True, f"ran:{sql}:db={database}")
    async def describe_query(self, c, sql, database):
        return True, [{"name": "x", "type": "int"}]


def test_run_query_skips_db_gate_when_no_databases(monkeypatch):
    monkeypatch.setitem(DRIVERS, "fake", _FakeDriver())
    sid = "s-fake"
    _run(connect.connect_new(sid, "f", {"v": 1}, "fake"))
    out = _run(connect.run_query(sid, "SELECT 1", 10, 0, "tsv", None))
    assert out["ok"] and out["output"] == "ran:SELECT 1:db=None"


def test_run_query_requires_database_when_picker_present(monkeypatch):
    class _WithDbs(_FakeDriver):
        type = "fakedb"
        async def list_databases(self, c): return True, ["a", "b"]
    monkeypatch.setitem(DRIVERS, "fakedb", _WithDbs())
    sid = "s-fakedb"
    _run(connect.connect_new(sid, "g", {"v": 1}, "fakedb"))
    out = _run(connect.run_query(sid, "SELECT 1", 10, 0, "tsv", None))
    assert out["ok"] is False and out["reason"] == "no-database"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --group test pytest tests/test_connect_flow.py -v`
Expected: FAIL (`connect_new` takes no `conn_type`, and run_query still hardcodes ClickHouse)

- [ ] **Step 3: Write minimal implementation**

In `connect.py`:

(a) `_build_session` already lists via a driver — change it to use the registry:

```python
async def _build_session(name, config, database, conn_type="clickhouse"):
    ok, result = await DRIVERS[conn_type].list_databases(config)
    if not ok:
        return None, result
    databases = result
    return (
        _SessionState(
            name=name, type=conn_type, config=config, databases=databases,
            database=database if database and database in databases else None,
        ),
        None,
    )
```

(b) `connect_new` takes the type and threads it through:

```python
async def connect_new(sid, name, config, conn_type):
    state, message = await _build_session(name, config, None, conn_type)
    if state is None:
        return {"ok": False, "message": message}
    _set_session_entry(sid, state)
    await _save_active_connection(name, config, conn_type)
    return {"ok": True, "name": name, "type": state.type, "databases": state.databases}
```

(c) Replace the body of `_build_order_by` usage: delete the local `_build_order_by`
in `connect.py` (now in `drivers.base`) and route `run_query`/`describe_query`
through the driver, relaxing the gate:

```python
async def describe_query(sid, sql):
    await _ensure_session(sid)
    s = _get_session_entry(sid)
    if s is None:
        return {"ok": False, "message": "not connected", "reason": "no-session"}
    if s.databases and not s.database:
        return {"ok": False, "message": "select a database first", "reason": "no-database"}
    ok, result = await DRIVERS[s.type].describe_query(s.config, sql, s.database)
    if not ok:
        return {"ok": False, "message": result}
    return {"ok": True, "fields": result}


async def run_query(sid, sql, limit, offset, fmt, order_by=None):
    await _ensure_session(sid)
    s = _get_session_entry(sid)
    if s is None:
        return {"ok": False, "message": "not connected", "reason": "no-session"}
    if s.databases and not s.database:
        return {"ok": False, "message": "select a database first", "reason": "no-database"}
    r = await DRIVERS[s.type].run_query(
        s.config, sql, s.database, limit, offset, order_by, fmt
    )
    if not r.ok:
        return {"ok": False, "message": r.value}
    return {"ok": True, "output": r.value}
```

> Note the new `run_query` `fmt` is the logical `"tsv"`/`"csv"` (Task 7 updates
> `main.py` to pass these). `open_saved` and `select_database` are unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run --group test pytest tests/test_connect_flow.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/queryview/connect.py backend/tests/test_connect_flow.py
git commit -m "feat: route the session flow through the driver registry"
```

---

### Task 5: Dashboard runner through the driver

**Files:**
- Modify: `backend/queryview/dashboard_queries.py`
- Test: covered by existing `backend/tests/test_dashboards.py` + e2e (Task 8)

**Interfaces:**
- Consumes: `DRIVERS`, `StoredConnection` (with `.type`, `.config`, `.database`).
- Produces: `run_queries_for_connection(name, queries)` unchanged signature/return.

- [ ] **Step 1: Update the implementation**

Replace the ClickHouse import and the per-query call:

```python
# old: from .clickhouse import ch_query
from .drivers import DRIVERS
from .connect import _connection_by_name
```

Inside `run_queries_for_connection`, replace the query loop body:

```python
    driver = DRIVERS[stored.type]
    results: dict[str, dict[str, list[str]]] = {}
    for qname, sql in queries.items():
        r = await driver.run_query(
            stored.config, sql, stored.database,
            limit=DASHBOARD_ROW_CAP, offset=0, order_by=None, fmt="tsv",
        )
        if not r.ok:
            return {"ok": False, "reason": "query", "message": f"{qname}: {r.value}"}
        results[qname] = _parse_tsv_columns(r.value)
    return {"ok": True, "results": results}
```

The no-database guard stays as-is for drivers that expose databases. For
driver parity, relax it to mirror the session gate:

```python
    if stored.type == "clickhouse" and not stored.database:
        return {"ok": False, "reason": "no-database", "message": (
            f'connection "{name}" has no selected database — select one for it '
            "or fully-qualify table names as db.table")}
```

> A cleaner check is "driver exposes databases"; since that needs a network
> call here, gate on a per-driver class attribute added in Plans 2–3
> (`requires_database: bool`). For this plan, ClickHouse is the only driver, so
> the explicit `clickhouse` check is correct and YAGNI-clean.

- [ ] **Step 2: Run the dashboard unit tests**

Run: `cd backend && uv run --group test pytest tests/test_dashboards.py -v`
Expected: PASS (these don't hit a real DB; they exercise the store + validation)

- [ ] **Step 3: Commit**

```bash
git add backend/queryview/dashboard_queries.py
git commit -m "feat: run dashboard queries through the driver registry"
```

---

### Task 6: Generalize the API to `/api/db/*`

**Files:**
- Modify: `backend/queryview/main.py`
- Test: `backend/tests/test_api_db.py`

**Interfaces:**
- Consumes: `DRIVERS`, `connect_new`, `open_saved`, `select_database`, `run_query`, `describe_query`, `get_session`.
- Produces endpoints: `POST /api/db/test`, `/api/db/connect`, `/api/db/open`,
  `/api/db/database`, `/api/db/query`, `/api/db/describe`. The old
  `/api/clickhouse/*` routes are removed.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_api_db.py
"""The /api/db surface: unknown type is a 400; validation errors are 400;
the old /api/clickhouse paths are gone (404)."""
from __future__ import annotations

from fastapi.testclient import TestClient

from queryview.main import app


def test_connect_unknown_type_is_400():
    c = TestClient(app)
    r = c.post("/api/db/connect", json={"type": "nope", "name": "x", "host": "h", "port": 1})
    assert r.status_code == 400
    assert "unknown" in r.json()["message"].lower()


def test_connect_validation_error_is_400():
    c = TestClient(app)
    r = c.post("/api/db/connect", json={"type": "clickhouse", "name": "x"})  # no host
    assert r.status_code == 400


def test_old_clickhouse_path_is_gone():
    c = TestClient(app)
    assert c.post("/api/clickhouse/connect", json={}).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run --group test pytest tests/test_api_db.py -v`
Expected: FAIL (old paths still 200/400, `/api/db/*` not found)

- [ ] **Step 3: Write minimal implementation**

In `main.py`:

(a) Replace the clickhouse import:

```python
# old: from .clickhouse import parse_ch_config, test_connection
from .drivers import DRIVERS
```

(b) Add a small helper and replace the six `@app.post("/api/clickhouse/...")`
handlers with `/api/db/...`:

```python
def _driver_and_config(body: Any):
    """Resolve (driver, config) from a request body's `type`. Returns
    (driver, config, None) or (None, None, message)."""
    b = body if isinstance(body, dict) else {}
    conn_type = b.get("type") if isinstance(b.get("type"), str) else ""
    driver = DRIVERS.get(conn_type)
    if driver is None:
        return None, None, f"unknown connection type: {conn_type or '(none)'}"
    config, error = driver.parse_config(b)
    if error:
        return None, None, error
    return driver, config, None


@app.post("/api/db/test")
async def db_test(request: Request):
    driver, config, error = _driver_and_config(await _read_json(request))
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    return await driver.test(config)


@app.post("/api/db/connect")
async def db_connect(request: Request):
    body = await _read_json(request)
    driver, config, error = _driver_and_config(body)
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    b = body if isinstance(body, dict) else {}
    raw_name = b.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else driver.type
    return await connect_new(request.state.sid, name, config, driver.type)


@app.post("/api/db/open")
async def db_open(request: Request):
    b = await _read_json(request) or {}
    raw_name = b.get("name") if isinstance(b, dict) else None
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name:
        return JSONResponse({"ok": False, "message": "name required"}, status_code=400)
    r = await open_saved(request.state.sid, name)
    if not r["ok"]:
        return JSONResponse(
            {"ok": False, "message": r["message"]},
            status_code=404 if r.get("not_found") else 200,
        )
    return {"ok": True, "name": r["name"], "type": r["type"], "databases": r["databases"]}


@app.post("/api/db/database")
async def db_database(request: Request):
    b = await _read_json(request) or {}
    raw_db = b.get("database") if isinstance(b, dict) else None
    database = raw_db if isinstance(raw_db, str) else ""
    r = await select_database(request.state.sid, database)
    if not r["ok"]:
        return JSONResponse(
            {"ok": False, "message": r["message"]},
            status_code=409 if r["reason"] == "no-session" else 400,
        )
    return {"ok": True}


@app.post("/api/db/query")
async def db_query(request: Request):
    b = (await _read_json(request)) or {}
    b = b if isinstance(b, dict) else {}
    raw_sql = b.get("query")
    sql = raw_sql.strip() if isinstance(raw_sql, str) else ""
    if not sql:
        return JSONResponse({"ok": False, "message": "query required"}, status_code=400)
    limit = _parse_int(b.get("limit"), 100)
    limit = 100 if limit < 1 else min(limit, 1000)
    offset = _parse_int(b.get("offset"), 0)
    offset = 0 if offset < 0 else offset
    fmt = "csv" if b.get("format") == "csv" else "tsv"
    raw_order = b.get("order_by")
    order_by = raw_order if isinstance(raw_order, list) else None
    r = await run_query(request.state.sid, sql, limit, offset, fmt, order_by)
    if not r["ok"]:
        status = 409 if r.get("reason") == "no-session" else 200
        return JSONResponse({"ok": False, "message": r["message"]}, status_code=status)
    return {"ok": True, "output": r["output"]}


@app.post("/api/db/describe")
async def db_describe(request: Request):
    b = (await _read_json(request)) or {}
    b = b if isinstance(b, dict) else {}
    raw_sql = b.get("query")
    sql = raw_sql.strip() if isinstance(raw_sql, str) else ""
    if not sql:
        return JSONResponse({"ok": False, "message": "query required"}, status_code=400)
    r = await describe_query(request.state.sid, sql)
    if not r["ok"]:
        status = 409 if r.get("reason") in ("no-session", "no-database") else 200
        return JSONResponse({"ok": False, "message": r["message"]}, status_code=status)
    return {"ok": True, "fields": r["fields"]}
```

(c) Ensure `from typing import Any` is imported (it already is) and remove the
now-unused `parse_ch_config`/`test_connection` references.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run --group test pytest tests/test_api_db.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the whole backend suite**

Run: `cd backend && uv run --group test pytest -p no:cacheprovider tests -v`
Expected: PASS (all backend tests; `testpaths` is `e2e`, so target `tests` explicitly)

- [ ] **Step 6: Commit**

```bash
git add backend/queryview/main.py backend/tests/test_api_db.py
git commit -m "feat: generalize the connection API to /api/db/*"
```

---

### Task 7: Frontend — data-driven `new <type>` and `/api/db/*`

**Files:**
- Create: `frontend/src/drivers.ts` (the frontend driver registry)
- Modify: `frontend/src/QueryView.tsx` (prompt parsing, generic form, repointed fetches)
- Modify: `frontend/src/App.tsx` (repointed fetch)
- Test: existing e2e (`e2e/test_query.py`) must stay green; new e2e in Task 8.

**Interfaces:**
- Produces:
  - `export type DriverMeta = { type: string; label: string; fields: { key: string; label: string; testid: string; type: 'text'|'password'; default: string }[]; formTestid: string; testTestid: string; connectTestid: string; resultTestid: string }`
  - `export const DRIVERS: Record<string, DriverMeta>` (clickhouse entry only here)
  - A generic `ConnectionForm` rendering any `DriverMeta`.

- [ ] **Step 1: Create the frontend driver registry**

```typescript
// frontend/src/drivers.ts
// Frontend driver registry: drives the `new <type>` command and the connection
// form. Plans 2 and 3 append a postgres / duckdb entry — no other UI changes.
export type DriverField = {
  key: string
  label: string
  testid: string
  type: 'text' | 'password'
  default: string
}

export type DriverMeta = {
  type: string
  label: string
  fields: DriverField[]
  formTestid: string
  testTestid: string
  connectTestid: string
  resultTestid: string
}

export const DRIVERS: Record<string, DriverMeta> = {
  clickhouse: {
    type: 'clickhouse',
    label: 'ClickHouse',
    formTestid: 'clickhouse-form',
    testTestid: 'ch-test',
    connectTestid: 'ch-connect',
    resultTestid: 'ch-result',
    fields: [
      { key: 'name', label: 'Name', testid: 'ch-name', type: 'text', default: 'clickhouse' },
      { key: 'host', label: 'Host', testid: 'ch-host', type: 'text', default: 'localhost' },
      { key: 'port', label: 'Port', testid: 'ch-port', type: 'text', default: '8123' },
      { key: 'username', label: 'Username', testid: 'ch-username', type: 'text', default: 'default' },
      { key: 'password', label: 'Password', testid: 'ch-password', type: 'password', default: '' },
    ],
  },
}
```

- [ ] **Step 2: Rewrite the prompt + form wiring in `QueryView.tsx`**

Replace the hardcoded `ClickHouseForm` and the `'new clickhouse'` branch.

(2a) Imports and state — add at the top of `QueryView`:

```typescript
import { DRIVERS, type DriverMeta } from './drivers'
// ...
const [formType, setFormType] = useState<string | null>(null)
```

(2b) In `submitPrompt`, replace the `if (lower === 'new clickhouse')` block with a
generic `new <type>` parser:

```typescript
    if (lower.startsWith('new ')) {
      const type = lower.slice('new '.length).trim()
      if (DRIVERS[type]) {
        setFormType(type)
        setShowForm(true)
        setShowQuery(false)
        setHint(null)
      } else {
        setHint(`Unknown driver “${type}”. Try: ${Object.keys(DRIVERS).join(', ')}.`)
      }
      return
    }
```

Update the final unknown-command hint to:

```typescript
    setHint(
      `Unknown command “${raw}”. Try “new ${Object.keys(DRIVERS).join('|')}”, ` +
        `“connect <name>” or “dashboard <name>”.`,
    )
```

(2c) Replace `{showForm && <ClickHouseForm onConnected={handleConnected} />}` with:

```typescript
      {showForm && formType && DRIVERS[formType] && (
        <ConnectionForm meta={DRIVERS[formType]} onConnected={handleConnected} />
      )}
```

(2d) Repoint the three fetches in `QueryView` (`openSaved`, `selectDatabase`) and
the options_sql/run/describe/csv/save fetches in `QueryPanel` from
`/api/clickhouse/*` to `/api/db/*`. The query/describe/database/open bodies are
unchanged; only the path changes (the session already knows the type).

(2e) Replace the `ClickHouseForm` component with a generic `ConnectionForm`:

```typescript
function ConnectionForm({
  meta,
  onConnected,
}: {
  meta: DriverMeta
  onConnected: (name: string, type: string, databases: string[]) => void
}) {
  const [values, setValues] = useState<Record<string, string>>(
    () => Object.fromEntries(meta.fields.map((f) => [f.key, f.default])),
  )
  const [result, setResult] = useState<TestResult | null>(null)
  const [busy, setBusy] = useState(false)

  function body() {
    return JSON.stringify({ type: meta.type, ...values })
  }

  async function testConnection() {
    setBusy(true)
    setResult(null)
    try {
      const res = await fetch('/api/db/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body(),
      })
      setResult((await res.json()) as TestResult)
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : 'request failed' })
    } finally {
      setBusy(false)
    }
  }

  async function connect() {
    setBusy(true)
    setResult(null)
    try {
      const res = await fetch('/api/db/connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body(),
      })
      const data = await res.json()
      if (data.ok) {
        onConnected(
          data.name as string,
          (data.type ?? meta.type) as string,
          (data.databases ?? []) as string[],
        )
      } else {
        setResult({ ok: false, message: data.message ?? 'connect failed' })
      }
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : 'request failed' })
    } finally {
      setBusy(false)
    }
  }

  const fieldClass = 'glass-input w-full px-3 py-2'
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        connect()
      }}
      data-testid={meta.formTestid}
      className="glass-panel mt-6 space-y-4 p-6"
    >
      <h2 className="text-lg font-semibold">New {meta.label} connection</h2>
      {meta.fields.map((f) => (
        <label key={f.key} className="block text-sm font-medium text-slate-300">
          {f.label}
          <input
            type={f.type}
            value={values[f.key]}
            onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
            aria-label={f.label}
            data-testid={f.testid}
            className={`mt-1 ${fieldClass}`}
          />
        </label>
      ))}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={testConnection}
          data-testid={meta.testTestid}
          disabled={busy}
          className="glass-btn flex-1 px-4 py-2 font-medium"
        >
          Test connection
        </button>
        <button
          type="submit"
          data-testid={meta.connectTestid}
          disabled={busy}
          className="glass-btn-primary flex-1 px-4 py-2 font-medium"
        >
          Connect
        </button>
      </div>
      {result && (
        <p
          data-testid={meta.resultTestid}
          data-ok={result.ok}
          className={`text-sm ${result.ok ? 'text-emerald-300' : 'text-red-300'}`}
        >
          {result.message}
        </p>
      )}
    </form>
  )
}
```

(2f) In `App.tsx`, change `openConnection`'s fetch from `/api/clickhouse/open` to
`/api/db/open`.

(2g) Update the prompt placeholder text `'Type a command, e.g. new clickhouse'`
stays valid; leave it.

- [ ] **Step 3: Typecheck + build the frontend**

Run: `npm run build -w frontend`
Expected: build succeeds (tsc + vite), no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/drivers.ts frontend/src/QueryView.tsx frontend/src/App.tsx
git commit -m "feat: data-driven connection form and /api/db endpoints"
```

---

### Task 8: Full verification + docs

**Files:**
- Modify: `docs/connect.md`, `docs/api.md` (rename paths; note the JSON config column)
- Modify: `README.md` (if it references `/api/clickhouse/*` — grep first)
- Test: full e2e suite against a live backend + ClickHouse

- [ ] **Step 1: Update docs to the new API**

Grep and replace `/api/clickhouse/` → `/api/db/` in `docs/connect.md`,
`docs/api.md`, `docs/query.md`, `README.md`. In `docs/connect.md`, replace the
`connections` table DDL with the new shape:

```sql
CREATE TABLE connections (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  name           TEXT NOT NULL UNIQUE,
  type           TEXT NOT NULL,        -- selects the driver
  config         TEXT NOT NULL,        -- base64(AES-GCM(json.dumps(driver config)))
  database       TEXT,                 -- last selected database (nullable)
  last_active_at INTEGER NOT NULL
);
```

And replace the "Password encryption" paragraph to say the **whole config blob**
is encrypted (same key resolution), not just the password column.

- [ ] **Step 2: Run the full backend suite**

Run: `cd backend && uv run --group test pytest tests -v`
Expected: PASS (all backend unit tests)

- [ ] **Step 3: Run e2e against a live stack**

```bash
# ClickHouse for the e2e (matches CI's service container):
docker run -d --rm -p 8123:8123 --name qv-ch clickhouse/clickhouse-server:24
npm ci && npm run build -w frontend
SERVE_STATIC=1 uv run --frozen queryview-backend > /tmp/backend.log 2>&1 &
# wait for /api/health, then:
BASE_URL=http://localhost:8000 uv run --group test pytest e2e -v
```

Expected: PASS — the existing ClickHouse e2e (`new clickhouse` → pick `test` →
query/paginate/CSV/cell-views/params) is green through the new `/api/db/*` API.

- [ ] **Step 4: Commit**

```bash
git add docs/ README.md
git commit -m "docs: document the /api/db API and encrypted config column"
```

---

## Self-Review

**Spec coverage:** Driver Protocol (Task 1) ✓; Protocol-not-ABC (Task 1) ✓;
registry (Task 1/2) ✓; ClickHouse refactor with no behavior change (Task 2,
asserted by SQL test + e2e) ✓; encrypted JSON config + migration with data
backfill (Task 3) ✓; flow dispatch + database-gate relaxation (Task 4) ✓;
dashboards through driver (Task 5) ✓; `/api/db/*` generalization, old paths
dropped (Task 6) ✓; frontend data-driven form + repointed fetches (Task 7) ✓;
output TSV/CSV contract preserved (serializer Task 1, used by Plans 2–3) ✓; docs
(Task 8) ✓. Postgres/DuckDB drivers are intentionally out of scope (Plans 2–3).

**Placeholder scan:** `<rev>` in Task 3 is a real, generated Alembic id with
explicit generation instructions — not a placeholder. No TBD/TODO remain.

**Type consistency:** `Driver` method names/signatures match across Tasks 1, 2,
4, 5, 6. `run_query(... fmt)` uses logical `"tsv"`/`"csv"` consistently
(Task 4 connect.py, Task 6 main.py, Task 1 serializer). `connect_new(sid, name,
config, conn_type)` matches between Task 4 and Task 6. `StoredConnection`
fields (`name, type, config, database`) match Tasks 3/5.
</content>
