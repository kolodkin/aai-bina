"""Workspace domain: named git-sync targets that own predefined queries and
dashboards. The remote URL may embed a token, so it is encrypted at rest with
connect.py's AES-GCM helpers and never returned by list_workspaces. Docs:
docs/workspace.md."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

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
    __tablename__: ClassVar[str] = "workspaces"

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
    return [{"name": r.name, "branch": r.branch, "configured": bool(r.remote)} for r in rows]


async def create_workspace(name: str, remote: str | None = None, branch: str = "main") -> None:
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
