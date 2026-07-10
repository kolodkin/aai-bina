"""The /api/git surface: validation 400s, unconfigured 409, and a store ->
history -> restore round trip against a local bare repo."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from queryview.main import app

# The git_env fixture (bare repo + GIT_SYNC_* env vars) lives in conftest.py.


def _run(coro):
    return asyncio.run(coro)


def _default_ws_id() -> int:
    from queryview.workspaces import DEFAULT_WORKSPACE, resolve

    return _run(resolve(DEFAULT_WORKSPACE)).id


def test_status_reports_unconfigured():
    # Outside git_env the default workspace has no remote configured.
    c = TestClient(app)
    assert c.get("/api/git/status").json() == {"configured": False}


def test_status_reports_configured(git_env):
    c = TestClient(app)
    assert c.get("/api/git/status").json() == {"configured": True}


def test_store_validation_400():
    c = TestClient(app)
    assert c.post("/api/git/store", json={}).status_code == 400
    assert c.post("/api/git/store", json={"kind": "nope", "name": "x"}).status_code == 400
    # queries require conn_type
    assert c.post("/api/git/store", json={"kind": "query", "name": "x"}).status_code == 400


def test_store_unconfigured_409():
    c = TestClient(app)
    r = c.post(
        "/api/git/store",
        json={"kind": "query", "name": "x", "conn_type": "clickhouse"},
    )
    assert r.status_code == 409
    assert "no git remote" in r.json()["message"]


def test_store_history_restore_round_trip(git_env):
    from queryview.queries import list_predefined_queries, save_predefined_query

    c = TestClient(app)
    _run(save_predefined_query("api rt", "clickhouse", "SELECT 1", workspace_id=_default_ws_id()))
    r1 = c.post(
        "/api/git/store",
        json={"kind": "query", "name": "api rt", "conn_type": "clickhouse"},
    ).json()
    assert r1["ok"] is True and r1["committed"] is True
    _run(save_predefined_query("api rt", "clickhouse", "SELECT 2", workspace_id=_default_ws_id()))
    c.post(
        "/api/git/store",
        json={"kind": "query", "name": "api rt", "conn_type": "clickhouse"},
    )

    h = c.get(
        "/api/git/history",
        params={"kind": "query", "name": "api rt", "conn_type": "clickhouse"},
    ).json()
    assert h["ok"] is True and len(h["revisions"]) == 2 and h["has_more"] is False
    oldest = h["revisions"][-1]["sha"]

    r2 = c.post(
        "/api/git/restore",
        json={"kind": "query", "name": "api rt", "conn_type": "clickhouse", "ref": oldest},
    ).json()
    assert r2["ok"] is True
    rows = _run(list_predefined_queries("clickhouse", _default_ws_id()))
    row = next(x for x in rows if x["query_name"] == "api rt")
    assert row["query"] == "SELECT 1"


def test_restore_unknown_entity_404(git_env):
    c = TestClient(app)
    r = c.post(
        "/api/git/restore",
        json={"kind": "query", "name": "never stored api", "conn_type": "clickhouse"},
    )
    assert r.status_code == 404


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
