# Alembic migrations in the app lifecycle — design

**Date:** 2026-06-03
**Status:** Approved, ready for implementation plan

## Problem

Schema is created today by `SQLModel.metadata.create_all` inside
`connect.py:_ensure_schema()` (called lazily before every DB operation, 12
sites across `connect.py`, `queries.py`, `dashboards.py`). `create_all` only
creates *missing* tables — it cannot evolve an existing `queryview.db`
(add/rename/drop a column is silently ignored), so the first schema change
after a DB exists in the field produces runtime errors. We need real,
versioned migrations.

## Decision summary

- Adopt **Alembic** as the single source of truth for schema; remove the
  `create_all` DDL path.
- Run `alembic upgrade head` **at application lifecycle start** (the FastAPI
  `lifespan` handler), before any request is served.
- **Single-process deployment by design.** SQLite is single-writer, so the app
  runs as one process; there is no multi-worker migration race to guard
  against. No file lock, no standalone migrate command.
- Migrations also run via the existing lazy `_ensure_schema()` calls (now
  backed by Alembic), which keeps the current test style working (see Tests).

## Scope boundary

In scope: introducing Alembic, an initial revision matching today's schema,
wiring `upgrade head` into the lifecycle, keeping tests green.

Out of scope (YAGNI): auto-generate-on-startup, multi-process/multi-worker
migration locking, a standalone `queryview-migrate` console script, downgrade
tooling beyond what Alembic gives for free.

## Current state (facts that shape the design)

- **Three tables**, defined across three modules, all registered in
  `SQLModel.metadata`:
  - `Connection` → `connections` (`connect.py`)
  - predefined-queries model (with a `UniqueConstraint`) (`queries.py`)
  - dashboards model (`dashboards.py`)
  `create_all` works today only because all three modules get imported.
- **Async runtime engine.** `connect.py` builds an `aiosqlite` async engine
  (`sqlite+aiosqlite:///{DB_PATH}`), memoized lazily.
- **`_ensure_schema()` is called at 12 sites**, awaited before each DB op, and
  memoized by a module-level `_schema_ready` flag.
- **Launch paths** (all single-process today):
  - Dev: `uvicorn queryview.main:app --reload` (`npm run backend`) — runs
    lifespan.
  - Prod/e2e: `queryview-backend` → `run()` → `uvicorn.run(app)` — runs
    lifespan.
  - Tests: `client = TestClient(app)` **without** a `with` block — lifespan
    does **not** run. Schema is provided to tests by the lazy
    `_ensure_schema()` calls plus the `conftest.py` reset of `_schema_ready`.

## Architecture

### Migration runner (`_ensure_schema()`)

Repurpose the existing function rather than removing the 12 call sites:

- Body changes from `create_all` to: build an Alembic `Config`
  programmatically (pointing at the packaged `migrations/` dir, with the
  `DB_PATH`-derived **sync** URL injected), then `command.upgrade(cfg, "head")`.
- Run the upgrade **synchronously, inline** — no `asyncio.to_thread`. It is a
  startup step; blocking is correct (guarantees schema is ready before the
  first request, and the only other caller path is tests, where blocking is
  irrelevant).
- Keep the `_schema_ready` memo so it runs at most once per process; the 12
  lazy call sites become cheap no-ops after the first call.

`_ensure_schema()` stays `async def` for call-site compatibility even though
its body is synchronous.

### Lifecycle wiring (`main.py`)

`lifespan` awaits `_ensure_schema()` at startup, before the
`mcp.session_manager.run()` yield. This covers the dev and prod launch paths
because the lifespan is attached to the app object (no launch-command changes).
Considered and rejected: a FastAPI **factory** (`create_app()` +
`factory=True`) — it runs per-worker just like lifespan (no multi-process
benefit), still wouldn't fire for the no-`with` `TestClient` tests, and would
force a refactor of the module-level `app`/routes plus every launch command. It
buys nothing here.

### Alembic environment (the two SQLite/async sharp edges)

- **Sync, not async, for migrations.** The runtime keeps its `aiosqlite` async
  engine. Migrations use a separate, throwaway **sync** engine
  (`sqlite:///{DB_PATH}`) via Alembic's normal `command.upgrade`. This sidesteps
  async-greenlet complexity entirely.
- **Batch mode on.** `render_as_batch=True` in `env.py` so future column
  changes survive SQLite's limited `ALTER TABLE`.
- **Config built in code, not from cwd.** Migrations live *inside* the package
  at `backend/queryview/migrations/` so they ship in the hatchling wheel
  (`packages = ["backend/queryview"]`) and run in prod regardless of working
  directory. The app builds the `Config` programmatically and points
  `script_location` at that dir; it does not read a cwd `alembic.ini` at
  runtime.
- **`env.py` imports `queryview.connect`, `queryview.queries`,
  `queryview.dashboards`** and sets `target_metadata = SQLModel.metadata` so
  autogenerate sees all three tables (otherwise it would try to drop the
  unseen ones).

## Components / files

**New dependency** (`pyproject.toml`): `alembic>=1.13`.

**New files:**
- `backend/queryview/migrations/env.py`
- `backend/queryview/migrations/script.py.mako` (standard template)
- `backend/queryview/migrations/versions/<rev>_initial_schema.py` —
  hand-verified initial revision matching the current 3-table schema so fresh
  installs land on it cleanly. Pre-Alembic DBs (created by the old `create_all`,
  with no `alembic_version` table) are **not** supported — `upgrade head` would
  fail trying to re-create existing tables; such a DB must be deleted and
  recreated. This is acceptable: there are no deployments to carry forward.
- `backend/alembic.ini` — **dev-only**, for authoring revisions
  (`alembic revision --autogenerate`). Not read at runtime.

**Changed files:**
- `backend/queryview/connect.py` — `_ensure_schema()` body
  (`create_all` → sync, lock-free `command.upgrade(head)`), gated by
  `_schema_ready`.
- `backend/queryview/main.py` — `lifespan` awaits `_ensure_schema()`.
- `docs/future.md` — replace the migration-plan note with "done; single-process
  by design."
- A doc (e.g. `docs/connect.md`) — note the backend is single-process (SQLite
  single-writer) and migrations run once at lifecycle start.

**Unchanged by design:** the 12 lazy `_ensure_schema()` call sites and the
`conftest.py` reset of `_schema_ready` — both load-bearing for the no-`with`
`TestClient` tests.

## Data flow

1. Process starts → first DB touch or lifespan → `_ensure_schema()`.
2. If `_schema_ready` is False: build sync Alembic `Config`, `upgrade head`,
   set `_schema_ready = True`.
3. Subsequent calls return immediately.
4. Runtime DB ops use the unchanged async `aiosqlite` engine/session.

## Error handling

- A failing migration raises out of `command.upgrade`. At lifespan startup this
  aborts boot (correct — do not serve on a bad schema). In tests it fails the
  test that first touches the DB (visible, correct).
- `upgrade head` when already at head is a safe no-op (idempotent), so repeated
  process starts on an up-to-date DB cost nothing.

## Testing

- Existing backend tests must pass unchanged — they now exercise
  `upgrade head` instead of `create_all`.
- Add one test: a fresh `test.db` after `_ensure_schema()` contains all three
  tables (`connections`, predefined-queries, dashboards) and `alembic_version`
  is stamped at head.

## Migration authoring (dev workflow)

New schema changes: edit the SQLModel models, then from `backend/`:
`alembic revision --autogenerate -m "..."`, review the generated script
(confirm batch ops for column changes), commit. Runtime applies it on next
start.
