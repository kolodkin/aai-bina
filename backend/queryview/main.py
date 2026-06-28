"""FastAPI app: the JSON API under /api/*, the per-session cookie, and (when
SERVE_STATIC=1) serving the built SPA with an index.html fallback."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import remote
from .mcp_server import mcp

from .drivers import DRIVERS
from .connect import (
    _ensure_schema,
    connect_new,
    describe_query,
    disconnect,
    get_session,
    list_connection_names,
    open_saved,
    run_query,
    select_database,
)
from .dashboard_queries import run_queries_for_connection
from .dashboards import _upsert_and_push, get_dashboard, list_dashboards
from .queries import list_predefined_queries, save_predefined_query

SERVE_STATIC = os.environ.get("SERVE_STATIC") == "1"


def _static_root() -> Path:
    env = os.environ.get("STATIC_ROOT")
    if env:
        return Path(env).resolve()
    return (Path(__file__).resolve().parent.parent.parent / "frontend" / "dist").resolve()


async def _read_json(request: Request) -> Any:
    try:
        return await request.json()
    except Exception:
        return None


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _clean_queries(raw: Any) -> dict[str, str]:
    """Keep only string→string entries with a non-empty name and SQL; ignore
    anything else so a malformed `queries` map can't reach the runner."""
    if not isinstance(raw, dict):
        return {}
    return {
        k: v
        for k, v in raw.items()
        if isinstance(k, str) and k and isinstance(v, str) and v.strip()
    }


def _parse_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema to head before serving any request (single-process, no lock needed).
    await _ensure_schema()
    # A mounted sub-app's lifespan isn't run by the parent, so run the MCP session
    # manager here. streamable_http_app() (at mount, below) initializes
    # mcp.session_manager before this runs.
    async with mcp.session_manager.run():
        yield


app = FastAPI(title="queryview-backend", lifespan=lifespan)
app.mount("/mcp", mcp.streamable_http_app())


@app.middleware("http")
async def session_cookie(request: Request, call_next):
    sid = request.cookies.get("qv_session")
    new_session = sid is None
    if not sid:
        sid = str(uuid.uuid4())
    request.state.sid = sid
    response = await call_next(request)
    if new_session:
        response.set_cookie(
            "qv_session", sid, path="/", httponly=True, samesite="lax"
        )
    return response


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "queryview-backend"}


@app.get("/api/session")
async def session(request: Request) -> dict[str, Any]:
    return await get_session(request.state.sid)


# Drop this session's active connection (disconnect command).
@app.post("/api/db/disconnect")
async def db_disconnect(request: Request) -> dict[str, Any]:
    return await disconnect(request.state.sid)


# Saved connection names, for the `connect <name>` autocomplete.
@app.get("/api/db/connections")
async def db_connections() -> dict[str, Any]:
    return {"names": await list_connection_names()}


def _driver_and_config(body: Any):
    """Resolve (driver, config) from a request body's `type`. Returns
    (driver, config, None) or (None, None, message)."""
    b = body if isinstance(body, dict) else {}
    conn_type = b.get("type") if isinstance(b.get("type"), str) else ""
    driver = DRIVERS.get(conn_type)
    if driver is None:
        return None, None, f"unknown connection type: {conn_type or '(none)'}"
    config, error = driver.parse_config(b)
    if error:
        return None, None, error
    return driver, config, None


# Test only: a throwaway connectivity check, no save, no activation.
@app.post("/api/db/test")
async def db_test(request: Request):
    driver, config, error = _driver_and_config(await _read_json(request))
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    return await driver.test(config)


# Create + open a connection for this session.
@app.post("/api/db/connect")
async def db_connect(request: Request):
    body = await _read_json(request)
    driver, config, error = _driver_and_config(body)
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    b = body if isinstance(body, dict) else {}
    raw_name = b.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else driver.type
    return await connect_new(request.state.sid, name, config, driver.type)


