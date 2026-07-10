# Workspaces (Per-Workspace Git Sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Queries and dashboards belong to a named workspace, and each workspace git-syncs to its own remote (spec: `docs/superpowers/specs/2026-07-10-workspaces-design.md`).

**Architecture:** A `workspaces` DB table (name, AES-GCM-encrypted remote URL, branch) becomes a full ownership dimension: `predefined_queries`/`dashboards` gain a `workspace_id`, name uniqueness becomes per-workspace, and `gitsync.py` takes a resolved workspace record (per-workspace clone + lock) instead of reading env vars. API endpoints take an optional `workspace` name defaulting to `"default"`; MCP stays workspace-unaware and resolves the workspace from an armed browser session's reported state.

**Tech Stack:** FastAPI + SQLModel/SQLite + Alembic (backend), FastMCP (MCP), React/Vite/vitest (frontend), pytest + pytest-playwright (tests).

## Global Constraints

- Backend tests: `uv run pytest backend/tests` (pyproject's `testpaths` is `e2e`, so the path is required). Frontend tests: `npm test -w frontend`; lint: `npm run lint -w frontend`; build: `npm run build`.
- Use `uv run ...`, never `python -m` or an activated venv (CLAUDE.md).
- No `__all__` declarations; no re-export lists in `__init__.py` (CLAUDE.md).
- No `Co-Authored-By: Claude` (or similar AI-attribution) trailers in commits (CLAUDE.md).
- Branch: all work on `claude/future-md-brainstorm-hbfexb`; push with `git push -u origin claude/future-md-brainstorm-hbfexb`.
- The concept is named **workspace** everywhere (never "project"). The default workspace's name is the string `"default"`.
- The workspace remote URL is a secret (may embed a token): it is encrypted at rest with `connect.py`'s `_encrypt_str`/`_decrypt_str` and must never appear in any API/MCP response.
- Existing clients keep working: every changed endpoint/tool treats an omitted `workspace`/`session_id` as the default workspace.

## File Structure

- `backend/queryview/migrations/versions/c7d8e9f0a1b2_workspaces.py` — new migration (Task 1).
- `backend/queryview/workspaces.py` — new module: `Workspace` model, `WorkspaceRec`, `WorkspaceError`, CRUD + `resolve` (Task 2).
- `backend/queryview/queries.py`, `dashboards.py` — `workspace_id` scoping (Tasks 3–4).
- `backend/queryview/gitsync.py` — operations take a `WorkspaceRec`; per-workspace clone dir + lock (Task 5).
- `backend/queryview/main.py` — `workspace` param on scoped endpoints; `/api/workspaces` CRUD (Tasks 3–6).
- `backend/queryview/remote.py`, `mcp_server.py` — session-reported workspace; `session_id` on entity/git tools (Task 7).
- `frontend/src/workspace.ts` (new), `gitsync.ts`, `WorkspaceSwitcher.tsx` (new), `App.tsx`, `QueryView.tsx`, `DashboardView.tsx`, `GitSyncControls.tsx` — active-workspace state, switcher UI, param threading (Tasks 8–9).
- `e2e/test_workspaces.py` — new e2e (Task 10).
- `docs/workspace.md` (new), `docs/gitsync.md`, `docs/future.md`, `docs/remote.md`, `docs/api.md` — docs (Task 11).

---

### Task 1: Workspaces migration (table, seed, entity columns, constraints)

**Files:**
- Create: `backend/queryview/migrations/versions/c7d8e9f0a1b2_workspaces.py`
- Modify: `backend/tests/conftest.py` (scrub ambient `GIT_SYNC_*` env at session start)
- Test: `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `queryview.connect._encrypt_str` (existing), Alembic head `b2c3d4e5f6a7`.
- Produces: DB schema used by all later tasks — table `workspaces(id, name UNIQUE, remote NULL, branch)`; `predefined_queries.workspace_id` + unique `(workspace_id, type, query_name)` named `uq_predefined_ws_type_name`; `dashboards.workspace_id` + unique `(workspace_id, name)` named `uq_dashboards_ws_name`; `ix_dashboards_name` becomes non-unique. A seeded row `name='default'` whose `remote` is `_encrypt_str(GIT_SYNC_REMOTE)` (or NULL) and `branch` is `GIT_SYNC_BRANCH` or `'main'`.
- Note: `workspace_id` gets a `server_default` of the default workspace's id so code that doesn't yet pass it (Tasks 1–2 interim) keeps inserting successfully; by Task 4 every writer passes it explicitly.

- [ ] **Step 1: Scrub ambient git-sync env in the test session fixture**

The migration reads `GIT_SYNC_REMOTE`/`GIT_SYNC_BRANCH` at upgrade time; the session DB migrates on first touch, so a developer's real env must not leak into the seeded default workspace. In `backend/tests/conftest.py`, inside `_isolated_db` right after the `DB_KEY_PATH` line, add:

```python
    # The workspaces migration seeds the default workspace from GIT_SYNC_*;
    # tests control that per-test (monkeypatch), never from ambient env.
    os.environ.pop("GIT_SYNC_REMOTE", None)
    os.environ.pop("GIT_SYNC_BRANCH", None)
```

- [ ] **Step 2: Write the failing migration test**

Append to `backend/tests/test_migrations.py`. The test runs on its **own fresh
DB** (not the shared session DB): once later tasks let tests create same-named
entities in *different* workspaces, downgrading the shared DB past this
revision would collide on the restored global-unique constraints and make the
suite order-dependent. `monkeypatch.setattr` on the lazy globals auto-restores
the shared engine afterwards.

```python
def test_workspaces_migration_seeds_default_and_backfills(tmp_path, monkeypatch):
    """Upgrading from the pre-workspace schema creates the workspaces table,
    seeds 'default' from GIT_SYNC_REMOTE/GIT_SYNC_BRANCH (remote encrypted),
    and backfills existing entity rows into it. Runs on a private DB so the
    shared session DB is never downgraded across the workspace boundary."""
    from alembic import command

    import queryview.connect as _c
    from queryview.connect import _alembic_config, _db_path, _decrypt_str

    monkeypatch.setenv("DB_PATH", str(tmp_path / "mig.db"))
    monkeypatch.setenv("DB_KEY_PATH", str(tmp_path / "mig.db.key"))
    monkeypatch.setattr(_c, "_engine", None)
    monkeypatch.setattr(_c, "_schema_ready", False)
    monkeypatch.setattr(_c, "_key", None)

    cfg = _alembic_config()
    command.upgrade(cfg, "b2c3d4e5f6a7")  # build the pre-workspace schema fresh

    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            "INSERT INTO predefined_queries (query_name, type, query) "
            "VALUES ('pre ws', 'clickhouse', 'SELECT 1')"
        )
        con.commit()
    finally:
        con.close()

    monkeypatch.setenv("GIT_SYNC_REMOTE", "https://example.test/repo.git")
    monkeypatch.setenv("GIT_SYNC_BRANCH", "trunk")
    command.upgrade(cfg, "head")

    con = sqlite3.connect(_db_path())
    try:
        ws = con.execute("SELECT id, name, remote, branch FROM workspaces").fetchall()
        assert len(ws) == 1
        wid, name, remote, branch = ws[0]
        assert name == "default" and branch == "trunk"
        assert _decrypt_str(remote) == "https://example.test/repo.git"
        backfilled = con.execute(
            "SELECT workspace_id FROM predefined_queries WHERE query_name='pre ws'"
        ).fetchone()[0]
        assert backfilled == wid
    finally:
        con.close()
```

Also update the head-check test: in `test_fresh_db_is_migrated_to_head`, add `"workspaces"` to the expected-tables set.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest backend/tests/test_migrations.py -v`
Expected: FAIL — `workspaces` table missing / downgrade target has no path issue resolved only by the new revision.

- [ ] **Step 4: Write the migration**

Create `backend/queryview/migrations/versions/c7d8e9f0a1b2_workspaces.py`:

```python
"""workspaces: per-workspace git sync — workspaces table, entity workspace_id

Seeds a 'default' workspace from GIT_SYNC_REMOTE/GIT_SYNC_BRANCH (read once
here; runtime config lives in the table from now on) and backfills all
existing predefined queries and dashboards into it. Name uniqueness becomes
per-workspace.

Revision ID: c7d8e9f0a1b2
Revises: b2c3d4e5f6a7
Create Date: 2026-07-10

"""
from __future__ import annotations

import os
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("remote", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("branch", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_workspaces_name"), "workspaces", ["name"], unique=True)

    # Seed 'default' from the legacy env config. The app's key loader is
    # imported here, mirroring the connection-config-blob migration.
    from queryview.connect import _encrypt_str

    env_remote = os.environ.get("GIT_SYNC_REMOTE")
    remote = _encrypt_str(env_remote) if env_remote else None
    branch = os.environ.get("GIT_SYNC_BRANCH") or "main"
    conn = op.get_bind()
    conn.execute(
        sa.text("INSERT INTO workspaces (name, remote, branch) VALUES (:n, :r, :b)"),
        {"n": "default", "r": remote, "b": branch},
    )
    default_id = conn.execute(
        sa.text("SELECT id FROM workspaces WHERE name = 'default'")
    ).scalar()

    # server_default backfills existing rows during the batch table rewrite and
    # keeps pre-workspace INSERT paths working mid-upgrade; application code
    # always passes workspace_id explicitly.
    with op.batch_alter_table("predefined_queries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "workspace_id",
                sa.Integer(),
                nullable=False,
                server_default=str(default_id),
            )
        )
        batch_op.drop_constraint("uq_predefined_type_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_predefined_ws_type_name", ["workspace_id", "type", "query_name"]
        )

    with op.batch_alter_table("dashboards", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "workspace_id",
                sa.Integer(),
                nullable=False,
                server_default=str(default_id),
            )
        )
        batch_op.create_unique_constraint("uq_dashboards_ws_name", ["workspace_id", "name"])

    # Dashboard names are now unique per workspace, not globally.
    op.drop_index("ix_dashboards_name", table_name="dashboards")
    op.create_index("ix_dashboards_name", "dashboards", ["name"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("dashboards", schema=None) as batch_op:
        batch_op.drop_constraint("uq_dashboards_ws_name", type_="unique")
        batch_op.drop_column("workspace_id")
    op.drop_index("ix_dashboards_name", table_name="dashboards")
    op.create_index("ix_dashboards_name", "dashboards", ["name"], unique=True)

    with op.batch_alter_table("predefined_queries", schema=None) as batch_op:
        batch_op.drop_constraint("uq_predefined_ws_type_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_predefined_type_name", ["type", "query_name"]
        )
        batch_op.drop_column("workspace_id")

    op.drop_index(op.f("ix_workspaces_name"), table_name="workspaces")
    op.drop_table("workspaces")
```

