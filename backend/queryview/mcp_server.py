"""The MCP layer: a FastMCP server (mounted by main.py at /mcp) exposing a
single tool that pushes a query to a live QueryView browser session. Tools
delegate to the in-process remote.py hub."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from . import remote

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
