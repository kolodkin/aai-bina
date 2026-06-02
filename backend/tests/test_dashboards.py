"""Tests for the dashboard store and its REST surface. These don't need a real
ClickHouse: the store is pure SQLite, and route tests exercise validation plus
the persist/push path (the runner's happy/bad ClickHouse paths live in e2e)."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from queryview import remote
from queryview.dashboards import (
    _upsert_and_push,
    get_dashboard,
    list_dashboards,
    upsert_dashboard,
)
from queryview.main import app


def _run(coro):
    return asyncio.run(coro)


def test_upsert_creates_then_updates_by_name():
    _run(upsert_dashboard("d1", "conn-a", "<h1>v1</h1>", {"q": "SELECT 1"}))
    got = _run(get_dashboard("d1"))
    assert got["connection"] == "conn-a"
    assert got["html"] == "<h1>v1</h1>"
    assert got["queries"] == {"q": "SELECT 1"}

    _run(upsert_dashboard("d1", "conn-b", "<h1>v2</h1>", {"q": "SELECT 2"}))
    got = _run(get_dashboard("d1"))
    assert got["connection"] == "conn-b"
    assert got["html"] == "<h1>v2</h1>"
    assert got["queries"] == {"q": "SELECT 2"}


def test_get_dashboard_round_trips_queries_dict():
    queries = {"sales": "SELECT * FROM sales", "users": "SELECT count() FROM users"}
    _run(upsert_dashboard("multi", "c", "<div></div>", queries))
    assert _run(get_dashboard("multi"))["queries"] == queries


def test_get_missing_dashboard_is_none():
    assert _run(get_dashboard("does-not-exist")) is None


def test_list_dashboards_orders_by_name_and_omits_payload():
    _run(upsert_dashboard("zeta", "c", "<i></i>", {"q": "SELECT 1"}))
    _run(upsert_dashboard("alpha", "c", "<i></i>", {"q": "SELECT 1"}))
    names = [d["name"] for d in _run(list_dashboards())]
    assert names.index("alpha") < names.index("zeta")
    row = next(d for d in _run(list_dashboards()) if d["name"] == "alpha")
    assert set(row) == {"name", "connection", "updated_at"}


def test_upsert_and_push_persists_without_session():
    persisted, pushed, _ = _run(
        _upsert_and_push("np", "c", "<p></p>", {"q": "SELECT 1"}, None)
    )
    assert persisted is True and pushed is False
    assert _run(get_dashboard("np")) is not None


def test_upsert_and_push_delivers_to_registered_session():
    rid = remote.register()
    try:
        persisted, pushed, _ = _run(
            _upsert_and_push("pushed", "c", "<p>x</p>", {"q": "SELECT 1"}, rid)
        )
        assert persisted is True and pushed is True
        msg = _run(remote.next_message(rid, 1.0))
        assert msg["type"] == "dashboard"
        assert msg["name"] == "pushed"
        assert msg["connection"] == "c"
        assert msg["html"] == "<p>x</p>"
        assert msg["queries"] == {"q": "SELECT 1"}
    finally:
        remote.unregister(rid)


# --- REST surface ---------------------------------------------------------


def test_runqueries_requires_connection_and_queries():
    client = TestClient(app)
    assert client.post("/api/runqueries", json={"queries": {"q": "SELECT 1"}}).status_code == 400
    assert client.post("/api/runqueries", json={"connection": "c"}).status_code == 400
    assert (
        client.post("/api/runqueries", json={"connection": "c", "queries": {}}).status_code
        == 400
    )


def test_runqueries_unknown_connection_returns_404():
    client = TestClient(app)
    r = client.post(
        "/api/runqueries",
        json={"connection": "no-such-connection", "queries": {"q": "SELECT 1"}},
    )
    assert r.status_code == 404
    assert r.json()["ok"] is False
    assert "no connection" in r.json()["message"].lower()


def test_dashboards_upsert_requires_fields():
    client = TestClient(app)
    r = client.post("/api/dashboards", json={"name": "x", "connection": "c"})
    assert r.status_code == 400


def test_dashboards_upsert_persists_and_lists_and_gets():
    client = TestClient(app)
    r = client.post(
        "/api/dashboards",
        json={
            "name": "rest-dash",
            "connection": "c",
            "html": "<h1>hi</h1>",
            "queries": {"q": "SELECT 1"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["persisted"] is True and body["pushed"] is False

    listed = client.get("/api/dashboards").json()["dashboards"]
    assert any(d["name"] == "rest-dash" for d in listed)

    got = client.get("/api/dashboards/rest-dash").json()
    assert got["html"] == "<h1>hi</h1>"
    assert got["queries"] == {"q": "SELECT 1"}


def test_dashboards_get_missing_returns_404():
    client = TestClient(app)
    assert client.get("/api/dashboards/missing-one").status_code == 404


def test_dashboards_upsert_pushes_to_registered_session():
    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/dashboards",
            json={
                "session_id": rid,
                "name": "live-dash",
                "connection": "c",
                "html": "<p></p>",
                "queries": {"q": "SELECT 1"},
            },
        )
        assert r.json()["pushed"] is True
        msg = _run(remote.next_message(rid, 1.0))
        assert msg["type"] == "dashboard" and msg["name"] == "live-dash"
    finally:
        remote.unregister(rid)
