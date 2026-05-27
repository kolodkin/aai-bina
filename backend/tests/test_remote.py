import asyncio

from queryview import remote


def test_register_returns_distinct_ids():
    a = remote.register()
    b = remote.register()
    assert a and b and a != b
    remote.unregister(a)
    remote.unregister(b)


def test_push_to_registered_session_delivers():
    rid = remote.register()
    try:
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True
        msg_in = asyncio.run(remote.next_message(rid, 1.0))
        assert msg_in == {"type": "query", "query": "SELECT 1"}
    finally:
        remote.unregister(rid)


def test_push_to_unknown_session_fails():
    ok, msg = remote.push("deadbeef", {"type": "query", "query": "SELECT 1"})
    assert ok is False
    assert "unknown" in msg.lower()


def test_unregister_makes_push_fail():
    rid = remote.register()
    remote.unregister(rid)
    ok, _ = remote.push(rid, {"type": "query", "query": "SELECT 1"})
    assert ok is False


def test_next_message_times_out_to_none():
    rid = remote.register()
    try:
        assert asyncio.run(remote.next_message(rid, 0.05)) is None
    finally:
        remote.unregister(rid)


from fastapi.testclient import TestClient

from queryview.main import app


def test_push_endpoint_requires_query():
    client = TestClient(app)
    r = client.post("/api/remote/push", json={"session_id": "x", "query": ""})
    assert r.status_code == 400


def test_push_endpoint_unknown_session_returns_not_delivered():
    client = TestClient(app)
    r = client.post(
        "/api/remote/push",
        json={"session_id": "deadbeef", "query": "SELECT 1"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_push_endpoint_delivers_to_registered_session():
    import asyncio
    from queryview import remote

    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/remote/push",
            json={
                "session_id": rid,
                "query": "SELECT id, name FROM items",
                "limit": 5,
                "order_by": [{"name": "id", "dir": "DESC"}],
                "fields": ["name"],
            },
        )
        assert r.json()["ok"] is True
        msg = asyncio.run(remote.next_message(rid, 1.0))
        assert msg["type"] == "query"
        assert msg["query"] == "SELECT id, name FROM items"
        assert msg["limit"] == 5
        assert msg["order_by"] == [{"name": "id", "dir": "DESC"}]
        assert msg["fields"] == ["name"]
    finally:
        remote.unregister(rid)