- [ ] **Step 5: Run the migration tests**

Run: `uv run pytest backend/tests/test_migrations.py -v`
Expected: PASS (all, including the two pre-existing tests).

- [ ] **Step 6: Run the whole backend suite**

Run: `uv run pytest backend/tests`
Expected: PASS — existing code doesn't reference `workspace_id`, and `server_default` covers its inserts.

- [ ] **Step 7: Commit**

```bash
git add backend/queryview/migrations/versions/c7d8e9f0a1b2_workspaces.py backend/tests/conftest.py backend/tests/test_migrations.py
git commit -m "workspaces: migration — table, default seed from env, entity workspace_id"
```

---

### Task 2: Workspace store (`workspaces.py`)

**Files:**
- Create: `backend/queryview/workspaces.py`
- Test: `backend/tests/test_workspaces.py` (new)

**Interfaces:**
- Consumes: `connect._encrypt_str`, `connect._decrypt_str`, `connect._engine_for_db`, `connect._ensure_schema`; the Task 1 schema.
- Produces (used by every later backend task):
  - `DEFAULT_WORKSPACE = "default"`
  - `class WorkspaceError(Exception)` with `.status: int` (400/404/409), mirroring `GitSyncError`.
  - `@dataclass WorkspaceRec: id: int; name: str; remote: str | None (decrypted); branch: str`
  - `async resolve(name: str) -> WorkspaceRec` — 404 for unknown.
  - `async list_workspaces() -> list[dict]` — `[{name, branch, configured}]`, never the remote.
  - `async create_workspace(name: str, remote: str | None = None, branch: str = "main") -> None` — 400 empty/`"/"`-containing name, 409 duplicate.
  - `async update_workspace(name: str, new_name: str | None = None, remote: Any = _UNSET, branch: str | None = None) -> None` — `remote` sentinel: omitted = keep, `None` = clear, str = set.
  - `async delete_workspace(name: str) -> None` — 404 unknown, 409 non-empty.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_workspaces.py`:

```python
"""Workspace store: CRUD, name rules, encrypted-remote round trip, and the
delete-refuses-on-non-empty rule. The 'default' workspace is seeded by the
migration and is an ordinary row."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from queryview.connect import _db_path
from queryview.workspaces import (
    DEFAULT_WORKSPACE,
    WorkspaceError,
    create_workspace,
    delete_workspace,
    list_workspaces,
    resolve,
    update_workspace,
)


def _run(coro):
    return asyncio.run(coro)


def test_default_workspace_is_seeded():
    rec = _run(resolve(DEFAULT_WORKSPACE))
    assert rec.name == "default"
    assert isinstance(rec.id, int)


def test_resolve_unknown_is_404():
    with pytest.raises(WorkspaceError) as e:
        _run(resolve("no-such-ws"))
    assert e.value.status == 404


def test_create_list_and_encrypted_remote_round_trip():
    _run(create_workspace("t2-crt", remote="https://u:tok@example.test/r.git", branch="dev"))
    rec = _run(resolve("t2-crt"))
    assert rec.remote == "https://u:tok@example.test/r.git"  # decrypted for gitsync
    assert rec.branch == "dev"

    listed = {w["name"]: w for w in _run(list_workspaces())}
    assert listed["t2-crt"] == {"name": "t2-crt", "branch": "dev", "configured": True}
    assert "remote" not in listed["t2-crt"]  # the URL (a secret) never leaves the store

    # At rest the remote is ciphertext, not the URL.
    con = sqlite3.connect(_db_path())
    try:
        stored = con.execute(
            "SELECT remote FROM workspaces WHERE name='t2-crt'"
        ).fetchone()[0]
    finally:
        con.close()
    assert "example.test" not in stored


def test_create_duplicate_is_409_and_bad_names_400():
    _run(create_workspace("t2-dup"))
    with pytest.raises(WorkspaceError) as e:
        _run(create_workspace("t2-dup"))
    assert e.value.status == 409
    with pytest.raises(WorkspaceError) as e:
        _run(create_workspace("   "))
    assert e.value.status == 400
    with pytest.raises(WorkspaceError) as e:
        _run(create_workspace("a/b"))  # names travel in URL paths
    assert e.value.status == 400


def test_update_rename_set_and_clear_remote():
    _run(create_workspace("t2-upd", remote="https://example.test/a.git"))
    _run(update_workspace("t2-upd", new_name="t2-upd2", branch="rel"))
    rec = _run(resolve("t2-upd2"))
    assert rec.branch == "rel" and rec.remote == "https://example.test/a.git"  # remote untouched
    _run(update_workspace("t2-upd2", remote=None))
    assert _run(resolve("t2-upd2")).remote is None
    with pytest.raises(WorkspaceError) as e:
        _run(update_workspace("t2-upd2", new_name=DEFAULT_WORKSPACE))
    assert e.value.status == 409


def test_delete_refuses_non_empty_then_deletes_empty():
    _run(create_workspace("t2-del"))
    rec = _run(resolve("t2-del"))
    con = sqlite3.connect(_db_path())
    try:
        con.execute(
            "INSERT INTO predefined_queries (query_name, type, query, workspace_id) "
            "VALUES ('held', 'clickhouse', 'SELECT 1', ?)",
            (rec.id,),
        )
        con.commit()
    finally:
        con.close()
    with pytest.raises(WorkspaceError) as e:
        _run(delete_workspace("t2-del"))
    assert e.value.status == 409

    con = sqlite3.connect(_db_path())
    try:
        con.execute("DELETE FROM predefined_queries WHERE workspace_id=?", (rec.id,))
        con.commit()
    finally:
        con.close()
    _run(delete_workspace("t2-del"))
    with pytest.raises(WorkspaceError):
        _run(resolve("t2-del"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest backend/tests/test_workspaces.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'queryview.workspaces'`.

- [ ] **Step 3: Implement `workspaces.py`**

Create `backend/queryview/workspaces.py`:

```python
"""Workspace domain: named git-sync targets that own predefined queries and
dashboards. The remote URL may embed a token, so it is encrypted at rest with
connect.py's AES-GCM helpers and never returned by list_workspaces. Docs:
docs/workspace.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from .connect import _decrypt_str, _encrypt_str, _engine_for_db, _ensure_schema

DEFAULT_WORKSPACE = "default"


class WorkspaceError(Exception):
    """Workspace failure carrying an HTTP-ish status for the API layer:
    400 invalid input, 404 unknown workspace, 409 conflict (duplicate name,
    delete of a non-empty workspace)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Workspace(SQLModel, table=True):
    __tablename__ = "workspaces"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    remote: str | None = Field(default=None)  # base64(AES-GCM(url)) — never plaintext
    branch: str = Field(default="main")


@dataclass
class WorkspaceRec:
    """A resolved workspace with its remote decrypted, ready for gitsync."""

    id: int
    name: str
    remote: str | None
    branch: str


def _to_rec(row: Workspace) -> WorkspaceRec:
    return WorkspaceRec(
        id=row.id,  # type: ignore[arg-type]  # persisted rows always have an id
        name=row.name,
        remote=_decrypt_str(row.remote) if row.remote else None,
        branch=row.branch,
    )


def _valid_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise WorkspaceError("workspace name is required", status=400)
    if "/" in name:
        # Names travel in /api/workspaces/{name} URL paths.
        raise WorkspaceError("workspace name must not contain '/'", status=400)
    return name


async def resolve(name: str) -> WorkspaceRec:
    """The named workspace, or 404. Every workspace-scoped operation starts here."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Workspace).where(Workspace.name == name))).first()
    if row is None:
        raise WorkspaceError(f"unknown workspace {name!r}", status=404)
    return _to_rec(row)


async def list_workspaces() -> list[dict[str, Any]]:
    """All workspaces ordered by name; exposes whether a remote is configured
    but never the remote itself."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        rows = (await s.exec(select(Workspace).order_by(Workspace.name))).all()
    return [
        {"name": r.name, "branch": r.branch, "configured": bool(r.remote)}
        for r in rows
    ]


async def create_workspace(
    name: str, remote: str | None = None, branch: str = "main"
) -> None:
    name = _valid_name(name)
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        dup = (await s.exec(select(Workspace).where(Workspace.name == name))).first()
        if dup is not None:
            raise WorkspaceError(f"workspace {name!r} already exists", status=409)
        s.add(
            Workspace(
                name=name,
                remote=_encrypt_str(remote) if remote else None,
                branch=branch.strip() or "main",
            )
        )
        await s.commit()


# Sentinel distinguishing "leave the remote as-is" (omitted) from "clear it"
# (an explicit None).
_UNSET: Any = object()


async def update_workspace(
    name: str,
    new_name: str | None = None,
    remote: Any = _UNSET,
    branch: str | None = None,
) -> None:
    """Rename and/or reconfigure a workspace. Renaming is a one-row update:
    entities and clone dirs are keyed by workspace id, so nothing else moves."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Workspace).where(Workspace.name == name))).first()
        if row is None:
            raise WorkspaceError(f"unknown workspace {name!r}", status=404)
        if new_name is not None and new_name.strip() != name:
            new_name = _valid_name(new_name)
            dup = (await s.exec(select(Workspace).where(Workspace.name == new_name))).first()
            if dup is not None:
                raise WorkspaceError(f"workspace {new_name!r} already exists", status=409)
            row.name = new_name
        if remote is not _UNSET:
            row.remote = _encrypt_str(remote) if remote else None
        if branch is not None and branch.strip():
            row.branch = branch.strip()
        s.add(row)
        await s.commit()


