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
    # Raw YAML (column_name -> {type, value}) controlling cell rendering; NULL =
    # none. Never parsed here — interpreted client-side, matched to columns by name.
    cell_view: str | None = Field(default=None)
    # Raw JSON text (or NULL) for saved presentation, stored verbatim like
    # cell_view: order_by is [{"name","dir"}], fields is ["col", ...].
    order_by: str | None = Field(default=None)
    fields: str | None = Field(default=None)


async def list_predefined_queries(conn_type: str) -> list[dict[str, str | None]]:
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


async def list_predefined_queries_view(conn_type: str) -> list[dict]:
    """Like list_predefined_queries but with order_by/fields parsed from their
    stored JSON text into values (cell_view stays raw YAML). Shared by the HTTP
    list endpoint and the MCP list_queries tool so both present the same shape."""
    import json

    rows = await list_predefined_queries(conn_type)
    for r in rows:
        ob, fl = r.get("order_by"), r.get("fields")
        r["order_by"] = json.loads(ob) if ob else None
        r["fields"] = json.loads(fl) if fl else None
    return rows


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
                query_name=query_name,
                type=conn_type,
                query=query,
                cell_view=cell_view,
                order_by=order_by,
                fields=fields,
            )
        else:
            row.query = query
            row.cell_view = cell_view
            row.order_by = order_by
            row.fields = fields
        s.add(row)
        await s.commit()
