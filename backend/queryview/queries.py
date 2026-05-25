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


async def list_predefined_queries(conn_type: str) -> list[dict[str, str]]:
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
    return [{"query_name": r.query_name, "query": r.query} for r in rows]


async def save_predefined_query(query_name: str, conn_type: str, query: str) -> None:
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
            row = PredefinedQuery(query_name=query_name, type=conn_type, query=query)
        else:
            row.query = query
        s.add(row)
        await s.commit()
