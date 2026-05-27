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
