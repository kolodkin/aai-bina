"""Flow dispatch: connect_new persists with the given type; run_query routes to
the driver; the database gate is skipped when the driver exposes no databases."""
from __future__ import annotations

import asyncio

import queryview.connect as connect
from queryview.drivers import DRIVERS
from queryview.drivers.base import QueryResult


def _run(coro):
    return asyncio.run(coro)


class _FakeDriver:
    type = "fake"
    requires_database = False  # no picker

    def parse_config(self, body):
        return {"v": 1}, None

    def config_to_dict(self, c):
        return c

    def config_from_dict(self, d):
        return d

    async def test(self, c):
        return {"ok": True, "message": "ok"}

    async def list_databases(self, c):
        return True, []  # no picker

    async def run_query(self, c, sql, database, limit, offset, order_by, fmt):
        return QueryResult(True, f"ran:{sql}:db={database}")

    async def describe_query(self, c, sql, database):
        return True, [{"name": "x", "type": "int"}]


def test_run_query_skips_db_gate_when_no_databases(monkeypatch):
    monkeypatch.setitem(DRIVERS, "fake", _FakeDriver())
    sid = "s-fake"
    _run(connect.connect_new(sid, "f", {"v": 1}, "fake"))
    out = _run(connect.run_query(sid, "SELECT 1", 10, 0, "tsv", None))
    assert out["ok"] and out["output"] == "ran:SELECT 1:db=None"


def test_run_query_requires_database_when_picker_present(monkeypatch):
    class _WithDbs(_FakeDriver):
        type = "fakedb"
        requires_database = True

        async def list_databases(self, c):
            return True, ["a", "b"]

    monkeypatch.setitem(DRIVERS, "fakedb", _WithDbs())
    sid = "s-fakedb"
    _run(connect.connect_new(sid, "g", {"v": 1}, "fakedb"))
    out = _run(connect.run_query(sid, "SELECT 1", 10, 0, "tsv", None))
    assert out["ok"] is False and out["reason"] == "no-database"