async def _entity_count(workspace_id: int) -> int:
    """How many entities the workspace still owns. Raw SQL so this module
    doesn't import the entity modules (which import nothing from here either)."""
    async with _engine_for_db().connect() as conn:
        n = (
            await conn.execute(
                text(
                    "SELECT (SELECT COUNT(*) FROM predefined_queries WHERE workspace_id = :w)"
                    " + (SELECT COUNT(*) FROM dashboards WHERE workspace_id = :w)"
                ),
                {"w": workspace_id},
            )
        ).scalar()
    return int(n or 0)


async def delete_workspace(name: str) -> None:
    """Delete an empty workspace; 409 while it still owns entities. The git
    remote keeps its history either way — this only removes the local row."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Workspace).where(Workspace.name == name))).first()
        if row is None:
            raise WorkspaceError(f"unknown workspace {name!r}", status=404)
        count = await _entity_count(row.id)  # type: ignore[arg-type]
        if count:
            raise WorkspaceError(
                f"workspace {name!r} still contains {count} entities; delete them first",
                status=409,
            )
        await s.delete(row)
        await s.commit()
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest backend/tests/test_workspaces.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole backend suite and commit**

Run: `uv run pytest backend/tests` — expected PASS.

```bash
git add backend/queryview/workspaces.py backend/tests/test_workspaces.py
git commit -m "workspaces: store module — CRUD, resolve, encrypted remote"
```

---

### Task 3: Scope predefined queries by workspace

**Files:**
- Modify: `backend/queryview/queries.py`, `backend/queryview/main.py` (predefined-queries endpoints + `_resolve_workspace` helper), `backend/queryview/gitsync.py` (`_load_entity` + `restore`'s save — interim default resolution), `backend/queryview/mcp_server.py` (`list_queries` — interim default resolution)
- Test: `backend/tests/test_queries.py` (update call sites, add isolation test), `backend/tests/conftest.py` (add `default_ws_id` fixture)

**Interfaces:**
- Consumes: `workspaces.resolve`, `workspaces.DEFAULT_WORKSPACE`, `workspaces.WorkspaceError` (Task 2).
- Produces:
  - `list_predefined_queries(conn_type: str, workspace_id: int)`
  - `get_predefined_query(conn_type: str, query_name: str, workspace_id: int)`
  - `list_predefined_queries_view(conn_type: str, workspace_id: int)`
  - `save_predefined_query(query_name, conn_type, query, cell_view=None, order_by=None, fields=None, *, workspace_id: int)`
  - `main._resolve_workspace(raw) -> tuple[WorkspaceRec | None, JSONResponse | None]` — shared by Tasks 4–6.
  - conftest fixture `default_ws_id` returning the default workspace's id.

- [ ] **Step 1: Add the shared test fixture**

Append to `backend/tests/conftest.py`:

```python
@pytest.fixture
def default_ws_id() -> int:
    """The seeded default workspace's id, for store-level calls in tests."""
    import asyncio

    from queryview.workspaces import DEFAULT_WORKSPACE, resolve

    return asyncio.run(resolve(DEFAULT_WORKSPACE)).id
```

- [ ] **Step 2: Write the failing isolation test**

Append to `backend/tests/test_queries.py`:

```python
def test_same_name_is_distinct_per_workspace(default_ws_id):
    from queryview.workspaces import create_workspace, resolve

    _run(create_workspace("t3-iso"))
    other = _run(resolve("t3-iso")).id
    _run(save_predefined_query("iso q", "clickhouse", "SELECT 1", workspace_id=default_ws_id))
    _run(save_predefined_query("iso q", "clickhouse", "SELECT 2", workspace_id=other))

    assert _run(get_predefined_query("clickhouse", "iso q", default_ws_id))["query"] == "SELECT 1"
    assert _run(get_predefined_query("clickhouse", "iso q", other))["query"] == "SELECT 2"
    names_default = [r["query_name"] for r in _run(list_predefined_queries("clickhouse", default_ws_id))]
    names_other = [r["query_name"] for r in _run(list_predefined_queries("clickhouse", other))]
    assert "iso q" in names_default and "iso q" in names_other
```

(Adjust imports at the top of the file to match its existing style; the test file already imports the store functions.)

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest backend/tests/test_queries.py -v`
Expected: FAIL — `save_predefined_query() got an unexpected keyword argument 'workspace_id'`.

- [ ] **Step 4: Implement the scoping**

In `backend/queryview/queries.py`:

1. Model — replace the `__table_args__` and add the column:

```python
class PredefinedQuery(SQLModel, table=True):
    __tablename__ = "predefined_queries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "type", "query_name", name="uq_predefined_ws_type_name"
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    query_name: str = Field(index=True)
    type: str = Field(index=True)
    workspace_id: int  # owning workspace (workspaces.id); names unique per workspace
    query: str
```

(keep the remaining fields exactly as they are).

2. Every function gains `workspace_id: int` and filters on it. The `where` clauses become e.g.:

```python
async def list_predefined_queries(
    conn_type: str, workspace_id: int
) -> list[dict[str, str | None]]:
    """Saved queries for a connection type within one workspace, ordered by name."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        rows = (
            await s.exec(
                select(PredefinedQuery)
                .where(
                    PredefinedQuery.type == conn_type,
                    PredefinedQuery.workspace_id == workspace_id,
                )
                .order_by(PredefinedQuery.query_name)
            )
        ).all()
```

`get_predefined_query(conn_type, query_name, workspace_id)` adds `PredefinedQuery.workspace_id == workspace_id` to its `where`; `list_predefined_queries_view(conn_type, workspace_id)` passes it through; `save_predefined_query` gains keyword-only `*, workspace_id: int`, adds the same `where` filter to its select, and sets `workspace_id=workspace_id` when constructing a new row. Update the module docstring's first line to `"""Predefined query store: reusable SQL keyed by connection type within a workspace."""`.

3. In `backend/queryview/main.py`, add near `_gitsync_args` (and `from . import gitsync, remote, workspaces` at the top):

```python
async def _resolve_workspace(raw: Any):
    """(WorkspaceRec, None) or (None, JSONResponse) for a request's optional
    `workspace` field; empty/missing means the default workspace."""
    name = _clean_str(raw) or workspaces.DEFAULT_WORKSPACE
    try:
        return await workspaces.resolve(name), None
    except workspaces.WorkspaceError as e:
        return None, JSONResponse({"ok": False, "message": str(e)}, status_code=e.status)
```

Update the two predefined-queries endpoints:

```python
@app.get("/api/predefined-queries")
async def predefined_queries_list(request: Request):
    conn_type = request.query_params.get("type") or "clickhouse"
    ws, err = await _resolve_workspace(request.query_params.get("workspace"))
    if err:
        return err
    return {"queries": await list_predefined_queries_view(conn_type, ws.id)}
```

and in `predefined_queries_save`, after the existing field validation:

```python
    ws, err = await _resolve_workspace(b.get("workspace"))
    if err:
        return err
```

then `await save_predefined_query(name, conn_type, query, cell_view, order_by, fields, workspace_id=ws.id)`.

4. Interim callers (replaced in Tasks 5 and 7 — keep them compiling and green now). In `gitsync.py`, `_load_entity`'s query branch becomes:

```python
    if kind == "query":
        from .queries import get_predefined_query
        from .workspaces import DEFAULT_WORKSPACE, resolve as resolve_workspace

        ws = await resolve_workspace(DEFAULT_WORKSPACE)  # interim until Task 5
        row = await get_predefined_query(conn_type or "", name, ws.id)
```

and `restore()`'s query upsert:

```python
        from .queries import save_predefined_query
        from .workspaces import DEFAULT_WORKSPACE, resolve as resolve_workspace

        ws = await resolve_workspace(DEFAULT_WORKSPACE)  # interim until Task 5
        await save_predefined_query(
            data["query_name"],
            conn_type or "",
            data["query"],
            data["cell_view"],
            data["order_by"],
            data["fields"],
            workspace_id=ws.id,
        )
```

In `mcp_server.py`, `list_queries` body becomes:

```python
    from .workspaces import DEFAULT_WORKSPACE, resolve

    ws = await resolve(DEFAULT_WORKSPACE)  # interim until Task 7
    return {"queries": await list_predefined_queries_view(conn_type, ws.id)}
```

5. Update every pre-existing call in `backend/tests/test_queries.py` (and any other backend test calling these functions — `grep -rn "save_predefined_query\|get_predefined_query\|list_predefined_queries" backend/tests`) to pass the `default_ws_id` fixture value: add the fixture to the test's parameters and pass `workspace_id=default_ws_id` (or positional `default_ws_id` for get/list).

- [ ] **Step 5: Run and commit**

Run: `uv run pytest backend/tests` — expected PASS.

Add an API-level check to `backend/tests/test_queries.py`:

```python
def test_api_unknown_workspace_is_404():
    from fastapi.testclient import TestClient

    from queryview.main import app

    c = TestClient(app)
    r = c.get("/api/predefined-queries", params={"workspace": "nope-t3"})
    assert r.status_code == 404
```

Run: `uv run pytest backend/tests/test_queries.py -v` — expected PASS.

```bash
git add backend/queryview/queries.py backend/queryview/main.py backend/queryview/gitsync.py backend/queryview/mcp_server.py backend/tests
git commit -m "workspaces: scope predefined queries by workspace"
```

---

### Task 4: Scope dashboards by workspace

