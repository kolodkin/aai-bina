# Postgres & DuckDB drivers — design

## Goal

Add **Postgres** and **DuckDB** as connection types alongside the existing
ClickHouse support, at **full feature parity**: connect, test, saved
connections, database picker, paginated query (TSV/CSV), describe/introspection,
dashboards, and predefined queries all work for every driver.

## Core insight: one flow, pluggable executors

The user-facing flow is identical across all backends:

```
test → connect → list "databases" → pick → query (paginate / order) → describe
```

Only the *execution* of each step differs (which protocol/SQL talks to the DB).
So the abstraction is a narrow **`Driver` Protocol**: one shared flow
(HTTP handlers + `connect.py` session/storage) dispatches to
`DRIVERS[connection.type]`. Nothing above the driver branches on `type`.

### Why `typing.Protocol`, not `abc.ABC`

There is a shared *contract* but **zero shared implementation** between an HTTP
client (ClickHouse), an asyncpg connection (Postgres), and an embedded library
(DuckDB). `Protocol` expresses exactly that — structural conformance, no
inheritance ceremony — and matches the codebase's existing module-of-functions
style. A registry `DRIVERS: dict[str, Driver]` type-checks each entry against the
protocol; a runtime smoke test asserts every registered driver conforms.

### The Driver contract (`backend/queryview/drivers/base.py`)

```python
class QueryResult(NamedTuple):
    ok: bool
    value: str   # serialized rows (TSV/CSV-with-names) when ok; error message otherwise

class Driver(Protocol):
    type: str

    def parse_config(self, body: Any) -> tuple[Any | None, str | None]: ...
    def config_from_row(self, row: Connection) -> Any: ...
    async def test(self, config: Any) -> dict[str, Any]: ...
    async def list_databases(self, config: Any) -> tuple[bool, list[str] | str]: ...
    async def run_query(self, config: Any, sql: str, database: str | None,
                        limit: int, offset: int,
                        order_by: list[dict] | None, fmt: str) -> QueryResult: ...
    async def describe_query(self, config: Any, sql: str,
                             database: str | None) -> tuple[bool, list[dict[str, str]] | str]: ...
```

Each driver owns its own config dataclass and its own dialect helpers
(identifier quoting + pagination wrapping live *inside* `run_query` /
`describe_query`, so the flow layer never constructs SQL). `config` is typed
`Any` at the protocol boundary because each driver round-trips its own concrete
type; the registry guarantees driver and config always match.

What differs per driver:

| Method | ClickHouse | Postgres | DuckDB |
|---|---|---|---|
| `parse_config` | host/port/user/pass | host/port/user/pass | `path` (or `:memory:`) |
| `test` (`SELECT 1`) | HTTP interface | asyncpg | duckdb lib |
| `list_databases` | `SHOW DATABASES` | `SELECT datname FROM pg_database WHERE datallowconn` | `[]` (no picker) |
| `run_query` | HTTP + `FORMAT` | asyncpg → serialize | duckdb → serialize |
| `describe_query` | `DESCRIBE (…)` | prepared / `LIMIT 0` cursor | cursor description |
| ident quote / paginate | `` ` `` , no alias | `"` , `AS _qv` alias | `"` , `AS _qv` alias |

## Module layout

```
backend/queryview/drivers/
    base.py        # Driver Protocol, QueryResult, DRIVERS registry, serialize helpers
    clickhouse.py  # existing clickhouse.py refactored to satisfy Driver
    postgres.py    # PgConfig + asyncpg-backed driver
    duckdb.py      # DuckConfig + duckdb-backed driver (sync calls via asyncio.to_thread)
```

`connect.py`, `dashboard_queries.py`, and `main.py` import from `drivers` and
dispatch by `type`; they contain no driver-specific SQL. The existing
`clickhouse.py` module path moves under `drivers/` (its public callers update to
go through the registry).

## Data model & storage

The `connections` table must hold DuckDB's path (no host/port/user/pass).
Alembic revision (SQLite batch mode):

- `host`, `port`, `username`, `password` → **nullable**.
- add `path TEXT` nullable (DuckDB file path; `:memory:` permitted).
- `type`, `name`, `database`, `last_active_at` unchanged.

Per-driver validation lives in `parse_config` (CH/PG require host+port; DuckDB
requires path). `_row_to_stored` becomes
`DRIVERS[row.type].config_from_row(row)`. Existing rows
(`type="clickhouse"`, `path` NULL) are untouched and keep working.

`StoredConnection.config` and `_SessionState.config` are typed `Any` (driver's
own config). All `connect.py` call sites become
`driver = DRIVERS[type]; await driver.<method>(config, …)`.

## Per-driver specifics

### ClickHouse (refactor, no behavior change)
Move the pagination wrapper and backtick `ORDER BY` quoting (currently in
`connect.py._build_order_by` / `run_query`) into the driver. Output and HTTP
behavior identical to today.

### Postgres (`asyncpg`)
- **List**: connect to a bootstrap database (`postgres`, fallback to the
  username) and `SELECT datname FROM pg_database WHERE datallowconn AND NOT
  datistemplate ORDER BY datname`. The picker selection (the existing
  `database` column) names the db that subsequent queries connect to.
- **Query**: open a short-lived connection to the selected db, run the
  paginating wrapper (`"`-quoted idents, `AS _qv` subquery alias — Postgres
  requires the alias), fetch rows, serialize to TSV/CSV-with-names.
