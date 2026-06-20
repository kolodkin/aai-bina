"""ClickHouse driver: config round-trip, validation, registry conformance, and
that run_query builds the historical paginated SQL (no network needed)."""
from __future__ import annotations

import asyncio

from queryview.drivers import DRIVERS, Driver
from queryview.drivers.clickhouse import ChConfig, ClickHouseDriver


def test_registry_has_clickhouse_satisfying_protocol():
    d = DRIVERS["clickhouse"]
    assert isinstance(d, Driver)
    assert d.type == "clickhouse"


def test_parse_config_validates_host_and_port():
    d = ClickHouseDriver()
    cfg, err = d.parse_config({"host": "h", "port": "8123", "username": "u", "password": "p"})
    assert err is None and cfg == ChConfig("h", 8123, "u", "p")
    assert d.parse_config({"port": 8123})[0] is None       # missing host
    assert d.parse_config({"host": "h", "port": 0})[0] is None  # bad port


def test_config_dict_round_trip():
    d = ClickHouseDriver()
    cfg = ChConfig("h", 8123, "u", "p")
    assert d.config_from_dict(d.config_to_dict(cfg)) == cfg


def test_run_query_builds_clickhouse_sql(monkeypatch):
    d = ClickHouseDriver()
    seen = {}

    async def fake_ch_query(c, query, database=None, fmt=None):
        from queryview.drivers.clickhouse import ChResult
        seen["query"] = query
        seen["fmt"] = fmt
        seen["database"] = database
        return ChResult(True, "ok")

    monkeypatch.setattr("queryview.drivers.clickhouse.ch_query", fake_ch_query)
    r = asyncio.run(
        d.run_query(ChConfig("h", 1, "u", ""), "SELECT 1;", "db", 100, 0,
                    [{"name": "a", "dir": "DESC"}], "tsv")
    )
    assert r.ok and r.value == "ok"
    assert seen["query"] == "SELECT * FROM (\nSELECT 1\n) ORDER BY `a` DESC LIMIT 100 OFFSET 0"
    assert seen["fmt"] == "TabSeparatedWithNames"
    assert seen["database"] == "db"