**Files:**
- Modify: `backend/queryview/dashboards.py`, `backend/queryview/main.py` (dashboards endpoints), `backend/queryview/gitsync.py` (dashboard branches of `_load_entity`/`restore` — same interim default pattern as Task 3)
- Test: `backend/tests/test_dashboards.py`

**Interfaces:**
- Consumes: Task 2 store, Task 3's `_resolve_workspace` and `default_ws_id` fixture.
- Produces:
  - `upsert_dashboard(name, connection, html, queries, *, workspace_id: int)`
  - `get_dashboard(name: str, workspace_id: int)`
  - `list_dashboards(workspace_id: int)`
  - `_upsert_and_push(name, connection, html, queries, session_id, *, workspace_id: int)`
  - Endpoints: `POST /api/dashboards` accepts optional `workspace` in the body; `GET /api/dashboards` and `GET /api/dashboards/{name}` accept optional `?workspace=`.

- [ ] **Step 1: Write the failing isolation test**

Append to `backend/tests/test_dashboards.py`:

```python
def test_dashboard_names_distinct_per_workspace(default_ws_id):
    from queryview.workspaces import create_workspace, resolve

    _run(create_workspace("t4-iso"))
    other = _run(resolve("t4-iso")).id
    _run(upsert_dashboard("iso d", "prod", "<html>1</html>", {"q": "SELECT 1"}, workspace_id=default_ws_id))
    _run(upsert_dashboard("iso d", "prod", "<html>2</html>", {"q": "SELECT 2"}, workspace_id=other))

    assert _run(get_dashboard("iso d", default_ws_id))["html"] == "<html>1</html>"
    assert _run(get_dashboard("iso d", other))["html"] == "<html>2</html>"
    assert "iso d" in [d["name"] for d in _run(list_dashboards(default_ws_id))]
    assert "iso d" in [d["name"] for d in _run(list_dashboards(other))]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest backend/tests/test_dashboards.py -v`
Expected: FAIL — unexpected keyword argument `workspace_id`.

- [ ] **Step 3: Implement**

In `dashboards.py`: add to the model

```python
class Dashboard(SQLModel, table=True):
    __tablename__ = "dashboards"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_dashboards_ws_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)  # unique per workspace, not globally
    workspace_id: int  # owning workspace (workspaces.id)
```

(add `from sqlalchemy import UniqueConstraint` to the imports; the remaining fields stay). Thread `workspace_id` through `upsert_dashboard` (keyword-only; filter the select by `Dashboard.workspace_id == workspace_id` and set it on new rows), `get_dashboard(name, workspace_id)`, `list_dashboards(workspace_id)`, and `_upsert_and_push(..., *, workspace_id)`. `_push_dashboard` and `_dashboard_event` are draft-only (no persistence) — unchanged.

In `main.py`:

```python
@app.get("/api/dashboards")
async def dashboards_list(request: Request):
    ws, err = await _resolve_workspace(request.query_params.get("workspace"))
    if err:
        return err
    return {"dashboards": await list_dashboards(ws.id)}


@app.get("/api/dashboards/{name}")
async def dashboards_get(name: str, request: Request):
    ws, err = await _resolve_workspace(request.query_params.get("workspace"))
    if err:
        return err
    d = await get_dashboard(name, ws.id)
    if d is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d
```

and in `dashboards_upsert`, resolve `b.get("workspace")` via `_resolve_workspace` and pass `workspace_id=ws.id` to `_upsert_and_push`.

In `gitsync.py`, the dashboard branch of `_load_entity` and `restore`'s `upsert_dashboard` call get the same interim default-workspace resolution as Task 3's query branches (resolve `DEFAULT_WORKSPACE`, pass `ws.id` / `workspace_id=ws.id`).

Update pre-existing calls in `backend/tests/test_dashboards.py` (and any other backend test file — `grep -rn "upsert_dashboard\|get_dashboard\|list_dashboards" backend/tests`) to pass `default_ws_id`.

- [ ] **Step 4: Run and commit**

Run: `uv run pytest backend/tests` — expected PASS.

```bash
git add backend/queryview/dashboards.py backend/queryview/main.py backend/queryview/gitsync.py backend/tests
git commit -m "workspaces: scope dashboards by workspace"
```

---

### Task 5: Per-workspace gitsync

**Files:**
- Modify: `backend/queryview/gitsync.py`, `backend/queryview/main.py` (git endpoints), `backend/queryview/mcp_server.py` (git tools — interim default), `backend/tests/conftest.py` (`git_env` fixture)
- Test: `backend/tests/test_gitsync.py`, `backend/tests/test_api_gitsync.py`, `backend/tests/test_mcp_gitsync.py`

**Interfaces:**
- Consumes: `WorkspaceRec` (Task 2); entity stores (Tasks 3–4).
- Produces (main.py and mcp_server.py call these; the frontend hits the endpoints):
  - `gitsync.configured(ws: WorkspaceRec) -> bool` (= `bool(ws.remote)`)
  - `gitsync.store(ws: WorkspaceRec, kind, name, conn_type=None, message=None)`
  - `gitsync.history(ws: WorkspaceRec, kind, name, conn_type=None, before=None, limit=10)`
  - `gitsync.restore(ws: WorkspaceRec, kind, name, conn_type=None, ref=None)`
  - Clone dir: `{GIT_SYNC_DIR or f"{db_path}.gitsync"}/{ws.id}/`; one lock per `(event loop, ws.id)`.
  - Endpoints: `/api/git/status|store|history|restore` accept optional `workspace`; status returns `{configured}` for that workspace.
  - `GIT_SYNC_REMOTE`/`GIT_SYNC_BRANCH` are no longer read at runtime (seed-only, Task 1). `GIT_SYNC_DIR` remains as the clones' base directory.

- [ ] **Step 1: Rewrite the `git_env` fixture**

In `backend/tests/conftest.py` replace the `git_env` fixture body (runtime config now lives on the default workspace row, not env):

```python
@pytest.fixture
def git_env(tmp_path, monkeypatch):
    """A local bare repo as the default workspace's git-sync remote + a fresh
    per-workspace clone base dir. Resets the default workspace to 'no remote'
    on teardown so unconfigured-state tests stay valid."""
    import asyncio

    from queryview.workspaces import DEFAULT_WORKSPACE, update_workspace

    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("GIT_SYNC_DIR", str(tmp_path / "clones"))
    asyncio.run(update_workspace(DEFAULT_WORKSPACE, remote=str(remote)))
    yield remote
    asyncio.run(update_workspace(DEFAULT_WORKSPACE, remote=None))
```

- [ ] **Step 2: Write the failing isolation test**

Append to `backend/tests/test_gitsync.py`:

```python
def _bare(tmp_path, name):
    remote = tmp_path / name
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    return remote


def test_store_is_isolated_per_workspace(tmp_path, monkeypatch):
    """A commit in workspace A lands only in A's remote; B's stays empty; a
    workspace without a remote is 409."""
    from queryview.queries import save_predefined_query
    from queryview.workspaces import create_workspace, resolve

    monkeypatch.setenv("GIT_SYNC_DIR", str(tmp_path / "clones"))
    ra, rb = _bare(tmp_path, "a.git"), _bare(tmp_path, "b.git")
    _run(create_workspace("t5-a", remote=str(ra)))
    _run(create_workspace("t5-b", remote=str(rb)))
    _run(create_workspace("t5-none"))
    wa, wb = _run(resolve("t5-a")), _run(resolve("t5-b"))

    _run(save_predefined_query("iso", "clickhouse", "SELECT 1", workspace_id=wa.id))
    r = _run(gitsync.store(wa, "query", "iso", "clickhouse"))
    assert r["committed"] is True
    assert "store query clickhouse/iso" in _remote_log(ra)
    # B's remote has no commits at all (git log fails on an empty bare repo).
    p = subprocess.run(
        ["git", "log", "--format=%H", "main"], cwd=rb, capture_output=True, text=True
    )
    assert p.returncode != 0

    # And B's history for the same entity name is empty, not A's history.
    _run(save_predefined_query("iso", "clickhouse", "SELECT 2", workspace_id=wb.id))
    h = _run(gitsync.history(wb, "query", "iso", "clickhouse"))
    assert h["revisions"] == []

    wn = _run(resolve("t5-none"))
    with pytest.raises(GitSyncError) as e:
        _run(gitsync.store(wn, "query", "iso", "clickhouse"))
    assert e.value.status == 409
    assert "no git remote" in str(e.value)
```

Also update the two existing signature-sensitive tests in this file:
- `test_store_unconfigured_is_409` — drop the `monkeypatch` env delete; resolve the default workspace (its remote is None outside `git_env`) and assert `store(ws, ...)` raises 409.
- `test_unknown_kind_is_400` — resolve the default workspace and call `store(ws, "quert", "x", "clickhouse")`.
- Any other test in the file calling `gitsync.store/history/restore` gains a resolved `WorkspaceRec` first argument (default workspace under `git_env`): `ws = _run(resolve(DEFAULT_WORKSPACE))`.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest backend/tests/test_gitsync.py -v`
Expected: FAIL — `store()` takes no `WorkspaceRec` first argument yet.

- [ ] **Step 4: Rewrite gitsync configuration + operations**

In `backend/queryview/gitsync.py`:

1. Module docstring: append `Operations take a resolved workspaces.WorkspaceRec; each workspace has its own clone and lock. Docs: docs/gitsync.md, docs/workspace.md.` Add `from typing import TYPE_CHECKING` guard import:

```python
if TYPE_CHECKING:
    from .workspaces import WorkspaceRec
```

2. Replace the Configuration section (`_remote`, `_branch`, `_workdir`, `configured`) with:

```python
def _require_remote(ws: "WorkspaceRec") -> str:
    if not ws.remote:
        raise GitSyncError(
            f"workspace {ws.name!r} has no git remote configured", status=409
        )
    return ws.remote