- **Describe**: prepare the statement (or wrap as `… LIMIT 0`) and read column
  names + types from the statement/cursor description; map type OIDs to names.
- 5s timeout to match ClickHouse's `CH_TIMEOUT_SECONDS`.

### DuckDB (`duckdb`, sync lib via `asyncio.to_thread`)
- File-based: config is a `path` (default `:memory:`). No network, no
  credentials. `list_databases` returns `[]` → the flow skips the
  "select a database first" gate for `type="duckdb"` (queries run directly;
  schema-qualify in SQL as needed).
- **Query/Describe**: open the file (read-only where possible) per request, run
  in a worker thread, serialize rows / read `cursor.description`.

## Output contract

The frontend's `parseTsv` and CSV download consume text verbatim, and
`dashboard_queries._parse_tsv_columns` parses `TabSeparatedWithNames`. Postgres
and DuckDB therefore **serialize their rows to the same TSV/CSV-with-names text**
ClickHouse emits, via one shared serializer in `drivers/base.py`
(first line = column names; tab- or comma-joined rows; CSV via `csv` module).
No change to the frontend table renderer or the dashboard parser.

The "select a database first" gate (`run_query` / `describe_query` in
`connect.py`) is relaxed: required when the driver exposes databases
(CH/PG have a non-empty picker), skipped when it does not (DuckDB).

## API & frontend

### Endpoints (generalize `/api/clickhouse/*` → `/api/db/*`)
The old paths are **dropped** (self-contained app, no external consumers).

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/db/test` | `{type, …config}` | dispatch by `type` |
| POST | `/api/db/connect` | `{type, name, …config}` | dispatch by `type` |
| POST | `/api/db/open` | `{name}` | session learns type from the saved row |
| POST | `/api/db/database` | `{database}` | session knows its type |
| POST | `/api/db/query` | `{query, limit?, offset?, format?, order_by?}` | session knows its type |
| POST | `/api/db/describe` | `{query}` | session knows its type |

`/api/predefined-queries` and `/api/session` unchanged (already type-aware).

### Frontend
- Prompt commands: `new clickhouse | postgres | duckdb` (the unknown-command
  hint updated accordingly).
- A small per-type connect form: CH/PG = name/host/port/username/password (with
  sensible per-type port defaults — CH 8123, PG 5432); DuckDB = name + path.
- All `fetch('/api/clickhouse/…')` calls repointed to `/api/db/…`; `connect`/
  `test` include `type`.
- Database picker renders only when `databases` is non-empty (hidden for
  DuckDB). `connectionType` already flows to predefined-queries-by-type and the
  query panel unchanged.

## Dependencies & CI

- Add `asyncpg` and `duckdb` to `[project.dependencies]`.
- CI (`.github/workflows/ci.yml`): add a `postgres:16` service container with a
  `pg_isready` healthcheck alongside the existing ClickHouse service; seed a
  tiny database for e2e. DuckDB needs no service (in-process; use a temp file or
  `:memory:`).
- e2e: extend the suite to connect + query each backend (Postgres via the
  service container; DuckDB against a seeded temp file).

## Testing

Follow the project's e2e-first Playwright style (live backend), plus
driver-level unit tests per backend, written TDD:

- `parse_config` validation (required fields, bad ports, DuckDB path).
- Row serialization → TSV/CSV-with-names (incl. empty result, NULLs).
- Pagination wrapper + identifier quoting (alias present for PG/DuckDB).
- `describe_query` column name/type extraction.
- Registry conformance: every entry in `DRIVERS` satisfies `Driver`.
- e2e: each driver connects, (picks a db where applicable), runs a query whose
  rows render in the table, and downloads CSV.

## Out of scope

- Non-`SELECT` / DDL execution semantics beyond what ClickHouse supports today.
- Connection pooling (short-lived connections per request, matching the current
  ClickHouse-over-HTTP model). Can be revisited if it becomes a bottleneck.
- Postgres schema browsing beyond the database picker (schemas are reachable by
  schema-qualifying in SQL).
</content>
</invoke>
