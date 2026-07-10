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


def test_push_endpoint_forwards_cell_view():
    import asyncio
    from queryview import remote

    rid = remote.register()
    try:
        client = TestClient(app)
        yaml = "source:\n  type: custom\n  value: <span>{cell}</span>\n"
        r = client.post(
            "/api/remote/push",
            json={"session_id": rid, "query": "SELECT 1", "cell_view": yaml},
        )
        assert r.json()["ok"] is True
        msg = asyncio.run(remote.next_message(rid, 1.0))
        assert msg["cell_view"] == yaml
    finally:
        remote.unregister(rid)


import time as _time
from queryview import remote as _remote


def test_lock_endpoint_acquire_blocks_push():
    from queryview import remote
    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post("/api/remote/lock", json={"session_id": rid, "action": "acquire"})
        assert r.json()["ok"] is True
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
        r = client.post("/api/remote/lock", json={"session_id": rid, "action": "release"})
        assert r.json()["ok"] is True
        ok, _ = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True
    finally:
        remote.unregister(rid)


def test_lock_endpoint_bad_action_400():
    from queryview import remote
    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post("/api/remote/lock", json={"session_id": rid, "action": "nope"})
        assert r.status_code == 400
    finally:
        remote.unregister(rid)


def test_acquire_blocks_push_then_release_allows():
    rid = _remote.register()
    try:
        ok, _ = _remote.acquire(rid, "human")
        assert ok is True
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
        _remote.release(rid, "human")
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True and msg == "delivered"
    finally:
        _remote.unregister(rid)


def test_lock_ttl_expiry_allows_push():
    rid = _remote.register()
    try:
        _remote.acquire(rid, "human")
        # Simulate the heartbeat lapsing: age the lock past its TTL.
        _remote._channels[rid].lock_touched = _time.monotonic() - (_remote.LOCK_TTL_SECONDS + 1)
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True and msg == "delivered"
    finally:
        _remote.unregister(rid)


def test_push_rejects_invalid_order_by():
    rid = _remote.register()
    try:
        ok, msg = _remote.push(
            rid, {"type": "query", "query": "SELECT 1", "order_by": [{"name": "id", "dir": "X"}]}
        )
        assert ok is False and "invalid order_by" in msg
    finally:
        _remote.unregister(rid)


def test_release_by_nonowner_is_noop():
    rid = _remote.register()
    try:
        _remote.acquire(rid, "human")
        _remote.release(rid, "agent")  # wrong owner: must not clear
        ok, msg = _remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
    finally:
        _remote.unregister(rid)


def test_push_endpoint_blank_cell_view_is_none():
    import asyncio
    from queryview import remote

    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/remote/push",
            json={"session_id": rid, "query": "SELECT 1", "cell_view": "   "},
        )
        assert r.json()["ok"] is True
        msg = asyncio.run(remote.next_message(rid, 1.0))
        assert msg["cell_view"] is None
    finally:
        remote.unregister(rid)


def test_session_database_set_and_read():
    rid = _remote.register()
    try:
        assert _remote.session_database(rid) is None
        assert _remote.set_session_database(rid, "acme_db") is True
        assert _remote.session_database(rid) == "acme_db"
    finally:
        _remote.unregister(rid)


def test_set_session_database_unknown_session():
    assert _remote.set_session_database("deadbeef", "x") is False


def test_remote_db_endpoint_sets_channel_database():
    from queryview import remote
    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post("/api/remote/db", json={"session_id": rid, "database": "d1"})
        assert r.json()["ok"] is True
        assert remote.session_database(rid) == "d1"
    finally:
        remote.unregister(rid)


def test_push_query_return_includes_database():
    import asyncio
    from queryview.mcp_server import push_query as mcp_push_query

    rid = _remote.register()
    try:
        _remote.set_session_database(rid, "acme_db")
        out = asyncio.run(mcp_push_query(rid, "SELECT 1"))
        assert out["ok"] is True and out["database"] == "acme_db"
    finally:
        _remote.unregister(rid)


def test_session_workspace_report_and_read():
    from queryview import remote

    rid = remote.register()
    try:
        assert remote.session_workspace(rid) is None
        assert remote.set_session_workspace(rid, "team-a") is True
        assert remote.session_workspace(rid) == "team-a"
        assert remote.set_session_workspace("nope", "x") is False
        assert remote.session_workspace("nope") is None
    finally:
        remote.unregister(rid)
