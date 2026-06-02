"""Dashboard store: globally-shared dashboards (an HTML layout plus a set of
named SQL queries) keyed by name. Reuses the SQLite engine owned by connect.py,
mirroring queries.py. Also hosts the shared upsert-and-push helper that both the
REST endpoint and the MCP tool call, so neither has to know about the other."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Field, SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from . import remote
from .connect import _engine_for_db, _ensure_schema, _now_ms


class Dashboard(SQLModel, table=True):
    __tablename__ = "dashboards"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    connection: str  # connection name the queries run against
    html: str  # agent-authored HTML document
    queries: str  # JSON text: {query_name: SQL}
    updated_at: int  # unix ms


async def upsert_dashboard(
    name: str, connection: str, html: str, queries: dict[str, str]
) -> None:
    """Upsert a dashboard by name; `queries` is serialized to JSON text."""
    await _ensure_schema()
    payload = json.dumps(queries)
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Dashboard).where(Dashboard.name == name))).first()
        if row is None:
            row = Dashboard(
                name=name,
                connection=connection,
                html=html,
                queries=payload,
                updated_at=_now_ms(),
            )
        else:
            row.connection = connection
            row.html = html
            row.queries = payload
            row.updated_at = _now_ms()
        s.add(row)
        await s.commit()


async def get_dashboard(name: str) -> dict[str, Any] | None:
    """A single dashboard with its `queries` parsed back to a dict, or None."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        row = (await s.exec(select(Dashboard).where(Dashboard.name == name))).first()
    if row is None:
        return None
    try:
        queries = json.loads(row.queries)
    except (ValueError, TypeError):
        queries = {}
    return {
        "name": row.name,
        "connection": row.connection,
        "html": row.html,
        "queries": queries,
    }


async def list_dashboards() -> list[dict[str, Any]]:
    """Saved dashboards ordered by name, without the html/queries payload."""
    await _ensure_schema()
    async with AsyncSession(_engine_for_db()) as s:
        rows = (await s.exec(select(Dashboard).order_by(Dashboard.name))).all()
    return [
        {"name": r.name, "connection": r.connection, "updated_at": r.updated_at}
        for r in rows
    ]


async def _upsert_and_push(
    name: str,
    connection: str,
    html: str,
    queries: dict[str, str],
    session_id: str | None,
) -> tuple[bool, bool, str]:
    """Persist a dashboard, then (if `session_id` given) push it to that live
    browser session. Returns (persisted, pushed, message). The push is
    best-effort: an unknown/inactive session leaves the dashboard saved with
    pushed=False, mirroring remote.push's contract."""
    await upsert_dashboard(name, connection, html, queries)
    if session_id:
        ok, message = remote.push(
            session_id,
            {
                "type": "dashboard",
                "name": name,
                "connection": connection,
                "html": html,
                "queries": queries,
            },
        )
        return True, ok, message
    return True, False, "persisted"
