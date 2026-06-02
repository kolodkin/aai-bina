"""The MCP layer: a FastMCP server (mounted by main.py at /mcp) exposing a
single tool that pushes a query to a live QueryView browser session. Tools
delegate to the in-process remote.py hub."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import remote
from .dashboards import _upsert_and_push

mcp = FastMCP("queryview", stateless_http=True)
mcp.settings.streamable_http_path = "/"


@mcp.tool()
async def push_query(
    session_id: str,
    query: str,
    limit: int = 100,
    offset: int = 0,
    order_by: list[dict[str, Any]] | None = None,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """Push a SQL query to a live QueryView browser session.

    The targeted browser fills its query panel and auto-runs the query. Get the
    `session_id` from the QueryView UI: the agent icon next to the connection
    status pill, after enabling "Allow remote control".

    Args:
        session_id: The session id shown in the QueryView popover.
        query: The SQL to run.
        limit: Page size (default 100).
        offset: Row offset (default 0).
        order_by: Optional sort, e.g. [{"name": "id", "dir": "DESC"}].
        fields: Optional column names to display; omit to show all columns.
    """
    payload = {
        "type": "query",
        "query": query,
        "limit": limit,
        "offset": offset,
        "order_by": order_by,
        "fields": fields,
    }
    ok, message = remote.push(session_id, payload)
    return {"ok": ok, "message": message}


@mcp.tool()
async def upsert_dashboard(
    session_id: str,
    name: str,
    connection: str,
    html: str,
    queries: dict[str, str],
) -> dict[str, Any]:
    """Create or update a dashboard and push it to a live QueryView session.

    Persists the dashboard (HTML + named SQL) by name, then pushes it to the
    browser identified by session_id (the id from the QueryView agent popover),
    which navigates to it and renders it. The dashboard's queries run against
    the named connection.

    The browser is the consumer of the results, not the agent: the HTML reads
    them from a `window.queries` global, a column-oriented map shaped
    `{query_name: {column_name: [values, …]}}` — e.g.
    `window.queries.sales.revenue` is the `revenue` column's values. Load chart
    libraries from a CDN inside the HTML if you need them.

    Args:
        session_id: The session id shown in the QueryView popover.
        name: Dashboard name (upsert key).
        connection: Saved connection name the queries run against.
        html: The dashboard HTML document (renders in a sandboxed iframe).
        queries: Map of query name to SQL.

    Returns {ok, persisted, pushed, message}.
    """
    persisted, pushed, message = await _upsert_and_push(
        name, connection, html, queries, session_id or None
    )
    return {
        "ok": persisted,
        "persisted": persisted,
        "pushed": pushed,
        "message": message,
    }
