"""Predefined query store: reusable SQL keyed by connection type within a
workspace (names are unique per workspace). Reuses the SQLite engine owned by
connect.py."""

from __future__ import annotations

from typing import Any, ClassVar

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from .connect import _engine_for_db, _ensure_schema


class PredefinedQuery(SQLModel, table=True):
    __tablename__: ClassVar[str] = "predefined_queries"
    __table_args__ = (UniqueConstraint("workspace_id", "type", "query_name", name="uq_predefined_ws_type_name"),)

    id: int | None = Field(default=None, primary_key=True)
    query_name: str = Field(index=True)
    type: str = Field(index=True)
    workspace_id: int  # owning workspace (workspaces.id); names unique per workspace
    query: str
    # Raw YAML (column_name -> {type, value}) controlling cell rendering; NULL =
    # none. Never parsed here — interpreted client-side, matched to columns by name.
    cell_view: str | None = Field(default=None)
    # Raw JSON text (or NULL) for saved presentation, stored verbatim like
    # cell_view: order_by is [{"name","dir"}], fields is ["col", ...].
    order_by: str | None = Field(default=None)
    fields: str | None = Field(default=None)


def _row_dict(r: PredefinedQuery) -> dict[str, str | None]:
    """The row shape every accessor here returns (order_by/fields stay the
    stored JSON text)."""
    return {
        "query_name": r.query_name,
        "query": r.query,
        "cell_view": r.cell_view,
        "order_by": r.order_by,
        "fields": r.fields,
    }


async def list_predefined_queries(conn_type: str, workspace_id: int) -> list[dict[str, str | None]]:
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
    return [_row_dict(r) for r in rows]


async def list_all_predefined_queries(workspace_id: int) -> list[dict[str, str | None]]:
    """Every saved query in one workspace regardless of connection type,
    ordered by (type, name) — the row shape of list_predefined_queries plus
    `type`. Used by the whole-workspace YAML export."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        rows = (
            await s.exec(
                select(PredefinedQuery)
                .where(PredefinedQuery.workspace_id == workspace_id)
                .order_by(PredefinedQuery.type, PredefinedQuery.query_name)
            )
        ).all()
    return [{"type": r.type, **_row_dict(r)} for r in rows]


async def get_predefined_query(conn_type: str, query_name: str, workspace_id: int) -> dict[str, str | None] | None:
    """One saved query by (workspace, type, name) — the unique key — in the same
    row shape as list_predefined_queries items, or None."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (
            await s.exec(
                select(PredefinedQuery).where(
                    PredefinedQuery.type == conn_type,
                    PredefinedQuery.query_name == query_name,
                    PredefinedQuery.workspace_id == workspace_id,
                )
            )
        ).first()
    return _row_dict(row) if row is not None else None


async def list_predefined_queries_view(conn_type: str, workspace_id: int) -> list[dict]:
    """Like list_predefined_queries but with order_by/fields parsed from their
    stored JSON text into values (cell_view stays raw YAML). Shared by the HTTP
    list endpoint and the MCP list_queries tool so both present the same shape."""
    import json

    rows = await list_predefined_queries(conn_type, workspace_id)
    for r in rows:
        ob, fl = r.get("order_by"), r.get("fields")
        r["order_by"] = json.loads(ob) if ob else None
        r["fields"] = json.loads(fl) if fl else None
    return rows


async def save_predefined_queries(rows: list[dict[str, Any]], *, workspace_id: int) -> None:
    """Upsert many predefined queries — each a dict with string `type`,
    `query_name`, `query` and optional `cell_view`/`order_by`/`fields` (JSON
    text or None) — in one transaction, keyed by (workspace, type, query_name)."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        for r in rows:
            row = (
                await s.exec(
                    select(PredefinedQuery).where(
                        PredefinedQuery.type == r["type"],
                        PredefinedQuery.query_name == r["query_name"],
                        PredefinedQuery.workspace_id == workspace_id,
                    )
                )
            ).first()
            if row is None:
                row = PredefinedQuery(query_name=r["query_name"], type=r["type"], workspace_id=workspace_id, query="")
            row.query = r["query"] or ""
            row.cell_view = r.get("cell_view")
            row.order_by = r.get("order_by")
            row.fields = r.get("fields")
            s.add(row)
        await s.commit()


async def save_predefined_query(
    query_name: str,
    conn_type: str,
    query: str,
    cell_view: str | None = None,
    order_by: str | None = None,
    fields: str | None = None,
    *,
    workspace_id: int,
) -> None:
    """Upsert a predefined query by (workspace, type, query_name)."""
    await save_predefined_queries(
        [
            {
                "type": conn_type,
                "query_name": query_name,
                "query": query,
                "cell_view": cell_view,
                "order_by": order_by,
                "fields": fields,
            }
        ],
        workspace_id=workspace_id,
    )
