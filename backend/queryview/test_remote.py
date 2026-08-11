import asyncio
import time

from fastapi.testclient import TestClient

from queryview import remote
from queryview.main import app


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
        assert msg is not None
        assert msg["type"] == "query"
        assert msg["query"] == "SELECT id, name FROM items"
        assert msg["limit"] == 5
        assert msg["order_by"] == [{"name": "id", "dir": "DESC"}]
        assert msg["fields"] == ["name"]
    finally:
        remote.unregister(rid)


def test_push_endpoint_forwards_cell_view():

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
        assert msg is not None
        assert msg["cell_view"] == yaml
    finally:
        remote.unregister(rid)


def test_lock_endpoint_acquire_blocks_push():

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

    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post("/api/remote/lock", json={"session_id": rid, "action": "nope"})
        assert r.status_code == 400
    finally:
        remote.unregister(rid)


def test_acquire_blocks_push_then_release_allows():
    rid = remote.register()
    try:
        ok, _ = remote.acquire(rid, "human")
        assert ok is True
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
        remote.release(rid, "human")
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True and msg == "delivered"
    finally:
        remote.unregister(rid)


def test_lock_ttl_expiry_allows_push():
    rid = remote.register()
    try:
        remote.acquire(rid, "human")
        # Simulate the heartbeat lapsing: age the lock past its TTL.
        remote._channels[rid].lock_touched = time.monotonic() - (remote.LOCK_TTL_SECONDS + 1)
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is True and msg == "delivered"
    finally:
        remote.unregister(rid)


def test_push_rejects_invalid_order_by():
    rid = remote.register()
    try:
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1", "order_by": [{"name": "id", "dir": "X"}]})
        assert ok is False and "invalid order_by" in msg
    finally:
        remote.unregister(rid)


def test_push_rejects_malformed_cell_view():
    rid = remote.register()
    try:
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1", "cell_view": "col: [unclosed"})
        assert ok is False and "invalid cell_view" in msg
    finally:
        remote.unregister(rid)


def test_release_by_nonowner_is_noop():
    rid = remote.register()
    try:
        remote.acquire(rid, "human")
        remote.release(rid, "agent")  # wrong owner: must not clear
        ok, msg = remote.push(rid, {"type": "query", "query": "SELECT 1"})
        assert ok is False and msg == "blocked, user editing"
    finally:
        remote.unregister(rid)


def test_push_endpoint_blank_cell_view_is_none():

    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/remote/push",
            json={"session_id": rid, "query": "SELECT 1", "cell_view": "   "},
        )
        assert r.json()["ok"] is True
        msg = asyncio.run(remote.next_message(rid, 1.0))
        assert msg is not None
        assert msg["cell_view"] is None
    finally:
        remote.unregister(rid)


def test_session_database_set_and_read():
    rid = remote.register()
    try:
        assert remote.session_database(rid) is None
        assert remote.set_session_database(rid, "acme_db") is True
        assert remote.session_database(rid) == "acme_db"
    finally:
        remote.unregister(rid)


def test_set_session_database_unknown_session():
    assert remote.set_session_database("deadbeef", "x") is False


def test_remote_db_endpoint_sets_channel_database():

    rid = remote.register()
    try:
        client = TestClient(app)
        r = client.post("/api/remote/db", json={"session_id": rid, "database": "d1"})
        assert r.json()["ok"] is True
        assert remote.session_database(rid) == "d1"
    finally:
        remote.unregister(rid)


def test_push_query_return_includes_database():

    from queryview.mcp_server import push_query as mcp_push_query

    rid = remote.register()
    try:
        remote.set_session_database(rid, "acme_db")
        out = asyncio.run(mcp_push_query(rid, "SELECT 1"))
        assert out["ok"] is True and out["database"] == "acme_db"
    finally:
        remote.unregister(rid)


def test_session_workspace_report_and_read():

    rid = remote.register()
    try:
        assert remote.session_workspace(rid) is None
        assert remote.set_session_workspace(rid, "team-a") is True
        assert remote.session_workspace(rid) == "team-a"
        assert remote.set_session_workspace("nope", "x") is False
        assert remote.session_workspace("nope") is None
    finally:
        remote.unregister(rid)