def _workdir(ws: "WorkspaceRec") -> Path:
    """This workspace's clone: {base}/{workspace id}. Keyed by id so renaming
    a workspace never orphans its clone. GIT_SYNC_DIR overrides the base."""
    env = os.environ.get("GIT_SYNC_DIR")
    if env:
        return Path(env) / str(ws.id)
    from .connect import _db_path

    return Path(f"{_db_path()}.gitsync") / str(ws.id)


def configured(ws: "WorkspaceRec") -> bool:
    return bool(ws.remote)
```

3. Locks: key by `(loop, workspace)` so different workspaces' syncs don't serialize each other:

```python
_locks: dict[tuple[int, int], asyncio.Lock] = {}


def _lock(ws: "WorkspaceRec") -> asyncio.Lock:
    key = (id(asyncio.get_running_loop()), ws.id)
    lock = _locks.get(key)
    if lock is None:
        lock = _locks[key] = asyncio.Lock()
    return lock
```

4. `_ensure_repo(ws)` uses `remote, branch, wd = _require_remote(ws), ws.branch, _workdir(ws)` (body otherwise unchanged). `_origin_head(wd, ws)` takes `ws` and uses `ws.branch` for the ref.

5. `_load_entity(ws, kind, name, conn_type)`: drop the Task 3/4 interim default resolution; use `ws.id` directly (`get_predefined_query(conn_type or "", name, ws.id)` / `get_dashboard(name, ws.id)`).

6. `store`, `history`, `restore` gain `ws: "WorkspaceRec"` as the first parameter; replace `_remote()` unconfigured checks with `_require_remote(ws)`, `_lock()` with `_lock(ws)`, `_ensure_repo()` with `_ensure_repo(ws)`, `_origin_head(wd)` with `_origin_head(wd, ws)`, `_branch()` in the push with `ws.branch`, and `restore`'s DB upserts pass `workspace_id=ws.id` (dropping the interim resolution).

7. `main.py` git endpoints — each resolves the workspace first:

```python
@app.get("/api/git/status")
async def git_status(request: Request):
    ws, err = await _resolve_workspace(request.query_params.get("workspace"))
    if err:
        return err
    return {"configured": gitsync.configured(ws)}
```

`git_store`/`git_restore` read `b.get("workspace")`, `git_history` reads `q.get("workspace")`, all via `_resolve_workspace`, then pass `ws` as the first argument to the gitsync call.

8. `mcp_server.py` git tools — interim (Task 7 replaces with session resolution): each of `git_store`/`git_history`/`git_restore` resolves the default workspace and passes it:

```python
    from .workspaces import DEFAULT_WORKSPACE, resolve

    ws = await resolve(DEFAULT_WORKSPACE)  # interim until Task 7
    return await _git_tool(gitsync.store(ws, kind, name, conn_type, message))
```

9. Update remaining call-site tests: in `test_api_gitsync.py` the endpoints handle resolution (no signature change needed, but `test_store_unconfigured_409` drops the `monkeypatch` delenv — outside `git_env` the default workspace simply has no remote); `test_mcp_gitsync.py` unchanged behaviorally (tools resolve default internally).

- [ ] **Step 5: Run everything**

Run: `uv run pytest backend/tests`
Expected: PASS.

- [ ] **Step 6: Add the API-level two-workspace test**

Append to `backend/tests/test_api_gitsync.py`:

```python
def test_api_store_routes_by_workspace(tmp_path, monkeypatch):
    import subprocess

    from queryview.queries import save_predefined_query
    from queryview.workspaces import create_workspace, resolve

    monkeypatch.setenv("GIT_SYNC_DIR", str(tmp_path / "clones"))
    remote = tmp_path / "ws.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    _run(create_workspace("t5-api", remote=str(remote)))
    wid = _run(resolve("t5-api")).id
    _run(save_predefined_query("api ws", "clickhouse", "SELECT 1", workspace_id=wid))

    c = TestClient(app)
    assert c.get("/api/git/status", params={"workspace": "t5-api"}).json() == {
        "configured": True
    }
    r = c.post(
        "/api/git/store",
        json={"kind": "query", "name": "api ws", "conn_type": "clickhouse", "workspace": "t5-api"},
    ).json()
    assert r["ok"] is True and r["committed"] is True
    h = c.get(
        "/api/git/history",
        params={"kind": "query", "name": "api ws", "conn_type": "clickhouse", "workspace": "t5-api"},
    ).json()
    assert len(h["revisions"]) == 1
```

Run: `uv run pytest backend/tests/test_api_gitsync.py -v` — expected PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/queryview/gitsync.py backend/queryview/main.py backend/queryview/mcp_server.py backend/tests
git commit -m "workspaces: per-workspace gitsync — clone, lock, branch, remote from the workspace row"
```

---

### Task 6: `/api/workspaces` endpoints

**Files:**
- Modify: `backend/queryview/main.py` (insert the new endpoints just before the `api_not_found` catch-all)
- Test: `backend/tests/test_api_workspaces.py` (new)

