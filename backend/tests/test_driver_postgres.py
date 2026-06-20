"""Postgres driver: validation, config round-trip, registry conformance, and
the paginated-SQL shape (asyncpg calls are monkeypatched — no server needed).
Live connect/query/describe are covered by e2e."""
from __future__ import annotations

import asyncio

from queryview.drivers import DRIVERS, Driver
from queryview.drivers.postgres import PgConfig, PostgresDriver


def test_registry_has_postgres_satisfying_protocol():
    d = DRIVERS["postgres"]
    assert isinstance(d, Driver)
    assert d.type == "postgres" and d.requires_database is True


def test_parse_config_validates_host_and_port():
    d = PostgresDriver()
    cfg, err = d.parse_config({"host": "h", "port": "5432", "username": "u", "password": "p"})
    assert err is None and cfg == PgConfig("h", 5432, "u", "p")
    assert d.parse_config({"port": 5432})[0] is None
    assert d.parse_config({"host": "h", "port": 99999})[0] is None


def test_config_dict_round_trip():
    d = PostgresDriver()
    cfg = PgConfig("h", 5432, "u", "p")
    assert d.config_from_dict(d.config_to_dict(cfg)) == cfg


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
