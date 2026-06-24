"""Postgres-specific behavior: run_query builds double-quoted, `_qv`-aliased
paginated SQL (asyncpg is monkeypatched — no server). Registry conformance,
config round-trip, and validation are covered by test_driver_contract; live
connect/query/describe by e2e."""
from __future__ import annotations

import asyncio

from queryview.drivers.postgres import PgConfig, PostgresDriver


def test_run_query_builds_aliased_double_quoted_sql(monkeypatch):
    d = PostgresDriver()
    captured = {}

    class _Stmt:
        def get_attributes(self):
            class _A:
                name = "name"

                class type:  # noqa: N801
                    name = "text"

            return (_A(),)

        async def fetch(self):
            return [["alpha"]]

    class _Conn:
        async def prepare(self, sql):
            captured["sql"] = sql
            return _Stmt()

        async def close(self):
            pass

    async def fake_connect(c, database):
        captured["database"] = database
        return _Conn()

    monkeypatch.setattr("queryview.drivers.postgres._raw_connect", fake_connect)
    r = asyncio.run(
        d.run_query(PgConfig("h", 5432, "u", ""), "SELECT name FROM t;", "mydb",
                    50, 10, [{"name": "name", "dir": "ASC"}], "tsv")
    )
    assert r.ok and r.value == "name\nalpha"
    assert captured["database"] == "mydb"
    assert captured["sql"] == (
        'SELECT * FROM (\nSELECT name FROM t\n) AS _qv ORDER BY "name" ASC LIMIT 50 OFFSET 10'
    )