**Interfaces:**
- Consumes: Task 2 store functions.
- Produces (frontend Task 8 consumes):
  - `GET /api/workspaces` → `{workspaces: [{name, branch, configured}]}`
  - `POST /api/workspaces` `{name, remote?, branch?}` → `{ok}` (400/409 on error)
  - `PATCH /api/workspaces/{name}` `{name?, remote?, branch?}` → `{ok}`; a present-but-null `remote` clears it, an absent key leaves it
  - `DELETE /api/workspaces/{name}` → `{ok}` (404 unknown, 409 non-empty)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_api_workspaces.py`:

```python
"""The /api/workspaces surface: CRUD, no-secret listing, delete-non-empty 409."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from queryview.main import app


def _run(coro):
    return asyncio.run(coro)


def test_list_includes_default():
    c = TestClient(app)
    names = [w["name"] for w in c.get("/api/workspaces").json()["workspaces"]]
    assert "default" in names


def test_create_list_update_delete_round_trip():
    c = TestClient(app)
    r = c.post(
        "/api/workspaces",
        json={"name": "t6-rt", "remote": "https://u:tok@example.test/r.git", "branch": "dev"},
    )
    assert r.json() == {"ok": True}

    listed = {w["name"]: w for w in c.get("/api/workspaces").json()["workspaces"]}
    assert listed["t6-rt"] == {"name": "t6-rt", "branch": "dev", "configured": True}
    assert "tok" not in r.text and "remote" not in listed["t6-rt"]

    # Rename + clear the remote (present-but-null clears; absent keeps).
    assert c.patch("/api/workspaces/t6-rt", json={"name": "t6-rt2", "remote": None}).json() == {"ok": True}
    listed = {w["name"]: w for w in c.get("/api/workspaces").json()["workspaces"]}
    assert "t6-rt" not in listed
    assert listed["t6-rt2"]["configured"] is False

    assert c.delete("/api/workspaces/t6-rt2").json() == {"ok": True}
    assert c.delete("/api/workspaces/t6-rt2").status_code == 404


def test_create_validation_and_conflict():
    c = TestClient(app)
    assert c.post("/api/workspaces", json={}).status_code == 400
    c.post("/api/workspaces", json={"name": "t6-dup"})
    assert c.post("/api/workspaces", json={"name": "t6-dup"}).status_code == 409


def test_delete_non_empty_409(default_ws_id):
    from queryview.queries import save_predefined_query
    from queryview.workspaces import resolve

    c = TestClient(app)
    c.post("/api/workspaces", json={"name": "t6-full"})
    wid = _run(resolve("t6-full")).id
    _run(save_predefined_query("holder", "clickhouse", "SELECT 1", workspace_id=wid))
    r = c.delete("/api/workspaces/t6-full")
    assert r.status_code == 409
    assert "contains" in r.json()["message"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest backend/tests/test_api_workspaces.py -v`
Expected: FAIL — 404s from the `/api/{rest:path}` catch-all.

- [ ] **Step 3: Implement the endpoints**

In `main.py`, before the `api_not_found` catch-all:

```python
# --- Workspaces (see docs/workspace.md) ------------------------------------
# Admin configuration with secrets (the remote URL may embed a token): exposed
# over REST/UI only, deliberately not over MCP.


def _workspace_error(e: workspaces.WorkspaceError) -> JSONResponse:
    return JSONResponse({"ok": False, "message": str(e)}, status_code=e.status)


@app.get("/api/workspaces")
async def workspaces_list():
    return {"workspaces": await workspaces.list_workspaces()}


@app.post("/api/workspaces")
async def workspaces_create(request: Request):
    b = await _read_json(request)
    b = b if isinstance(b, dict) else {}
    name = _clean_str(b.get("name"))
    if not name:
        return JSONResponse({"ok": False, "message": "name is required"}, status_code=400)
    remote_url = _clean_str(b.get("remote")) or None
    branch = _clean_str(b.get("branch")) or "main"
    try:
        await workspaces.create_workspace(name, remote_url, branch)
    except workspaces.WorkspaceError as e:
        return _workspace_error(e)
    return {"ok": True}


@app.patch("/api/workspaces/{name}")
async def workspaces_update(name: str, request: Request):
    b = await _read_json(request)
    b = b if isinstance(b, dict) else {}
    kwargs: dict[str, Any] = {}
    if "name" in b:
        kwargs["new_name"] = _clean_str(b.get("name")) or None
    if "remote" in b:  # present-but-null clears; absent leaves as-is
        kwargs["remote"] = _clean_str(b.get("remote")) or None
    if "branch" in b:
        kwargs["branch"] = _clean_str(b.get("branch")) or None
    try:
        await workspaces.update_workspace(name, **kwargs)
    except workspaces.WorkspaceError as e:
        return _workspace_error(e)
    return {"ok": True}


@app.delete("/api/workspaces/{name}")
async def workspaces_delete(name: str):
    try:
        await workspaces.delete_workspace(name)
    except workspaces.WorkspaceError as e:
        return _workspace_error(e)
    return {"ok": True}
```

- [ ] **Step 4: Run and commit**

Run: `uv run pytest backend/tests` — expected PASS.

```bash
git add backend/queryview/main.py backend/tests/test_api_workspaces.py
git commit -m "workspaces: /api/workspaces CRUD endpoints"
```

---

### Task 7: Session-scoped workspace for MCP

**Files:**
- Modify: `backend/queryview/remote.py`, `backend/queryview/main.py` (`/api/remote/db`), `backend/queryview/mcp_server.py`
- Test: `backend/tests/test_remote.py`, `backend/tests/test_mcp_gitsync.py`

**Interfaces:**
- Consumes: Tasks 2–5.
- Produces:
  - `remote._Channel.workspace: str | None = None`
  - `remote.set_session_workspace(remote_id: str, workspace: str | None) -> bool`
  - `remote.session_workspace(remote_id: str) -> str | None`
  - `POST /api/remote/db` body gains optional `workspace` (browser reports it alongside `database`; Task 9 sends it).
  - MCP: `list_queries(conn_type="clickhouse", session_id: str | None = None)`; `git_store`/`git_history`/`git_restore` gain trailing `session_id: str | None = None`. `mcp_server._session_workspace_rec(session_id) -> WorkspaceRec` resolves the session's reported workspace, else the default. The agent never names workspaces.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_remote.py`:

```python
def test_session_workspace_report_and_read():
    from queryview import remote

    rid = remote.register()
    try:
        assert remote.session_workspace(rid) is None
        assert remote.set_session_workspace(rid, "team-a") is True
        assert remote.session_workspace(rid) == "team-a"
        assert remote.set_session_workspace("nope", "x") is False
        assert remote.session_workspace("nope") is None
    finally:
        remote.unregister(rid)
```

Append to `backend/tests/test_mcp_gitsync.py`:

```python
def test_git_store_resolves_workspace_from_session(tmp_path, monkeypatch):
    """An armed session on workspace B routes git_store to B's remote; no
    session_id falls back to the default workspace."""
    import subprocess

    from queryview import remote
    from queryview.mcp_server import git_store
    from queryview.queries import save_predefined_query
    from queryview.workspaces import create_workspace, resolve

    monkeypatch.setenv("GIT_SYNC_DIR", str(tmp_path / "clones"))
    rb = tmp_path / "b.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(rb)],
        check=True,
        capture_output=True,
    )
    _run(create_workspace("t7-b", remote=str(rb)))
    wid = _run(resolve("t7-b")).id
    _run(save_predefined_query("sess q", "clickhouse", "SELECT 1", workspace_id=wid))

    rid = remote.register()
    try:
        remote.set_session_workspace(rid, "t7-b")
        r = _run(git_store("query", "sess q", "clickhouse", session_id=rid))
        assert r["ok"] is True and r["committed"] is True
        log = subprocess.run(
            ["git", "log", "--format=%s", "main"],
            cwd=rb,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "sess q" in log
    finally:
        remote.unregister(rid)


def test_git_store_without_session_uses_default_workspace():
    from queryview.mcp_server import git_store

    # Outside git_env the default workspace has no remote -> unconfigured.
    r = _run(git_store("query", "anything", "clickhouse"))
    assert r["ok"] is False
    assert "no git remote" in r["message"]
```

(Match `_run` and import style already used by `test_mcp_gitsync.py`; if the git tools there are invoked via `.fn(...)` or similar FastMCP accessor, follow the file's existing call pattern.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest backend/tests/test_remote.py backend/tests/test_mcp_gitsync.py -v`
Expected: FAIL — `set_session_workspace` missing / unexpected `session_id` argument.

- [ ] **Step 3: Implement**

`remote.py` — add to `_Channel`:

```python
    # The workspace this browser session is on, reported by the UI like
    # `database`, so session-scoped MCP tools resolve against it.
    workspace: str | None = None
```

and next to the database accessors:

```python
def set_session_workspace(remote_id: str, workspace: str | None) -> bool:
    """Record the workspace a live session is on (reported by the UI). Returns
    False for an unknown/inactive session."""
    channel = _channels.get(remote_id)
    if channel is None:
        return False
    channel.workspace = workspace
    return True


def session_workspace(remote_id: str) -> str | None:
    """The workspace a live session is on, or None if unknown/unreported."""
    channel = _channels.get(remote_id)
    return channel.workspace if channel else None
```

`main.py` — in `remote_db`, after the `set_session_database` call:

```python
    if "workspace" in b:
        raw_ws = b.get("workspace")
        remote.set_session_workspace(
            session_id, raw_ws if isinstance(raw_ws, str) and raw_ws else None
        )
```

`mcp_server.py` — add the resolver:

```python
async def _session_workspace_rec(session_id: str | None):
    """The workspace the given armed session is on (reported by the browser),
    else the default workspace. MCP is deliberately workspace-unaware: the
    human picks the workspace in the UI; the agent works in session scope."""
    from . import workspaces

    name = remote.session_workspace(session_id) if session_id else None
    return await workspaces.resolve(name or workspaces.DEFAULT_WORKSPACE)
```

Then:
- `list_queries(conn_type: str = "clickhouse", session_id: str | None = None)` — replace the interim default resolution with `ws = await _session_workspace_rec(session_id)`; docstring gains: `session_id: optional armed-session id; scopes the list to that session's workspace (default workspace otherwise).`
- `git_store(kind, name, conn_type=None, message=None, session_id: str | None = None)` → `ws = await _session_workspace_rec(session_id)` then `gitsync.store(ws, kind, name, conn_type, message)`. Same pattern for `git_history` and `git_restore`. Each docstring's Args gains the same `session_id` line, and the `git_store` docstring's "Requires the server to be configured with GIT_SYNC_REMOTE" sentence becomes "Requires the target workspace to have a git remote configured."

- [ ] **Step 4: Run and commit**

Run: `uv run pytest backend/tests` — expected PASS.

```bash
git add backend/queryview/remote.py backend/queryview/main.py backend/queryview/mcp_server.py backend/tests
git commit -m "workspaces: session-scoped MCP — browser reports workspace, tools resolve via session_id"
```

---

### Task 8: Frontend workspace module + gitsync client

**Files:**
- Create: `frontend/src/workspace.ts`, `frontend/src/workspace.test.ts`
- Modify: `frontend/src/gitsync.ts`, `frontend/src/gitsync.test.ts` (only if imports/types need it)

**Interfaces:**
- Consumes: `/api/workspaces` (Task 6), workspace params on `/api/git/*` (Task 5).
- Produces (Task 9 consumes):
  - `workspace.ts`: `type Workspace = { name: string; branch: string; configured: boolean }`, `activeWorkspace(): string`, `setActiveWorkspace(name: string): void`, `listWorkspaces(): Promise<Workspace[]>`, `createWorkspace(name, remote?, branch?)`, `updateWorkspace(name, changes: { name?: string; remote?: string | null; branch?: string })`, `deleteWorkspace(name)` — the latter four return `Promise<{ ok: boolean; message?: string }>` (list returns the array).
  - `gitsync.ts`: `gitStatus(workspace: string)`, `gitStore(kind, name, connType?, workspace?)`, `gitHistory(kind, name, opts: { connType?; before?; limit?; workspace? })`, `gitRestore(kind, name, ref, connType?, workspace?)`, `invalidateGitStatus(): void`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/workspace.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from 'vitest'
import { activeWorkspace, setActiveWorkspace } from './workspace'

function stubStorage() {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
  })
}

afterEach(() => vi.unstubAllGlobals())

describe('activeWorkspace', () => {
  it('defaults to "default" with nothing stored', () => {
    stubStorage()
    expect(activeWorkspace()).toBe('default')
  })

  it('round-trips through setActiveWorkspace', () => {
    stubStorage()
    setActiveWorkspace('team-a')
    expect(activeWorkspace()).toBe('team-a')
  })

  it('falls back to "default" when localStorage is unavailable', () => {
    // No stub: node has no localStorage; must not throw.
    expect(activeWorkspace()).toBe('default')
  })
})
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test -w frontend`
Expected: FAIL — `./workspace` doesn't exist.

- [ ] **Step 3: Implement `workspace.ts`**

```typescript
// Active-workspace state (localStorage) + client for /api/workspaces.
// The active workspace scopes predefined queries, dashboards, and git sync;
// 'default' matches the backend's fallback for an omitted workspace param.
// See docs/workspace.md.

export type Workspace = { name: string; branch: string; configured: boolean }

export type WorkspaceResult = { ok: boolean; message?: string }

const KEY = 'qv_workspace'

export function activeWorkspace(): string {
  try {
    return localStorage.getItem(KEY) || 'default'
  } catch {
    return 'default'
  }
}

export function setActiveWorkspace(name: string): void {
  try {
    localStorage.setItem(KEY, name)
  } catch {
    /* non-persistent contexts still work within the page's lifetime */
  }
}

export async function listWorkspaces(): Promise<Workspace[]> {
  const r = await (await fetch('/api/workspaces')).json()
  return (r.workspaces ?? []) as Workspace[]
}

export async function createWorkspace(
  name: string,
  remote?: string,
  branch?: string,
): Promise<WorkspaceResult> {
  const res = await fetch('/api/workspaces', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, remote: remote || null, branch: branch || null }),
  })
  return res.json()
}

export async function updateWorkspace(
  name: string,
  changes: { name?: string; remote?: string | null; branch?: string },
): Promise<WorkspaceResult> {
  const res = await fetch(`/api/workspaces/${encodeURIComponent(name)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  })
  return res.json()
}

export async function deleteWorkspace(name: string): Promise<WorkspaceResult> {
  const res = await fetch(`/api/workspaces/${encodeURIComponent(name)}`, {
    method: 'DELETE',
  })
  return res.json()
}
```

- [ ] **Step 4: Thread workspace through `gitsync.ts`**

- `gitStatus` becomes per-workspace (the cache keys by workspace; `invalidateGitStatus` lets the manage dialog clear it after settings change):

```typescript
// Whether git sync is configured is per-workspace server state — cache one
// probe per workspace, cleared when workspace settings change.
const statusCache = new Map<string, Promise<boolean>>()

export function gitStatus(workspace: string): Promise<boolean> {
  let cached = statusCache.get(workspace)
  if (!cached) {
    cached = (async () => {
      try {
        const r = await (
          await fetch(`/api/git/status?workspace=${encodeURIComponent(workspace)}`)
        ).json()
        return Boolean(r.configured)
      } catch {
        return false
      }
    })()
    statusCache.set(workspace, cached)
  }
  return cached
}

export function invalidateGitStatus(): void {
  statusCache.clear()
}
```

- `gitStore(kind, name, connType?, workspace?)` adds `workspace` to the JSON body; `gitHistory`'s `opts` gains `workspace?: string` set as a query param when present; `gitRestore(kind, name, ref, connType?, workspace?)` adds it to the body.

- [ ] **Step 5: Run tests, lint, build; commit**

Run: `npm test -w frontend` — expected PASS (workspace tests + existing gitsync tests).
Run: `npm run lint -w frontend` — expected clean. `npm run build` currently fails only if call sites don't match — `GitSyncControls.tsx` still calls `gitStatus()` with no argument; update that single call now to `gitStatus(activeWorkspace())` (import `activeWorkspace` from `./workspace`) so the build stays green; the rest of the component is Task 9.
Run: `npm run build` — expected PASS.

```bash
git add frontend/src/workspace.ts frontend/src/workspace.test.ts frontend/src/gitsync.ts frontend/src/GitSyncControls.tsx
git commit -m "workspaces: frontend workspace state module + per-workspace git client"
```

---

### Task 9: Workspace switcher UI + threading

**Files:**
- Create: `frontend/src/WorkspaceSwitcher.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/QueryView.tsx`, `frontend/src/DashboardView.tsx`, `frontend/src/GitSyncControls.tsx`

**Interfaces:**
- Consumes: Task 8 module; Task 7's `/api/remote/db` `workspace` field.
- Produces: header switcher with test-ids `workspace-switcher`, `workspace-option`, `workspace-manage`, `workspace-name-input`, `workspace-remote-input`, `workspace-branch-input`, `workspace-create`, `workspace-delete`, `workspace-save`, `workspace-error` (Task 10 e2e relies on these). Every scoped fetch carries the active workspace; switching remounts the page views.

- [ ] **Step 1: Build `WorkspaceSwitcher.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { invalidateGitStatus } from './gitsync'
import {
  createWorkspace,
  deleteWorkspace,
  listWorkspaces,
  updateWorkspace,
  type Workspace,
} from './workspace'

type Props = {
  workspace: string
  onSwitch: (name: string) => void
}

// Header dropdown for the active workspace plus a small manage panel
// (create / rename / set-clear remote / delete). Workspace settings are admin
// config; the remote URL is write-only here — the server never returns it.
export default function WorkspaceSwitcher({ workspace, onSwitch }: Props) {
  const [open, setOpen] = useState(false)
  const [manage, setManage] = useState(false)
  const [list, setList] = useState<Workspace[]>([])
  const [error, setError] = useState('')
  // Manage-panel form state; empty remote means "leave as-is" on save.
  const [name, setName] = useState('')
  const [remote, setRemote] = useState('')
  const [branch, setBranch] = useState('')

  async function reload() {
    try {
      setList(await listWorkspaces())
    } catch {
      /* keep the last list */
    }
  }

  useEffect(() => {
    void reload()
  }, [])

  function openManage() {
    setManage(true)
    setName(workspace)
    setRemote('')
    setBranch(list.find((w) => w.name === workspace)?.branch ?? '')
    setError('')
  }

  async function saveSettings() {
    const changes: { name?: string; remote?: string | null; branch?: string } = {}
    if (name.trim() && name.trim() !== workspace) changes.name = name.trim()
    if (remote.trim()) changes.remote = remote.trim()
    if (branch.trim()) changes.branch = branch.trim()
    const r = await updateWorkspace(workspace, changes)
    if (!r.ok) {
      setError(r.message ?? 'update failed')
      return
    }
    invalidateGitStatus()
    setManage(false)
    await reload()
    if (changes.name) onSwitch(changes.name)
  }

  async function create() {
    if (!name.trim()) return
    const r = await createWorkspace(name.trim(), remote.trim() || undefined, branch.trim() || undefined)
    if (!r.ok) {
      setError(r.message ?? 'create failed')
      return
    }
    invalidateGitStatus()
    setManage(false)
    await reload()
    onSwitch(name.trim())
  }

  async function remove() {
    if (!window.confirm(`Delete workspace '${workspace}'? It must be empty.`)) return
    const r = await deleteWorkspace(workspace)
    if (!r.ok) {
      setError(r.message ?? 'delete failed')
      return
    }
    setManage(false)
    await reload()
    onSwitch('default')
  }

  return (
    <div className="relative">
      <button
        type="button"
        data-testid="workspace-switcher"
        onClick={() => {
          setOpen((o) => !o)
          if (!open) void reload()
        }}
        className="glass-chip flex items-center gap-2 px-3 py-1.5 text-sm font-medium"
      >
        {workspace}
        <span className="text-xs text-slate-400">▾</span>
      </button>
      {open && (
        <div className="glass-popover absolute right-0 top-full z-10 mt-2 w-64 p-1 text-sm">
          {list.map((w) => (
            <button
              key={w.name}
              type="button"
              data-testid="workspace-option"
              onClick={() => {
                setOpen(false)
                onSwitch(w.name)
              }}
              className={`block w-full truncate rounded px-2 py-1.5 text-left hover:bg-white/10 ${
                w.name === workspace ? 'text-indigo-200' : 'text-slate-200'
              }`}
            >
              {w.name}
            </button>
          ))}
          <button
            type="button"
            data-testid="workspace-manage"
            onClick={() => {
              setOpen(false)
              openManage()
            }}
            className="mt-1 block w-full rounded border-t border-white/10 px-2 py-1.5 text-left text-xs text-slate-400 hover:bg-white/10"
          >
            Manage workspaces…
          </button>
        </div>
      )}
      {manage && (
        <div className="glass-popover absolute right-0 top-full z-10 mt-2 w-80 space-y-2 p-3 text-sm">
          <div className="text-xs text-slate-400">Workspace name</div>
          <input
            data-testid="workspace-name-input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded bg-white/10 px-2 py-1 text-slate-100"
          />
          <div className="text-xs text-slate-400">
            Git remote URL (leave blank to keep; settings are write-only)
          </div>
          <input
            data-testid="workspace-remote-input"
            value={remote}
            onChange={(e) => setRemote(e.target.value)}
            placeholder="https://user:token@github.com/org/repo.git"
            className="w-full rounded bg-white/10 px-2 py-1 font-mono text-xs text-slate-100"
          />
          <div className="text-xs text-slate-400">Branch</div>
          <input
            data-testid="workspace-branch-input"
            value={branch}
            onChange={(e) => setBranch(e.target.value)}
            placeholder="main"
            className="w-full rounded bg-white/10 px-2 py-1 text-slate-100"
          />
          <div className="flex gap-2 pt-1">
            <button type="button" data-testid="workspace-save" onClick={() => void saveSettings()} className="glass-btn px-2 py-1 text-xs font-medium">
              Save
            </button>
            <button type="button" data-testid="workspace-create" onClick={() => void create()} className="glass-btn px-2 py-1 text-xs font-medium">
              Create as new
            </button>
            <button type="button" data-testid="workspace-delete" onClick={() => void remove()} className="glass-btn px-2 py-1 text-xs font-medium text-red-300">
              Delete
            </button>
            <button type="button" onClick={() => setManage(false)} className="glass-btn px-2 py-1 text-xs">
              Close
            </button>
          </div>
          {error && (
            <p data-testid="workspace-error" className="text-xs text-red-300">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Wire it into `App.tsx`**

- Imports: `import WorkspaceSwitcher from './WorkspaceSwitcher'` and `import { activeWorkspace, setActiveWorkspace } from './workspace'`.
- State in `Shell`: `const [workspace, setWorkspace] = useState(activeWorkspace())`.
- Switch handler:

```tsx
  function switchWorkspace(name: string) {
    setActiveWorkspace(name)
    setWorkspace(name)
  }
```

- Render the switcher inside the `<nav>` block, before the Queries link: `<WorkspaceSwitcher workspace={workspace} onSwitch={switchWorkspace} />`.
- Remount pages on switch so they refetch under the new workspace: `<QueryView key={workspace} ... />` and `<DashboardView key={workspace} ... />` in the two `<Route>` elements.
- Report the workspace to the armed session — extend the existing `/api/remote/db` effect body and deps:

```tsx
  // Report the active database and workspace to the live session so the agent's
  // session-scoped tools resolve against them. Fires on arm and on each change.
  useEffect(() => {
    if (!armed || !remoteId) return
    void fetch('/api/remote/db', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: remoteId,
        database: connection?.database ?? null,
        workspace,
      }),
    }).catch(() => {})
  }, [armed, remoteId, connection?.database, workspace])
```

- [ ] **Step 3: Thread the workspace through the page fetches**

All three files import `{ activeWorkspace }` from `./workspace` and read it at call time (the `key={workspace}` remount keeps renders consistent).

`QueryView.tsx`:
- `loadPredefined` URL: `` `/api/predefined-queries?type=${encodeURIComponent(connectionType)}&workspace=${encodeURIComponent(activeWorkspace())}` ``
- `save()` body: add `workspace: activeWorkspace(),` to the `JSON.stringify({...})` object.

`DashboardView.tsx` (four call sites):
- `loadDashboards()` and the list effect: `` fetch(`/api/dashboards?workspace=${encodeURIComponent(activeWorkspace())}`) ``
- `save()` body: add `workspace: activeWorkspace(),`
- `loadDashboard()`: `` fetch(`/api/dashboards/${encodeURIComponent(name)}?workspace=${encodeURIComponent(activeWorkspace())}`) ``

`GitSyncControls.tsx`:
- `gitStatus(activeWorkspace())` (done in Task 8), `gitStore(kind, name, connType, activeWorkspace())`, `gitHistory(kind, name, { connType, before, limit: 10, workspace: activeWorkspace() })`, `gitRestore(kind, name, sha, connType, activeWorkspace())`.
- Tooltip text: `'Git sync is not configured for this workspace'`.

- [ ] **Step 4: Verify and commit**

Run: `npm test -w frontend` — expected PASS.
Run: `npm run lint -w frontend` — expected clean.
Run: `npm run build` — expected PASS.

```bash
git add frontend/src
git commit -m "workspaces: switcher UI, per-workspace fetches, session workspace reporting"
```

---

### Task 10: e2e — two-workspace isolation

**Files:**
- Create: `e2e/test_workspaces.py`

**Interfaces:**
- Consumes: Task 9 test-ids, Task 6 endpoints; the e2e stack (`BASE_URL`, started separately — see `e2e/conftest.py`).
- Deliberate deviation from the spec's testing note: the e2e exercises two-workspace *content* isolation rather than two git remotes — CI's loopback git daemon serves a single remote (the default workspace's seed), and two-remote git isolation is already covered end-to-end at the API layer in Task 5's tests.

- [ ] **Step 1: Write the e2e test**

```python
"""Workspace e2e: the switcher isolates dashboards between workspaces. Uses a
uniquely-named workspace per run and cleans it up so reruns are idempotent."""

import uuid

import httpx
from playwright.sync_api import Page, expect


def test_switcher_isolates_dashboards(page: Page, base_url: str):
    ws = f"e2e-{uuid.uuid4().hex[:6]}"
    dash = f"ws dash {ws}"
    httpx.post(f"{base_url}/api/workspaces", json={"name": ws}).raise_for_status()
    httpx.post(
        f"{base_url}/api/dashboards",
        json={
            "name": dash,
            "connection": "prod",
            "html": "<html><body>ws</body></html>",
            "queries": {},
            "workspace": ws,
        },
    ).raise_for_status()

    try:
        # Default workspace: the dashboard is absent from the picker's list.
        page.goto(f"{base_url}/dashboard")
        expect(page.get_by_test_id("workspace-switcher")).to_have_text("default ▾")
        assert dash not in [
            d["name"]
            for d in httpx.get(f"{base_url}/api/dashboards").json()["dashboards"]
        ]

        # Switch: the page views remount and the dashboard appears in that
        # workspace's list.
        page.get_by_test_id("workspace-switcher").click()
        page.get_by_test_id("workspace-option").filter(has_text=ws).click()
        expect(page.get_by_test_id("workspace-switcher")).to_have_text(f"{ws} ▾")
        page.goto(f"{base_url}/dashboard?name={httpx.QueryParams({'n': dash})['n']}")
        # The dashboard resolves (renders or at least doesn't 404 into an error).
        expect(page.locator("body")).not_to_contain_text("not found")
    finally:
        # Reset the browser's persisted choice and remove the test workspace.
        page.evaluate("localStorage.setItem('qv_workspace', 'default')")
        httpx.request(
            "DELETE",
            f"{base_url}/api/workspaces/{ws}",
        )
```

Note for the implementer: the workspace still contains the dashboard, so the final DELETE returns 409 — that's fine as cleanup-best-effort (there is no dashboard-delete endpoint yet; see future.md). Do not assert on it. Adjust the switcher-text assertions to the exact rendered text if the `▾` glyph differs.

- [ ] **Step 2: Run it against a local stack**

Start the stack (two terminals or `npm run dev`-style background): `npm run build && SERVE_STATIC=1 uv run queryview-backend` then
Run: `BASE_URL=http://localhost:8000 uv run pytest e2e/test_workspaces.py -v`
Expected: PASS. (In CI it runs under the existing e2e workflow; the gitsync loopback daemon still seeds the *default* workspace via `GIT_SYNC_REMOTE` at first migration.)

- [ ] **Step 3: Commit**

```bash
git add e2e/test_workspaces.py
git commit -m "workspaces: e2e switcher isolation test"
```

---

### Task 11: Docs + cleanup

**Files:**
- Create: `docs/workspace.md`
- Modify: `docs/gitsync.md`, `docs/future.md`, `docs/remote.md`, `docs/api.md`, `backend/queryview/gitsync.py` (docstring only, if not already updated)
- Delete: `docs/superpowers/specs/2026-07-10-workspaces-design.md`, `docs/superpowers/plans/2026-07-10-workspaces.md`

- [ ] **Step 1: Write `docs/workspace.md`**

```markdown
# Workspaces

Predefined queries and dashboards belong to a **workspace**; each workspace
git-syncs to its own remote (see [gitsync.md](./gitsync.md)). Entity names are
unique per workspace, so two workspaces can each have a "daily revenue"
dashboard. Connections are global — any workspace can use any connection.

A `default` workspace always exists after migration (seeded from the legacy
`GIT_SYNC_REMOTE`/`GIT_SYNC_BRANCH` env vars, which are read only at that
migration). It is an ordinary row — renamable and deletable like any other;
it is only special as the fallback for an omitted `workspace` parameter.

## Configuration

Workspace settings live in the database, not env vars. The remote URL may
embed a token, so it is encrypted at rest (same AES-GCM key as connection
configs) and is write-only through the API — never returned. A workspace
without a remote is a pure namespace: its git controls are disabled.

Deleting a workspace requires it to be empty (409 otherwise); the git remote
keeps its history either way.

## API

- `GET /api/workspaces` → `{workspaces: [{name, branch, configured}]}`
- `POST /api/workspaces` `{name, remote?, branch?}`
- `PATCH /api/workspaces/{name}` `{name?, remote?, branch?}` — a null `remote`
  clears it; an absent key leaves it unchanged
- `DELETE /api/workspaces/{name}`

Scoped endpoints (predefined queries, dashboards, `/api/git/*`) accept an
optional `workspace` name, defaulting to `default`.

## UI

The header shows a workspace switcher (dropdown + manage panel for
create/rename/remote/branch/delete). The active workspace is remembered in
localStorage; switching reloads the query and dashboard lists.

## MCP

MCP is workspace-unaware: no workspace parameter, no workspace tools. The
armed browser session reports its active workspace (alongside the database);
`list_queries` and the `git_*` tools take an optional `session_id` and resolve
the workspace from it, falling back to `default`. The human picks the
workspace; the agent works inside the session it was invited into. Workspace
CRUD stays API/UI-only because it is admin configuration involving secrets.

## Related docs

- [gitsync.md](./gitsync.md) — backup & restore mechanics.
- [query.md](./query.md) — predefined queries.
- [dashboard.md](./dashboard.md) — dashboards.
- [api.md](./api.md) — backend JSON API.
```

- [ ] **Step 2: Update `docs/gitsync.md`**

- Replace the Configuration table with:

```markdown
## Configuration

Each workspace (see [workspace.md](./workspace.md)) carries its own remote URL
and branch, managed in the UI/API and encrypted at rest. A workspace without a
remote has git sync disabled.

| Env var           | Meaning                                              | Default              |
| ----------------- | ---------------------------------------------------- | -------------------- |
| `GIT_SYNC_REMOTE` | Seed for the default workspace's remote (read once, at migration) | unset ⇒ none |
| `GIT_SYNC_BRANCH` | Seed for the default workspace's branch (read once, at migration) | `main`       |
| `GIT_SYNC_DIR`    | Base dir for per-workspace clones                    | `{db_path}.gitsync/` |

Clones live at `{base}/{workspace id}/`; the repository layout inside each
clone is unchanged.
```

- In the API section, note that `status`/`store`/`history`/`restore` accept an optional `workspace` (default `default`), and change the MCP sentence to: `MCP tools git_store, git_history, git_restore mirror the same surface, resolving the workspace from an optional session_id (see workspace.md).`
- UI section: "Both are disabled when the active workspace has no remote configured."
- Add `workspace.md` to Related docs.

- [ ] **Step 3: Update `docs/future.md`, `docs/remote.md`, `docs/api.md`**

- `future.md`: delete the whole "Per-project git sync (project resolution)" section; add:

```markdown
## Workspace-scoped connections

Connections are global — any workspace (see [workspace.md](./workspace.md))
can use any connection. If workspaces come to represent genuinely separate
environments or teams, scope connections per workspace: each workspace sees
only its own, and restore requires the connection to exist in that workspace.
Deferred because it multiplies setup for the common single-team case.
```

- `remote.md`: in "How it works", extend the sentence about the browser reporting its database: it reports the active **workspace** the same way (`POST /api/remote/db` with `{session_id, database, workspace}`), which session-scoped MCP tools resolve against.
- `api.md`: add the `/api/workspaces` endpoints and the optional `workspace` parameter on predefined-queries/dashboards/git endpoints, following the file's existing format (read it first and mirror its style).

- [ ] **Step 4: Delete the shipped spec and plan**

Per CLAUDE.md, plans/specs are deleted when the work ships:

```bash
git rm docs/superpowers/specs/2026-07-10-workspaces-design.md docs/superpowers/plans/2026-07-10-workspaces.md
```

Fix any links that pointed at them (grep: `grep -rn "2026-07-10-workspaces" docs/`).

- [ ] **Step 5: Final full verification**

Run: `uv run pytest backend/tests` — expected PASS.
Run: `npm test -w frontend && npm run lint -w frontend && npm run build` — expected PASS.

- [ ] **Step 6: Commit and push**

```bash
git add docs backend/queryview/gitsync.py
git commit -m "workspaces: docs — workspace.md, gitsync config rewrite, future.md follow-up"
git push -u origin claude/future-md-brainstorm-hbfexb
```
