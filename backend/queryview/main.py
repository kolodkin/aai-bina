"""FastAPI app: the JSON API under /api/*, the per-session cookie, and (when
SERVE_STATIC=1) serving the built SPA with an index.html fallback."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from .clickhouse import parse_ch_config, test_connection
from .connect import connect_new, get_session, open_saved, run_query, select_database
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


app = FastAPI(title="queryview-backend")


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


# Test only: a throwaway connectivity check, no save, no activation.
@app.post("/api/clickhouse/test")
async def clickhouse_test(request: Request):
    config, error = parse_ch_config(await _read_json(request))
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    return await test_connection(config)


# Create + open a connection for this session.
@app.post("/api/clickhouse/connect")
async def clickhouse_connect(request: Request):
    body = await _read_json(request)
    config, error = parse_ch_config(body)
    if error:
        return JSONResponse({"ok": False, "message": error}, status_code=400)
    b = body if isinstance(body, dict) else {}
    raw_name = b.get("name")
    name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "clickhouse"
    return await connect_new(request.state.sid, name, config)


# Open a saved connection by name for this session (connect <name>).
@app.post("/api/clickhouse/open")
async def clickhouse_open(request: Request):
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
@app.post("/api/clickhouse/database")
async def clickhouse_database(request: Request):
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
@app.post("/api/clickhouse/query")
async def clickhouse_query(request: Request):
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
    fmt = "CSVWithNames" if b.get("format") == "csv" else "TabSeparatedWithNames"
    r = await run_query(request.state.sid, sql, limit, offset, fmt)
    if not r["ok"]:
        status = 409 if r.get("reason") == "no-session" else 200
        return JSONResponse({"ok": False, "message": r["message"]}, status_code=status)
    return {"ok": True, "output": r["output"]}


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
    await save_predefined_query(name, conn_type, query)
    return {"ok": True}


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
        # SPA fallback: serve index.html for any unknown path so client-side
        # routing works. The browser still gets a 200 with the SPA shell.
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
