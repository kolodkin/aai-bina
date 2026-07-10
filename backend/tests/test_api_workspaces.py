"""The /api/workspaces surface: CRUD, no-secret listing, delete-non-empty 409."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from queryview.main import app


def _run(coro):
    return asyncio.run(coro)


def test_list_includes_default():
    c = TestClient(app)
    names = [w["name"] for w in c.get("/api/workspaces").json()["workspaces"]]
    assert "default" in names


def test_create_list_update_delete_round_trip():
    c = TestClient(app)
    r = c.post(
        "/api/workspaces",
        json={"name": "t6-rt", "remote": "https://u:tok@example.test/r.git", "branch": "dev"},
    )
    assert r.json() == {"ok": True}

    listed = {w["name"]: w for w in c.get("/api/workspaces").json()["workspaces"]}
    assert listed["t6-rt"] == {"name": "t6-rt", "branch": "dev", "configured": True}
    assert "tok" not in r.text and "remote" not in listed["t6-rt"]

    # Rename + clear the remote (present-but-null clears; absent keeps).
    assert c.patch("/api/workspaces/t6-rt", json={"name": "t6-rt2", "remote": None}).json() == {"ok": True}
    listed = {w["name"]: w for w in c.get("/api/workspaces").json()["workspaces"]}
    assert "t6-rt" not in listed
    assert listed["t6-rt2"]["configured"] is False

    assert c.delete("/api/workspaces/t6-rt2").json() == {"ok": True}
    assert c.delete("/api/workspaces/t6-rt2").status_code == 404


def test_create_validation_and_conflict():
    c = TestClient(app)
    assert c.post("/api/workspaces", json={}).status_code == 400
    c.post("/api/workspaces", json={"name": "t6-dup"})
    assert c.post("/api/workspaces", json={"name": "t6-dup"}).status_code == 409


def test_delete_non_empty_409(default_ws_id):
    from queryview.queries import save_predefined_query
    from queryview.workspaces import resolve

    c = TestClient(app)
    c.post("/api/workspaces", json={"name": "t6-full"})
    wid = _run(resolve("t6-full")).id
    _run(save_predefined_query("holder", "clickhouse", "SELECT 1", workspace_id=wid))
    r = c.delete("/api/workspaces/t6-full")
    assert r.status_code == 409
    assert "contains" in r.json()["message"]
