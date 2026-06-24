"""ClickHouse-specific behavior: run_query builds the historical paginated SQL
(backtick-quoted, no subquery alias, FORMAT clause). Registry conformance,
config round-trip, and validation are covered by test_driver_contract."""
from __future__ import annotations

import asyncio

from queryview.drivers.clickhouse import ChConfig, ClickHouseDriver


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