# Open a saved connection by name for this session (connect <name>).
@app.post("/api/db/open")
async def db_open(request: Request):
    b = await _read_json(request) or {}
    raw_name = b.get("name") if isinstance(b, dict) else None
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name:
        return JSONResponse({"ok": False, "message": "name required"}, status_code=400)
    r = await open_saved(request.state.sid, name)
    if not r["ok"]:
        return JSONResponse(
            {"ok": False, "message": r["message"]},
            status_code=404 if r.get("not_found") else 200,
        )
    return {"ok": True, "name": r["name"], "type": r["type"], "databases": r["databases"]}


# Select this session's active connection's database.
@app.post("/api/db/database")
async def db_database(request: Request):
    b = await _read_json(request) or {}
    raw_db = b.get("database") if isinstance(b, dict) else None
    database = raw_db if isinstance(raw_db, str) else ""
    r = await select_database(request.state.sid, database)
    if not r["ok"]:
        return JSONResponse(
            {"ok": False, "message": r["message"]},
            status_code=409 if r["reason"] == "no-session" else 400,
        )
    return {"ok": True}


# Run a SQL query (paginated) against this session's selected database.
@app.post("/api/db/query")
async def db_query(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    raw_sql = b.get("query")
    sql = raw_sql.strip() if isinstance(raw_sql, str) else ""
    if not sql:
        return JSONResponse({"ok": False, "message": "query required"}, status_code=400)
    limit = _parse_int(b.get("limit"), 100)
    limit = 100 if limit < 1 else min(limit, 1000)
    offset = _parse_int(b.get("offset"), 0)
    offset = 0 if offset < 0 else offset
    fmt = "csv" if b.get("format") == "csv" else "tsv"
    raw_order = b.get("order_by")
    order_by = raw_order if isinstance(raw_order, list) else None
    r = await run_query(request.state.sid, sql, limit, offset, fmt, order_by)
    if not r["ok"]:
        status = 409 if r.get("reason") == "no-session" else 200
        return JSONResponse({"ok": False, "message": r["message"]}, status_code=status)
    return {"ok": True, "output": r["output"]}


# Describe a query's output columns (name + type) without scanning data.
@app.post("/api/db/describe")
async def db_describe(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    raw_sql = b.get("query")
    sql = raw_sql.strip() if isinstance(raw_sql, str) else ""
    if not sql:
        return JSONResponse({"ok": False, "message": "query required"}, status_code=400)
    r = await describe_query(request.state.sid, sql)
    if not r["ok"]:
        status = 409 if r.get("reason") in ("no-session", "no-database") else 200
        return JSONResponse({"ok": False, "message": r["message"]}, status_code=status)
    return {"ok": True, "fields": r["fields"]}


# Predefined queries: global, keyed by connection type.
@app.get("/api/predefined-queries")
async def predefined_queries_list(request: Request):
    conn_type = request.query_params.get("type") or "clickhouse"
    return {"queries": await list_predefined_queries(conn_type)}


@app.post("/api/predefined-queries")
async def predefined_queries_save(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    name = b.get("query_name")
    conn_type = b.get("type")
    query = b.get("query")
    name = name.strip() if isinstance(name, str) else ""
    conn_type = conn_type.strip() if isinstance(conn_type, str) else ""
    query = query.strip() if isinstance(query, str) else ""
    if not name or not conn_type or not query:
        return JSONResponse(
            {"ok": False, "message": "query_name, type and query are required"},
            status_code=400,
        )
    raw_cv = b.get("cell_view")
    # Store the raw YAML text verbatim; empty string => NULL (no custom views).
    cell_view = raw_cv if isinstance(raw_cv, str) and raw_cv.strip() else None
    await save_predefined_query(name, conn_type, query, cell_view)
    return {"ok": True}


# --- Remote control (MCP push -> live browser session) --------------------

_SSE_POLL_SECONDS = 1.0
_SSE_HEARTBEAT_SECONDS = 15.0


def _sse(event: str, data: dict[str, Any]) -> bytes:
    import json

    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


async def _event_stream(remote_id: str, request: Request):
    """Yield SSE: a `ready` event with the id, then pushed payloads (each under the
    SSE event named by its `type` field) plus a heartbeat. Polls disconnect every
    second so disarming (the browser closing the EventSource) unregisters the
    channel promptly."""
    try:
        yield _sse("ready", {"id": remote_id})
        elapsed = 0.0
        while True:
            if await request.is_disconnected():
                break
            msg = await remote.next_message(remote_id, _SSE_POLL_SECONDS)
            if msg is None:
                elapsed += _SSE_POLL_SECONDS
                if elapsed >= _SSE_HEARTBEAT_SECONDS:
                    elapsed = 0.0
                    yield b": ping\n\n"
                continue
            yield _sse(msg.get("type", "query"), msg)
    finally:
        remote.unregister(remote_id)


# Open an SSE channel for this browser; the browser does this when the user
# arms "remote control". Closing the EventSource unregisters the channel.
@app.get("/api/remote/events")
async def remote_events(request: Request):
    remote_id = remote.register()
    return StreamingResponse(
        _event_stream(remote_id, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# Push a query to a live session (used by the MCP tool and, in tests, directly).
@app.post("/api/remote/push")
async def remote_push(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    raw_sid = b.get("session_id")
    session_id = raw_sid.strip() if isinstance(raw_sid, str) else ""
    raw_sql = b.get("query")
    query = raw_sql.strip() if isinstance(raw_sql, str) else ""
    if not session_id or not query:
        return JSONResponse(
            {"ok": False, "message": "session_id and query are required"},
            status_code=400,
        )
    limit = _parse_int(b.get("limit"), 100)
    offset = _parse_int(b.get("offset"), 0)
    raw_order = b.get("order_by")
    order_by = raw_order if isinstance(raw_order, list) else None
    raw_fields = b.get("fields")
    fields = (
        [f for f in raw_fields if isinstance(f, str)]
        if isinstance(raw_fields, list)
        else None
    )
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


# --- Dashboards (persist + reopen + run-against-a-named-connection) --------


# Run a dashboard's named queries against a named connection. Fail-fast: any
# failure returns an HTTP error and no partial results.
@app.post("/api/runqueries")
async def run_queries(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    connection = _clean_str(b.get("connection"))
    queries = _clean_queries(b.get("queries"))
    if not connection or not queries:
        return JSONResponse(
            {"ok": False, "message": "connection and queries are required"},
            status_code=400,
        )
    r = await run_queries_for_connection(connection, queries)
    if not r["ok"]:
        status = 404 if r.get("reason") == "no-connection" else 400
        return JSONResponse({"ok": False, "message": r["message"]}, status_code=status)
    return {"ok": True, "results": r["results"]}


# Upsert a dashboard and (with a session_id) push it to a live browser session.
# REST mirror of the upsert_dashboard MCP tool.
@app.post("/api/dashboards")
async def dashboards_upsert(request: Request):
    body = await _read_json(request)
    b = body if isinstance(body, dict) else {}
    name = _clean_str(b.get("name"))
    connection = _clean_str(b.get("connection"))
    html = b.get("html") if isinstance(b.get("html"), str) else ""
    queries = _clean_queries(b.get("queries"))
    if not name or not connection or not html.strip():
        return JSONResponse(
            {"ok": False, "message": "name, connection and html are required"},
            status_code=400,
        )
    session_id = _clean_str(b.get("session_id"))
    persisted, pushed, message = await _upsert_and_push(
        name, connection, html, queries, session_id or None
    )
    return {"ok": persisted, "persisted": persisted, "pushed": pushed, "message": message}


@app.get("/api/dashboards")
async def dashboards_list():
    return {"dashboards": await list_dashboards()}


@app.get("/api/dashboards/{name}")
async def dashboards_get(name: str):
    d = await get_dashboard(name)
    if d is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return d


@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api_not_found(rest: str):
    return JSONResponse({"error": "not found"}, status_code=404)


if SERVE_STATIC:

    @app.get("/{full_path:path}")
    async def spa(full_path: str):
        root = _static_root()
        candidate = (root / full_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            candidate = None
        if candidate is not None and candidate.is_file():
            return FileResponse(candidate)
        # SPA fallback: serve index.html (200) for any unknown path so
        # client-side routing works.
        return FileResponse(root / "index.html")

else:

    @app.get("/{full_path:path}")
    async def not_found(full_path: str):
        return JSONResponse({"error": "not found"}, status_code=404)


def run() -> None:
    """Console-script entry point: launch uvicorn honoring PORT (default 8000)."""
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
